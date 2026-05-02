"""Admin panel handlers — доступно только администраторам из ADMIN_USERNAMES."""

from __future__ import annotations

import logging
import math
from typing import Union

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.core.database import async_session
from client_bot.core.formatting import e
from client_bot.services.notification_settings import (
    ADMIN_SCOPE_CLIENT,
    get_or_create_admin_settings,
)
from client_bot.texts import TYPE_RU, ORDER_STATUS_RU, Btn, Client
from client_bot.ui.keyboards import (
    ADMIN_PAGE_SIZE,
    adm_notif_settings_kb,
    admin_filter_kb,
    admin_main_kb,
    admin_order_detail_kb,
    admin_orders_kb,
    admin_partner_detail_kb,
    admin_service_detail_kb,
    main_menu_kb,
)
from client_bot.domain.models import (
    Brand,
    MetroStation,
    Model,
    Order,
    Service,
    ServiceDraft,
    ServiceCategory,
    User,
)

logger = logging.getLogger(__name__)
router = Router(name="admin")

_ADM_NOTIF_TOGGLE_TO_ATTR = {
    "enabled": "notif_enabled",
    "dispute": "notif_client_dispute",
    "client_cancel": "notif_client_cancel",
    "no_center": "notif_no_center",
    "completed": "notif_order_completed",
}


# ── Filter ────────────────────────────────────────────────────


