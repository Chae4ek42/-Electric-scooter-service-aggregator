"""Partner bot: admin panel for partner application moderation."""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
import zoneinfo
from dataclasses import dataclass
from typing import Sequence, Union

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.core.database import async_session
from client_bot.core.formatting import e
from client_bot.core.resilience import create_guarded_task
from client_bot.services.admin_notifications import notify_admins
from client_bot.services.notification_settings import (
    ADMIN_SCOPE_PARTNER,
    get_or_create_admin_settings,
)
from client_bot.domain.models import (
    Order,
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
    ServiceOwnerSettings,
)
from client_bot.services.sheets_writer import update_service_row
from client_bot.texts import Btn, ORDER_STATUS_RU, PARTNER_STATUS_RU, Partner, TYPE_RU
from partner_bot.handlers.common import _draft_complete
from partner_bot.ui.keyboards import (
    padm_main_kb,
    padm_notif_settings_kb,
    padm_partner_detail_kb,
    padm_partners_kb,
    padm_service_detail_kb,
    padm_service_order_detail_kb,
    padm_service_orders_kb,
    padm_services_kb,
    partner_main_menu_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_admin")

_PADM_NOTIF_TOGGLE_TO_ATTR = {
    "enabled": "notif_enabled",
    "partner_application": "notif_partner_application",
    "profile_update": "notif_partner_profile_update",
    "status_change": "notif_partner_status_change",
}

PARTNER_PAGE_SIZE = 10
SERVICE_PAGE_SIZE = 10
SERVICE_ORDERS_PAGE_SIZE = 10
_STATUS_RU = PARTNER_STATUS_RU

try:
    _MSK = zoneinfo.ZoneInfo("Europe/Moscow")
except Exception:
    _MSK = datetime.timezone(datetime.timedelta(hours=3), name="MSK")


def _display_order_code(order: Order) -> str:
    code = (order.order_code or "").strip()
    return code or f"{order.id:06d}"


def _format_pause_until(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        dt = value.replace(tzinfo=_MSK)
    else:
        dt = value.astimezone(_MSK)
    return dt.strftime("%d.%m.%Y %H:%M")


@dataclass
class AdminServiceStatsItem:
    id: int
    name: str
    orders_total: int


def _schedule_service_sheet_sync(service_id: int) -> None:
    """Sync approved service to Sheets in background thread."""

    async def _run() -> None:
        try:
            async with async_session() as session:
                svc = (
                    await session.execute(
                        select(Service)
                        .options(selectinload(Service.category_rel))
                        .where(Service.id == service_id)
                    )
                ).scalar_one_or_none()
            if svc is None:
                return
            await asyncio.to_thread(update_service_row, svc)
        except Exception:
            logger.exception("Failed to update approved service in Sheets")

    create_guarded_task(
        _run(),
        logger=logger,
        task_name=f"partner_admin_sheet_sync:{service_id}",
        action_type="partner_admin_sheet_sync_error",
        payload=f"service_id={service_id}",
    )


def _padm_notif_markup(settings) -> types.InlineKeyboardMarkup:
    return padm_notif_settings_kb(
        enabled=bool(settings.notif_enabled),
        partner_application=bool(settings.notif_partner_application),
        profile_update=bool(settings.notif_partner_profile_update),
        status_change=bool(settings.notif_partner_status_change),
    )


def _apply_padm_notif_preset(settings, preset: str) -> bool:
    if preset == "all_on":
        value = True
    elif preset == "all_off":
        value = False
    else:
        return False

    settings.notif_enabled = value
    settings.notif_partner_application = value
    settings.notif_partner_profile_update = value
    settings.notif_partner_status_change = value
    return True


async def _notify_partner_admins(
    bot,
    *,
    event_key: str,
    text: str,
    dedupe_suffix: str,
) -> None:
    await notify_admins(
        bot,
        scope=ADMIN_SCOPE_PARTNER,
        event_key=event_key,
        text=text,
        dedupe_prefix=f"partner_admin:{event_key}:{dedupe_suffix}",
    )


class IsAdmin(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        uname = event.from_user.username if event.from_user else None
        return bool(uname) and uname.lower() in ADMIN_USERNAMES


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _fmt_partner(owner: ServiceDraft) -> str:
    type_label = TYPE_RU.get(
        owner.draft_service_type or "", owner.draft_service_type or "—"
    )
    service = owner.service
    availability = "—"
    if service is not None:
        availability = "Да" if service.is_available else "Нет"

    lines = [
        f"<b>Партнёр #</b><code>{owner.id}</code>",
        f"<b>Telegram ID:</b> <code>{owner.owner_user_id}</code>",
        f"<b>Статус:</b> {_STATUS_RU.get(owner.status, owner.status)}",
        f"<b>Доступность:</b> {availability}",
    ]
    if service is not None and not service.is_available:
        pause_text = _format_pause_until(service.pause_until)
        lines.append(f"<b>Закрыт до:</b> {pause_text or 'вручную'}")

    lines += [
        "",
        f"<b>Название:</b> {e(owner.draft_name or '—')}",
        f"<b>Тип:</b> {type_label}",
    ]
    if owner.draft_service_type == "upgrade":
        cats = (owner.draft_upgrade_categories or "").replace(",", ", ") or "—"
        lines.append(f"<b>Категории апгрейда:</b> {e(cats)}")
    if owner.draft_service_type in ("repair", "complex"):
        lines.append(f"<b>Категория ремонта:</b> {e(owner.draft_category or '—')}")
    lines.append(
        f"<b>Гидроизоляция:</b> {'Да' if owner.draft_hydroisolation else 'Нет'}"
    )
    if owner.draft_hydroisolation:
        lines.append(f"<b>Цена гидроизоляции:</b> {e(owner.draft_hydro_price or '—')}")

    lines += [
        f"<b>Адрес:</b> {e(owner.draft_address or '—')}",
        f"<b>Метро:</b> {e(owner.draft_metro or '—')}",
        f"<b>Телефон:</b> {e(owner.draft_phone or '—')}",
        f"<b>Telegram:</b> {e(owner.draft_telegram or '—')}",
        f"<b>Рабочие дни:</b> {e((owner.draft_working_days or '').replace(',', ', ') or '—')}",
        f"<b>Часы:</b> {owner.draft_open_time or '?'}—{owner.draft_close_time or '?'}",
    ]
    if owner.draft_diagnostics_price is not None and owner.draft_diagnostics_price > 0:
        lines.append(f"<b>Диагностика:</b> {int(owner.draft_diagnostics_price)} ₽")
    else:
        lines.append("<b>Диагностика:</b> бесплатно")

    lines.append(
        f"<b>Входит в стоимость:</b> {'Да' if owner.draft_diag_included else 'Нет'}"
    )
    lines += [
        f"<b>Форма:</b> {e(owner.draft_legal_form or '—')}",
        f"<b>Налогообложение:</b> {e(owner.draft_tax_system or '—')}",
        "",
        "<b>Банковские реквизиты:</b>",
        f"  Р/с: {e(owner.draft_bank_account or '—')}",
        f"  Банк: {e(owner.draft_bank_name or '—')}",
        f"  БИК: {e(owner.draft_bik or '—')}",
        f"  К/с: {e(owner.draft_corr_account or '—')}",
        f"  Организация: {e(owner.draft_org_name or '—')}",
        f"  ИНН: {e(owner.draft_inn or '—')}",
    ]
    return "\n".join(lines)


async def _panel_stats() -> tuple[int, list[tuple[str, int]]]:
    async with async_session() as session:
        owners = (
            (
                await session.execute(
                    select(ServiceDraft).where(
                        ServiceDraft.registration_complete.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    valid_owners = [owner for owner in owners if _draft_complete(owner)]
    by_status: dict[str, int] = {}
    for owner in valid_owners:
        by_status[owner.status] = by_status.get(owner.status, 0) + 1
    return len(valid_owners), [(status, cnt) for status, cnt in by_status.items()]


async def _service_stats_page(
    page: int,
) -> tuple[list[AdminServiceStatsItem], int, int, int]:
    async with async_session() as session:
        services = (
            (
                await session.execute(
                    select(Service)
                    .where(Service.registration_complete.is_(True))
                    .order_by(Service.id.desc())
                )
            )
            .scalars()
            .all()
        )

        total_services = len(services)
        if total_services == 0:
            return [], 0, 1, 0

        total_pages = max(1, math.ceil(total_services / SERVICE_PAGE_SIZE))
        safe_page = max(0, min(page, total_pages - 1))
        page_services = services[
            safe_page * SERVICE_PAGE_SIZE : (safe_page + 1) * SERVICE_PAGE_SIZE
        ]

        service_ids = [svc.id for svc in page_services]
        stats_rows = (
            await session.execute(
                select(Order.service_id, func.count(Order.id))
                .where(Order.service_id.in_(service_ids))
                .group_by(Order.service_id)
            )
        ).all()
        totals_by_service = {int(sid): int(cnt) for sid, cnt in stats_rows if sid}

    items = [
        AdminServiceStatsItem(
            id=svc.id,
            name=svc.name or "Без названия",
            orders_total=totals_by_service.get(svc.id, 0),
        )
        for svc in page_services
    ]
    return items, safe_page, total_pages, total_services


async def _service_status_breakdown(service_id: int) -> dict[str, int]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(Order.service_id == service_id)
                .group_by(Order.status)
            )
        ).all()
    return {status: int(cnt) for status, cnt in rows}


async def _service_orders_snapshot(service_id: int) -> list[Order]:
    async with async_session() as session:
        return (
            (
                await session.execute(
                    select(Order)
                    .where(Order.service_id == service_id)
                    .order_by(Order.created_at.desc())
                )
            )
            .scalars()
            .all()
        )


async def _service_orders_page(
    service_id: int,
    page: int,
) -> tuple[list[Order], int, int, int]:
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.service_id == service_id)
            )
        ).scalar_one()

        if total == 0:
            return [], 0, 1, 0

        total_pages = max(1, math.ceil(total / SERVICE_ORDERS_PAGE_SIZE))
        safe_page = max(0, min(page, total_pages - 1))
        orders = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.service_id == service_id)
                    .order_by(Order.created_at.desc())
                    .offset(safe_page * SERVICE_ORDERS_PAGE_SIZE)
                    .limit(SERVICE_ORDERS_PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )

    return list(orders), safe_page, total_pages, int(total)


def _orders_status_breakdown(orders: Sequence[Order]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for order in orders:
        breakdown[order.status] = breakdown.get(order.status, 0) + 1
    return breakdown


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _order_model_text(order: Order) -> str:
    if order.brand_custom_name:
        if order.model_custom_name:
            return f"{order.brand_custom_name} / {order.model_custom_name}"
        return order.brand_custom_name
    if order.model_custom_name:
        if order.model and order.model.brand:
            return f"{order.model.brand.name} / {order.model_custom_name}"
        return order.model_custom_name
    if order.model and order.model.brand:
        return f"{order.model.brand.name} {order.model.name}"
    if order.model:
        return order.model.name
    return "—"


def _format_service_stats_text(
    service: Service,
    by_status: dict[str, int],
    orders: Sequence[Order],
) -> str:
    total_orders = len(orders)
    completed = by_status.get("completed", 0)
    paid = by_status.get("paid", 0)
    accepted_flow = sum(
        by_status.get(key, 0)
        for key in ("accepted", "in_progress", "ready_for_pickup", "completed")
    )
    failed = sum(
        by_status.get(key, 0)
        for key in (
            "cancelled",
            "rejected_by_partner",
            "client_refused",
            "interrupted",
            "disputed",
        )
    )

    completed_costs = [
        float(order.total_cost)
        for order in orders
        if order.status == "completed" and order.total_cost is not None
    ]
    revenue = sum(completed_costs)
    avg_check = (revenue / len(completed_costs)) if completed_costs else 0.0
    median_check = _median(completed_costs)

    cycle_hours = [
        (order.completed_at - order.created_at).total_seconds() / 3600
        for order in orders
        if order.status == "completed"
        and order.completed_at is not None
        and order.created_at is not None
    ]
    avg_cycle_hours = sum(cycle_hours) / len(cycle_hours) if cycle_hours else 0.0

    conversion = (completed / total_orders * 100) if total_orders else 0.0
    accepted_conversion = (accepted_flow / total_orders * 100) if total_orders else 0.0

    lines = [
        f"<b>Сервис #{service.id}</b>",
        f"<b>Название:</b> {e(service.name or '—')}",
        f"<b>Тип:</b> {TYPE_RU.get(service.service_type or '', service.service_type or '—')}",
        f"<b>Доступность:</b> {'Да' if service.is_available else 'Нет'}",
    ]
    if not service.is_available:
        pause_text = _format_pause_until(service.pause_until)
        lines.append(f"<b>Закрыт до:</b> {pause_text or 'вручную'}")

    lines += [
        f"<b>Адрес:</b> {e(service.address or '—')}",
        f"<b>Метро:</b> {e(service.nearest_metro or '—')}",
        f"<b>Телефон:</b> {e(service.phone or '—')}",
        "",
        "<b>Бизнес-метрики:</b>",
        f"<b>Всего заявок:</b> {total_orders}",
        f"<b>Оплачено:</b> {paid}",
        f"<b>Дошли до работы:</b> {accepted_flow}",
        f"<b>Завершено:</b> {completed}",
        f"<b>Негативные исходы:</b> {failed}",
        f"<b>Выручка (completed):</b> {revenue:.0f} ₽",
        f"<b>Средний чек (completed):</b> {avg_check:.0f} ₽",
        f"<b>Медианный чек (completed):</b> {median_check:.0f} ₽",
        f"<b>Средний цикл до завершения:</b> {avg_cycle_hours:.1f} ч",
        f"<b>Конверсия в работу:</b> {accepted_conversion:.1f}%",
        f"<b>Конверсия в завершение:</b> {conversion:.1f}%",
        "",
        "<b>Статусы:</b>",
    ]

    for status_key, status_label in ORDER_STATUS_RU.items():
        cnt = by_status.get(status_key)
        if cnt:
            lines.append(f"• {status_label}: {cnt}")

    if all(not by_status.get(k) for k in ORDER_STATUS_RU):
        lines.append("• Пока нет заявок")

    return "\n".join(lines)


def _format_service_order_detail_text(order: Order) -> str:
    created_at = (
        order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "—"
    )
    slot = f"{order.scheduled_date or '—'} {order.scheduled_time or ''}".strip()
    lines = [
        f"<b>Заявка #{order.id}</b>",
        f"<b>Код заказа:</b> <code>{_display_order_code(order)}</code>",
        f"<b>Статус:</b> {ORDER_STATUS_RU.get(order.status, order.status)}",
        f"<b>Создана:</b> {created_at}",
        f"<b>Клиент TG ID:</b> <code>{order.user_id}</code>",
        f"<b>Модель:</b> {e(_order_model_text(order))}",
        f"<b>Метро:</b> {e(order.metro_station or '—')}",
        f"<b>Слот:</b> {e(slot)}",
    ]
    if order.upgrade_category:
        lines.append(f"<b>Категория апгрейда:</b> {e(order.upgrade_category)}")
    if order.problem_description:
        lines.append(f"<b>Описание:</b> {e(order.problem_description)}")
    if order.diagnostics_price is not None:
        lines.append(f"<b>Диагностика:</b> {order.diagnostics_price:.0f} ₽")
    if order.estimate_cost is not None:
        lines.append(f"<b>Смета:</b> {order.estimate_cost:.0f} ₽")
    if order.total_cost is not None:
        lines.append(f"<b>Итоговая стоимость:</b> {order.total_cost:.0f} ₽")
    if order.partner_comment:
        lines.append(f"<b>Комментарий партнёра:</b> {e(order.partner_comment)}")
    if order.reject_reason:
        lines.append(f"<b>Причина отклонения:</b> {e(order.reject_reason)}")
    if order.refusal_reason:
        lines.append(f"<b>Причина отказа клиента:</b> {e(order.refusal_reason)}")
    if order.dispute_reason:
        lines.append(f"<b>Причина спора:</b> {e(order.dispute_reason)}")

    return "\n".join(lines)


@router.message(F.text == Btn.ADMIN_PANEL)
async def padm_enter(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    total, stats_rows = await _panel_stats()
    lines = [f"<b>Партнёров:</b> {total}", ""]
    for status, cnt in stats_rows:
        lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await message.answer("\n".join(lines), reply_markup=padm_main_kb())


@router.callback_query(F.data == "padm:main")
async def padm_main(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    total, stats_rows = await _panel_stats()
    lines = [f"<b>Партнёров:</b> {total}", ""]
    for status, cnt in stats_rows:
        lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await cb.message.edit_text("\n".join(lines), reply_markup=padm_main_kb())
    await cb.answer()


@router.callback_query(F.data == "padm:notif")
async def padm_notif_menu(cb: types.CallbackQuery) -> None:
    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_PARTNER,
        )
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (partner bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_padm_notif_markup(settings),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:notif:toggle:"))
async def padm_notif_toggle(cb: types.CallbackQuery) -> None:
    field = cb.data.split(":")[3]
    attr_name = _PADM_NOTIF_TOGGLE_TO_ATTR.get(field)
    if attr_name is None:
        await cb.answer("Неизвестная настройка.", show_alert=True)
        return

    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_PARTNER,
        )
        current = bool(getattr(settings, attr_name))
        setattr(settings, attr_name, not current)
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (partner bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_padm_notif_markup(settings),
    )
    await cb.answer("Настройки обновлены")


@router.callback_query(F.data.startswith("padm:notif:preset:"))
async def padm_notif_preset(cb: types.CallbackQuery) -> None:
    preset = cb.data.split(":")[3]
    async with async_session() as session:
        settings = await get_or_create_admin_settings(
            session,
            admin_user_id=cb.from_user.id,
            scope=ADMIN_SCOPE_PARTNER,
        )
        if not _apply_padm_notif_preset(settings, preset):
            await cb.answer("Неизвестный пресет.", show_alert=True)
            return
        await session.commit()

    await cb.message.edit_text(
        "Настройки админ-уведомлений (partner bot):\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_padm_notif_markup(settings),
    )
    await cb.answer("Настройки обновлены")


@router.message(F.text == Btn.EXIT_PANEL)
async def padm_exit(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        Partner.Admin.EXIT,
        reply_markup=partner_main_menu_kb(is_admin=True),
    )


@router.callback_query(F.data.startswith("padm:partners:"))
async def padm_partners_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    page = int(parts[2])
    status_filter = parts[4] if len(parts) >= 5 and parts[3] == "status" else None

    async with async_session() as session:
        q = (
            select(ServiceDraft)
            .options(selectinload(ServiceDraft.service))
            .where(ServiceDraft.registration_complete.is_(True))
        )
        if status_filter:
            q = q.where(ServiceDraft.status == status_filter)

        all_owners = (
            (await session.execute(q.order_by(ServiceDraft.registered_at.desc())))
            .scalars()
            .all()
        )
    all_owners = [owner for owner in all_owners if _draft_complete(owner)]

    total = len(all_owners)
    owners = all_owners[page * PARTNER_PAGE_SIZE : (page + 1) * PARTNER_PAGE_SIZE]
    total_pages = max(1, math.ceil(total / PARTNER_PAGE_SIZE))

    if not owners:
        try:
            await cb.message.edit_text(
                "Нет заявок партнёров.", reply_markup=padm_main_kb()
            )
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    header = (
        "Все партнёры"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    await cb.message.edit_text(
        f"<b>{header}:</b> {total} (стр. {page + 1}/{total_pages})",
        reply_markup=padm_partners_kb(list(owners), page, total_pages, status_filter),
    )
    await cb.answer()


@router.callback_query(F.data == "padm:filter")
async def padm_filter(cb: types.CallbackQuery) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    statuses = [
        ("ожидает", "Ожид. модерации"),
        ("активный", "Активные"),
        ("приостановлен", "Приостановленные"),
        ("отклонён", "Отклонённые"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=label, callback_data=f"padm:partners:0:status:{key}"
            )
        ]
        for key, label in statuses
    ]
    rows.append([InlineKeyboardButton(text="Все", callback_data="padm:partners:0")])

    await cb.message.edit_text(
        "Фильтр по статусу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:services:"))
async def padm_services_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) >= 3 else 0

    items, safe_page, total_pages, total_services = await _service_stats_page(page)
    if not items:
        await cb.message.edit_text(
            "Сервисы не найдены.",
            reply_markup=padm_main_kb(),
        )
        await cb.answer()
        return

    await cb.message.edit_text(
        f"<b>Сервисов:</b> {total_services} (стр. {safe_page + 1}/{total_pages})\n"
        "Выберите сервис, чтобы посмотреть подробную статистику.",
        reply_markup=padm_services_kb(items, safe_page, total_pages),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:service:"))
async def padm_service_detail(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Некорректный запрос.", show_alert=True)
        return

    service_id = int(parts[2])
    from_page = 0
    if len(parts) >= 5 and parts[3] == "from":
        from_page = int(parts[4])

    async with async_session() as session:
        service = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
    if not service:
        await cb.answer("Сервис не найден.", show_alert=True)
        return

    orders_snapshot = await _service_orders_snapshot(service_id)
    breakdown = _orders_status_breakdown(orders_snapshot)
    await cb.message.edit_text(
        _format_service_stats_text(service, breakdown, orders_snapshot),
        reply_markup=padm_service_detail_kb(from_page, service_id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:service_orders:"))
async def padm_service_orders_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer("Некорректный запрос.", show_alert=True)
        return

    service_id = int(parts[2])
    page = int(parts[3])
    from_page = 0
    if len(parts) >= 6 and parts[4] == "from":
        from_page = int(parts[5])

    orders, safe_page, total_pages, total = await _service_orders_page(service_id, page)
    if not orders:
        await cb.message.edit_text(
            f"По сервису #{service_id} пока нет заявок.",
            reply_markup=padm_service_orders_kb(
                service_id,
                [],
                safe_page,
                total_pages,
                from_page,
            ),
        )
        await cb.answer()
        return

    await cb.message.edit_text(
        f"<b>Заявки сервиса #{service_id}:</b> {total} "
        f"(стр. {safe_page + 1}/{total_pages})",
        reply_markup=padm_service_orders_kb(
            service_id,
            orders,
            safe_page,
            total_pages,
            from_page,
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:service_order:"))
async def padm_service_order_detail(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    if len(parts) < 5:
        await cb.answer("Некорректный запрос.", show_alert=True)
        return

    service_id = int(parts[2])
    order_id = int(parts[3])
    page = int(parts[4])
    from_page = 0
    if len(parts) >= 7 and parts[5] == "from":
        from_page = int(parts[6])

    async with async_session() as session:
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == order_id)
                .where(Order.service_id == service_id)
            )
        ).scalar_one_or_none()
    if not order:
        await cb.answer("Заявка не найдена.", show_alert=True)
        return

    await cb.message.edit_text(
        _format_service_order_detail_text(order),
        reply_markup=padm_service_order_detail_kb(service_id, page, from_page),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:partner:"))
async def padm_partner_detail(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft)
                .options(selectinload(ServiceDraft.service))
                .where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if not owner:
        await cb.answer("Партнёр не найден.", show_alert=True)
        return

    await cb.message.edit_text(
        _fmt_partner(owner),
        reply_markup=padm_partner_detail_kb(owner.id, owner.status, owner.service_id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:approve:"))
async def padm_approve_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    admin_username = cb.from_user.username or str(cb.from_user.id)

    svc_id: int | None = None
    partner_tg_id: int | None = None
    partner_name = "—"

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Партнёр не найден.", show_alert=True)
            return
        if owner.status != "ожидает":
            await cb.answer("Нельзя одобрить — статус не ожидает.", show_alert=True)
            return
        # Guard against stale admin messages: incomplete draft must never be approved.
        if not _draft_complete(owner):
            owner.registration_complete = False
            await session.commit()
            await cb.answer(
                "Нельзя одобрить: анкета заполнена не полностью.",
                show_alert=True,
            )
            return
        partner_name = owner.draft_name or "—"

        cat_id = None
        if owner.draft_category:
            cat = (
                await session.execute(
                    select(ServiceCategory).where(
                        ServiceCategory.name == owner.draft_category
                    )
                )
            ).scalar_one_or_none()
            if cat:
                cat_id = cat.id

        svc = None
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()

        if svc is None:
            svc = Service(
                name=owner.draft_name or "Без названия",
                service_type=owner.draft_service_type or "repair",
                is_available=True,
            )
            session.add(svc)
            await session.flush()

        svc.name = owner.draft_name or "Без названия"
        svc.service_type = owner.draft_service_type or "repair"
        svc.category_id = cat_id
        svc.address = owner.draft_address
        svc.nearest_metro = owner.draft_metro
        svc.phone = owner.draft_phone
        svc.telegram_handle = owner.draft_telegram
        svc.open_time = owner.draft_open_time
        svc.close_time = owner.draft_close_time
        svc.has_hydroisolation = owner.draft_hydroisolation
        svc.hydroisolation_price = owner.draft_hydro_price
        svc.diagnostics_price = owner.draft_diagnostics_price
        svc.diagnostics_included = owner.draft_diag_included
        svc.upgrade_categories = owner.draft_upgrade_categories
        svc.working_days = owner.draft_working_days
        svc.is_available = True
        svc.registration_complete = True
        svc.partnership_status = "активный"

        owner.service_id = svc.id
        owner.status = "активный"
        owner.registration_complete = True
        owner.approved_at = datetime.datetime.now(tz=datetime.timezone.utc)
        owner.approved_by = admin_username

        bank = (
            await session.execute(
                select(ServiceBankDetails).where(
                    ServiceBankDetails.service_id == svc.id
                )
            )
        ).scalar_one_or_none()
        if bank is None:
            bank = ServiceBankDetails(service_id=svc.id)
            session.add(bank)
        bank.legal_form = owner.draft_legal_form
        bank.tax_system = owner.draft_tax_system
        bank.bank_account = owner.draft_bank_account
        bank.bank_name = owner.draft_bank_name
        bank.bik = owner.draft_bik
        bank.corr_account = owner.draft_corr_account
        bank.org_name = owner.draft_org_name
        bank.inn = owner.draft_inn

        settings = (
            await session.execute(
                select(ServiceOwnerSettings).where(
                    ServiceOwnerSettings.service_id == svc.id
                )
            )
        ).scalar_one_or_none()
        if settings is None:
            session.add(
                ServiceOwnerSettings(
                    service_id=svc.id,
                    owner_user_id=owner.owner_user_id,
                )
            )
        else:
            settings.owner_user_id = owner.owner_user_id

        await session.commit()
        svc_id = svc.id
        partner_tg_id = owner.owner_user_id

    if svc_id is not None:
        _schedule_service_sheet_sync(svc_id)

    try:
        await _notify_partner_admins(
            cb.bot,
            event_key="status_change",
            text=(
                "Статус партнёра изменён\n"
                f"Партнёр #{owner_id}: {partner_name}\n"
                "Было: ожидает\n"
                "Стало: активный\n"
                f"Администратор: @{admin_username}"
            ),
            dedupe_suffix=f"approve:{owner_id}",
        )
    except Exception:
        logger.exception("Failed to notify admins about partner approval")

    await cb.answer("Партнёр одобрен!", show_alert=True)

    if partner_tg_id is not None:
        try:
            partner_uname = ""
            try:
                chat = await cb.bot.get_chat(partner_tg_id)
                partner_uname = chat.username or ""
            except Exception:
                pass
            partner_is_admin = (
                partner_uname.lower() in ADMIN_USERNAMES if partner_uname else False
            )
            await cb.bot.send_message(
                partner_tg_id,
                "Ваша заявка одобрена! Теперь вы можете принимать заявки.",
                reply_markup=partner_main_menu_kb(is_admin=partner_is_admin),
            )
        except Exception:
            logger.exception("Failed to notify partner about approval")

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(
                owner.id, owner.status, owner.service_id
            ),
        )


@router.callback_query(F.data.startswith("padm:reject_partner:"))
async def padm_reject_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    partner_tg_id: int | None = None
    partner_name = "—"
    prev_status = "—"
    admin_username = cb.from_user.username or str(cb.from_user.id)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Партнёр не найден.", show_alert=True)
            return

        partner_name = owner.draft_name or "—"
        prev_status = owner.status
        owner.status = "отклонён"
        partner_tg_id = owner.owner_user_id

        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = False
                svc.partnership_status = "отклонён"

        await session.commit()

    try:
        await _notify_partner_admins(
            cb.bot,
            event_key="status_change",
            text=(
                "Статус партнёра изменён\n"
                f"Партнёр #{owner_id}: {partner_name}\n"
                f"Было: {prev_status}\n"
                "Стало: отклонён\n"
                f"Администратор: @{admin_username}"
            ),
            dedupe_suffix=f"reject:{owner_id}",
        )
    except Exception:
        logger.exception("Failed to notify admins about partner rejection")

    await cb.answer("Партнёр отклонён.", show_alert=True)

    if partner_tg_id is not None:
        try:
            await cb.bot.send_message(
                partner_tg_id,
                "Ваша заявка на регистрацию сервисного центра была отклонена.",
            )
        except Exception:
            logger.exception("Failed to notify partner about rejection")

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(
                owner.id, owner.status, owner.service_id
            ),
        )


@router.callback_query(F.data.startswith("padm:suspend:"))
async def padm_suspend_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    partner_name = "—"
    prev_status = "—"
    admin_username = cb.from_user.username or str(cb.from_user.id)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Не найден.", show_alert=True)
            return

        partner_name = owner.draft_name or "—"
        prev_status = owner.status
        owner.status = "приостановлен"
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = False
                svc.partnership_status = "приостановлен"

        await session.commit()

    try:
        await _notify_partner_admins(
            cb.bot,
            event_key="status_change",
            text=(
                "Статус партнёра изменён\n"
                f"Партнёр #{owner_id}: {partner_name}\n"
                f"Было: {prev_status}\n"
                "Стало: приостановлен\n"
                f"Администратор: @{admin_username}"
            ),
            dedupe_suffix=f"suspend:{owner_id}",
        )
    except Exception:
        logger.exception("Failed to notify admins about partner suspension")

    await cb.answer("Партнёр приостановлен.", show_alert=True)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(
                owner.id, owner.status, owner.service_id
            ),
        )


@router.callback_query(F.data.startswith("padm:unsuspend:"))
async def padm_unsuspend_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    partner_name = "—"
    prev_status = "—"
    admin_username = cb.from_user.username or str(cb.from_user.id)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Не найден.", show_alert=True)
            return

        partner_name = owner.draft_name or "—"
        prev_status = owner.status
        owner.status = "активный"
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = True
                svc.partnership_status = "активный"

        await session.commit()

    try:
        await _notify_partner_admins(
            cb.bot,
            event_key="status_change",
            text=(
                "Статус партнёра изменён\n"
                f"Партнёр #{owner_id}: {partner_name}\n"
                f"Было: {prev_status}\n"
                "Стало: активный\n"
                f"Администратор: @{admin_username}"
            ),
            dedupe_suffix=f"unsuspend:{owner_id}",
        )
    except Exception:
        logger.exception("Failed to notify admins about partner reactivation")

    await cb.answer("Партнёр восстановлен.", show_alert=True)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(
                owner.id, owner.status, owner.service_id
            ),
        )


@router.callback_query(F.data == "padm:noop")
async def padm_noop(cb: types.CallbackQuery) -> None:
    await cb.answer()