class IsAdmin(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        uname = event.from_user.username if event.from_user else None
        return bool(uname) and uname.lower() in ADMIN_USERNAMES


# Применяем фильтр ко всем хэндлерам роутера сразу
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ── FSM ───────────────────────────────────────────────────────


class AdminFSM(StatesGroup):
    orders_list = State()
    order_detail = State()


# ── Статусы — человеческие названия ──────────────────────────

_STATUS_RU = ORDER_STATUS_RU


# ── Helpers ───────────────────────────────────────────────────


def _display_order_code(order: Order) -> str:
    code = (order.order_code or "").strip()
    return code or f"{order.id:06d}"


def _fmt_order(order: Order) -> str:
    """Format full order card in HTML."""
    _TYPE_RU = TYPE_RU
    # Модель
    if order.brand_custom_name:
        model_str = e(order.brand_custom_name)
        if order.model_custom_name:
            model_str += f" / {e(order.model_custom_name)}"
        model_str += " <i>(вручную)</i>"
    elif order.model_custom_name:
        brand_name = order.model.brand.name if order.model else "—"
        model_str = f"{e(brand_name)} / {e(order.model_custom_name)} <i>(вручную)</i>"
    elif order.model:
        model_str = f"{e(order.model.brand.name)} {e(order.model.name)}"
    else:
        model_str = "—"

    # Услуга — скрываем до оплаты
    svc = order.service
    if svc is None:
        svc_str = "—"
    elif order.status == "awaiting_payment":
        svc_str = f"<i>{e(svc.name)}</i> (скрыт от клиента)"
    else:
        svc_str = e(svc.name)
    if svc and svc.category_rel:
        svc_str += f" ({e(svc.category_rel.name)})"

    # Пользователь
    u = order.user
    tg_link = (
        f'<a href="https://t.me/{u.username}">@{u.username}</a>'
        if u.username
        else e(u.full_name)
    )
    user_str = f"{tg_link} (ID: <code>{u.id}</code>)"

    lines = [
        f"<b>Заявка #</b><code>{order.id}</code>",
        f"<b>Дата создания:</b> {order.created_at.strftime('%d.%m.%Y %H:%M')}",
        "",
        f"<b>Пользователь:</b> {user_str}",
        f"<b>Имя:</b> {e(u.full_name)}",
        "",
        f"<b>Модель:</b> {model_str}",
        f"<b>Сервис:</b> {svc_str}",
        f"<b>Тип:</b> {_TYPE_RU.get(svc.service_type, svc.service_type) if svc else '—'}",
        "",
        f"<b>Метро:</b> {e(order.metro_station) if order.metro_station else '—'}",
        f"<b>Дата записи:</b> {order.scheduled_date or '—'}",
        f"<b>Время:</b> {order.scheduled_time or '—'}",
        "",
        f"<b>Статус:</b> {_STATUS_RU.get(order.status, order.status)}",
        f"<b>Код заказа:</b> <code>{_display_order_code(order)}</code>",
    ]
    if order.upgrade_category:
        lines.append(f"<b>Категория апгрейда:</b> {e(order.upgrade_category)}")
    if order.diagnostics_price:
        incl = (
            " (входит в стоимость)"
            if svc and svc.diagnostics_included
            else " (оплачивается отдельно)"
        )
        lines.append(f"<b>Диагностика:</b> {order.diagnostics_price:.0f} ₽{incl}")
    if order.total_cost is not None:
        lines.append(f"<b>Итоговая стоимость:</b> {order.total_cost:.0f} ₽")
    if order.estimate_cost is not None:
        lines.append(f"<b>Смета:</b> {order.estimate_cost:.0f} ₽")
    if order.estimate_items:
        lines.append(f"<b>Работы:</b> {e(order.estimate_items)}")
    if order.estimate_deadline:
        lines.append(f"<b>Срок:</b> {e(order.estimate_deadline)}")
    if order.estimate_description:
        lines.append(f"<b>Описание сметы:</b> {e(order.estimate_description)}")
    if order.payment_id:
        lines.append(f"<b>ID платежа:</b> <code>{order.payment_id}</code>")
    if order.problem_description:
        lines.append(f"<b>Описание проблемы:</b> {e(order.problem_description)}")
    if order.reject_reason:
        lines.append(f"<b>Причина отказа:</b> {e(order.reject_reason)}")
    if order.refusal_reason:
        lines.append(f"<b>Причина отказа клиента:</b> {e(order.refusal_reason)}")
    if order.dispute_reason:
        lines.append(f"<b>Причина оспаривания:</b> {e(order.dispute_reason)}")
    if order.partner_comment:
        lines.append(f"<b>Комментарий партнёра:</b> {e(order.partner_comment)}")
    if order.client_visited is not None:
        lines.append(
            f"<b>Клиент был в сервисе:</b> {'Да' if order.client_visited else 'Нет'}"
        )
    return "\n".join(lines)


def _fmt_service(service: Service) -> str:
    category = service.category_rel.name if service.category_rel else "—"
    lines = [
        f"<b>Сервис #</b><code>{service.id}</code>",
        f"<b>Название:</b> {e(service.name or '—')}",
        f"<b>Тип:</b> {TYPE_RU.get(service.service_type or '', service.service_type or '—')}",
        f"<b>Категория:</b> {e(category)}",
        f"<b>Статус партнёрства:</b> {e(service.partnership_status or '—')}",
        f"<b>Доступен:</b> {'Да' if service.is_available else 'Нет'}",
        "",
        f"<b>Адрес:</b> {e(service.address or '—')}",
        f"<b>Метро:</b> {e(service.nearest_metro or '—')}",
        f"<b>Телефон:</b> {e(service.phone or '—')}",
        f"<b>Telegram:</b> {e(service.telegram_handle or '—')}",
    ]
    if service.open_time or service.close_time:
        lines.append(
            f"<b>График:</b> {service.open_time or '?'}-{service.close_time or '?'}"
        )
    if service.working_days:
        lines.append(
            f"<b>Рабочие дни:</b> {e(service.working_days.replace(',', ', '))}"
        )
    if service.yandex_rating is not None:
        lines.append(f"<b>Рейтинг Я.Карт:</b> {service.yandex_rating}")
    if service.upgrade_categories:
        lines.append(
            f"<b>Категории апгрейда:</b> {e(service.upgrade_categories.replace(',', ', '))}"
        )
    if service.has_hydroisolation:
        lines.append(
            f"<b>Гидроизоляция:</b> Да ({e(service.hydroisolation_price or 'цена не указана')})"
        )
    else:
        lines.append("<b>Гидроизоляция:</b> Нет")
    if service.diagnostics_price is not None:
        lines.append(f"<b>Диагностика:</b> {service.diagnostics_price:.0f} ₽")
    return "\n".join(lines)


async def _get_orders(
    page: int,
    status_filter: str | None = None,
) -> tuple[list[Order], int, int]:
    """Возвращает (заявки на странице, total_pages, total_count)."""
    offset = page * ADMIN_PAGE_SIZE
    async with async_session() as session:
        query = select(Order)
        count_q = select(func.count()).select_from(Order)
        if status_filter:
            query = query.where(Order.status == status_filter)
            count_q = count_q.where(Order.status == status_filter)
        total: int = (await session.execute(count_q)).scalar_one()
        orders = (
            (
                await session.execute(
                    query.order_by(Order.created_at.desc())
                    .offset(offset)
                    .limit(ADMIN_PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    logger.info(
        "ADM_ORDERS | page=%d | filter=%s | total=%d | page_size=%d",
        page,
        status_filter,
        total,
        len(orders),
    )
    total_pages = max(1, math.ceil(total / ADMIN_PAGE_SIZE))
    return list(orders), total_pages, total


async def _send_or_edit(
    event: Union[types.Message, types.CallbackQuery],
    text: str,
    markup,
) -> None:
    """Редактирует сообщение для callback или отправляет новое для message."""
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


def _adm_notif_markup(settings) -> types.InlineKeyboardMarkup:
    return adm_notif_settings_kb(
        enabled=bool(settings.notif_enabled),
        dispute=bool(settings.notif_client_dispute),
        client_cancel=bool(settings.notif_client_cancel),
        no_center=bool(settings.notif_no_center),
        completed=bool(settings.notif_order_completed),
    )


def _apply_adm_notif_preset(settings, preset: str) -> bool:
    if preset == "all_on":
        value = True
    elif preset == "all_off":
        value = False
    else:
        return False

    settings.notif_enabled = value
    settings.notif_client_dispute = value
    settings.notif_client_cancel = value
    settings.notif_no_center = value
    settings.notif_order_completed = value
    return True


# ══════════════════════════════════════════════════════════════
# Вход в панель
# ══════════════════════════════════════════════════════════════


@router.message(F.text == Btn.ADMIN_PANEL)
async def admin_enter(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(Order))
        ).scalar_one()
        stats_rows = (
            await session.execute(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
        ).all()
        partner_total = (
            await session.execute(
                select(func.count())
                .select_from(ServiceDraft)
                .where(ServiceDraft.registration_complete.is_(True))
            )
        ).scalar_one()
    stat_lines = [
        f"<b>Партнёров:</b> {partner_total}",
        f"<b>Заявок:</b> {total}",
        "",
    ]
    for status, cnt in stats_rows:
        stat_lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await message.answer("\n".join(stat_lines), reply_markup=admin_main_kb())


# ══════════════════════════════════════════════════════════════
# Навигация — главное меню
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:main")
async def adm_main(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(Order))
        ).scalar_one()
        stats_rows = (
            await session.execute(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
        ).all()
        partner_total = (
            await session.execute(
                select(func.count())
                .select_from(ServiceDraft)
                .where(ServiceDraft.registration_complete.is_(True))
            )
        ).scalar_one()
    stat_lines = [
        f"<b>Партнёров:</b> {partner_total}",
        f"<b>Заявок:</b> {total}",
        "",
    ]
    for status, cnt in stats_rows:
        stat_lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await cb.message.edit_text("\n".join(stat_lines), reply_markup=admin_main_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:notif")
async def adm_notif_menu(cb: types.CallbackQuery) -> None:
    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_CLIENT,
        )
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (client bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_adm_notif_markup(settings),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:notif:toggle:"))
async def adm_notif_toggle(cb: types.CallbackQuery) -> None:
    field = cb.data.split(":")[3]
    attr_name = _ADM_NOTIF_TOGGLE_TO_ATTR.get(field)
    if attr_name is None:
        await cb.answer("Неизвестная настройка.", show_alert=True)
        return

    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_CLIENT,
        )
        current = bool(getattr(settings, attr_name))
        setattr(settings, attr_name, not current)
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (client bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_adm_notif_markup(settings),
    )
    await cb.answer("Настройки обновлены")


@router.callback_query(F.data.startswith("adm:notif:preset:"))
async def adm_notif_preset(cb: types.CallbackQuery) -> None:
    preset = cb.data.split(":")[3]
    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_CLIENT,
        )
        if not _apply_adm_notif_preset(settings, preset):
            await cb.answer("Неизвестный пресет.", show_alert=True)
            return
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (client bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_adm_notif_markup(settings),
    )
    await cb.answer("Настройки обновлены")


# ══════════════════════════════════════════════════════════════
# Выход из панели
# ══════════════════════════════════════════════════════════════


@router.message(F.text == Btn.EXIT_PANEL)
async def admin_exit(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        Client.Admin.EXIT,
        reply_markup=main_menu_kb(is_admin=True),
    )


# ══════════════════════════════════════════════════════════════
# Фильтр по статусу
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:filter")
async def adm_filter(cb: types.CallbackQuery) -> None:
    await cb.message.edit_text(
        Client.Admin.CHOOSE_STATUS_FILTER, reply_markup=admin_filter_kb()
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Список заявок  adm:orders:{page}  или  adm:orders:{page}:status:{status}
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("adm:orders:"))
async def adm_orders_list(cb: types.CallbackQuery, state: FSMContext) -> None:
    # Парсим callback: adm:orders:{page}  или  adm:orders:{page}:status:{status}
    parts = cb.data.split(":")  # ["adm", "orders", page, "status", status]
    page = int(parts[2])
    status_filter = parts[4] if len(parts) >= 5 and parts[3] == "status" else None

    orders, total_pages, total_count = await _get_orders(page, status_filter)
    await state.update_data(orders_page=page, orders_status_filter=status_filter)
    await state.set_state(AdminFSM.orders_list)

    if not orders:
        try:
            await cb.message.edit_text(
                "Заявки не найдены.",
                reply_markup=admin_main_kb(),
            )
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    header = (
        "Все заявки"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    try:
        await cb.message.edit_text(
            f"<b>{header}:</b> {total_count} (стр. {page + 1}/{total_pages})",
            reply_markup=admin_orders_kb(orders, page, total_pages, status_filter),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Карточка конкретной заявки
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("adm:order:"))
async def adm_order_detail(cb: types.CallbackQuery, state: FSMContext) -> None:
    order_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if not order:
        await cb.answer("Заявка не найдена.", show_alert=True)
        return
    await state.update_data(detail_order_id=order_id)
    await state.set_state(AdminFSM.order_detail)
    await cb.message.edit_text(
        _fmt_order(order),
        reply_markup=admin_order_detail_kb(order_id, order.service_id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:service:"))
async def adm_service_detail(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Сервис не найден.", show_alert=True)
        return

    service_id = int(parts[2])
    order_id: int | None = None
    if len(parts) >= 4 and parts[3].isdigit():
        order_id = int(parts[3])

    async with async_session() as session:
        service = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
    if not service:
        await cb.answer("Сервис не найден.", show_alert=True)
        return

    await cb.message.edit_text(
        _fmt_service(service),
        reply_markup=admin_service_detail_kb(order_id),
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Смена статуса заявки
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("adm:setstatus:"))
async def adm_set_status(cb: types.CallbackQuery, state: FSMContext) -> None:
    # adm:setstatus:{order_id}:{new_status}
    parts = cb.data.split(":")
    order_id = int(parts[2])
    new_status = parts[3]

    # 1. Обновляем статус
    old_status: str | None = None
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order:
            await cb.answer("Заявка не найдена.", show_alert=True)
            return
        old_status = order.status
        order.status = new_status
        await session.commit()

    # 2. Перечитываем с relations (после commit все атрибуты expired)
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()

    await cb.answer(
        f"Статус изменён: {_STATUS_RU.get(old_status, old_status)} → {_STATUS_RU.get(new_status, new_status)}",
        show_alert=True,
    )
    await cb.message.edit_text(
        _fmt_order(order),
        reply_markup=admin_order_detail_kb(order_id, order.service_id),
    )


# ══════════════════════════════════════════════════════════════
# Назад к списку из детального просмотра
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:back_list")
async def adm_back_to_list(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("orders_page", 0)
    status_filter = data.get("orders_status_filter")
    orders, total_pages, total_count = await _get_orders(page, status_filter)

    if not orders:
        await cb.message.edit_text("Заявки не найдены.", reply_markup=admin_main_kb())
        await cb.answer()
        return

    header = (
        "Все заявки"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    try:
        await cb.message.edit_text(
            f"<b>{header}:</b> {total_count} (стр. {page + 1}/{total_pages})",
            reply_markup=admin_orders_kb(orders, page, total_pages, status_filter),
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            raise
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# No-op кнопка (номер страницы)
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:noop")
async def adm_noop(cb: types.CallbackQuery) -> None:
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Список партнёров (ServiceOwner) в клиентском боте
# ══════════════════════════════════════════════════════════════


_PARTNER_STATUS_RU: dict[str, str] = {
    "ожидает": "Ожидает модерации",
    "активный": "Активный",
    "отклонён": "Отклонён",
    "приостановлен": "Приостановлен",
}


def _fmt_partner_short(owner: ServiceDraft) -> str:
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплекс"}
    lines = [
        f"<b>Партнёр #</b><code>{owner.id}</code>",
        f"<b>TG ID:</b> <code>{owner.owner_user_id}</code>",
        f"<b>Статус:</b> {_PARTNER_STATUS_RU.get(owner.status, owner.status)}",
        f"<b>Название:</b> {e(owner.draft_name or '—')}",
        f"<b>Тип:</b> {type_map.get(owner.draft_service_type or '', owner.draft_service_type or '—')}",
        f"<b>Адрес:</b> {e(owner.draft_address or '—')}",
        f"<b>Метро:</b> {e(owner.draft_metro or '—')}",
        f"<b>Телефон:</b> {e(owner.draft_phone or '—')}",
    ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("adm:partners:"))
async def adm_partners_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    page = int(parts[2])

    async with async_session() as session:
        all_owners = (
            (
                await session.execute(
                    select(ServiceDraft)
                    .where(ServiceDraft.registration_complete.is_(True))
                    .order_by(ServiceDraft.registered_at.desc())
                )
            )
            .scalars()
            .all()
        )
    total = len(all_owners)
    owners = all_owners[page * ADMIN_PAGE_SIZE : (page + 1) * ADMIN_PAGE_SIZE]
    logger.info(
        "ADM_PARTNERS | total=%d | page=%d | page_size=%d", total, page, len(owners)
    )
    total_pages = max(1, math.ceil(total / ADMIN_PAGE_SIZE))
    if not owners:
        try:
            await cb.message.edit_text("Нет партнёров.", reply_markup=admin_main_kb())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    _SHORT = {
        "ожидает": "Ожид.",
        "активный": "Активен",
        "отклонён": "Откл.",
        "приостановлен": "Приост.",
    }
    rows: list[list[InlineKeyboardButton]] = []
    for o in owners:
        label = f"#{o.id} | {o.draft_name or '?'} | {_SHORT.get(o.status, o.status)}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"adm:partner_detail:{o.id}"
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◄ Назад", callback_data=f"adm:partners:{page - 1}"
            )
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="adm:noop")
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ►", callback_data=f"adm:partners:{page + 1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="В главное меню", callback_data="adm:main")])

    await cb.message.edit_text(
        f"<b>Заявки партнёров:</b> {total} (стр. {page + 1}/{total_pages})",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:partner_detail:"))
async def adm_partner_detail(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if not owner:
        await cb.answer("Партнёр не найден.", show_alert=True)
        return
    await cb.message.edit_text(
        _fmt_partner_short(owner),
        reply_markup=admin_partner_detail_kb(owner.id, owner.status),
    )
    await cb.answer()
