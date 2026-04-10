"""Admin panel handlers — доступно только администраторам из ADMIN_USERNAMES."""

from __future__ import annotations

import logging
import math
from typing import Union

from aiogram import F, Router, types
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select

from bot.core.config import ADMIN_USERNAMES
from bot.core.database import async_session
from bot.ui.keyboards import (
    ADMIN_PAGE_SIZE,
    admin_filter_kb,
    admin_main_kb,
    admin_order_detail_kb,
    admin_orders_kb,
    main_menu_kb,
)
from bot.domain.models import (
    Brand,
    MetroStation,
    Model,
    Order,
    Service,
    ServiceCategory,
    ServiceOwner,
    User,
)

logger = logging.getLogger(__name__)
router = Router(name="admin")


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

_STATUS_RU: dict[str, str] = {
    "new": "Новая",
    "awaiting_payment": "Ожидает оплаты",
    "accepted": "Принята",
    "interrupted": "Прервана",
    "completed": "Завершена",
    "cancelled": "Отменена",
}


# ── Helpers ───────────────────────────────────────────────────


def _md_escape(text: str) -> str:
    """Escape Markdown V1 special characters in user-supplied text."""
    for ch in ("\\", "*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _fmt_order(order: Order) -> str:
    """Форматирует полную карточку заявки в Markdown."""
    e = _md_escape
    # Модель
    if order.brand_custom_name:
        model_str = f"{e(order.brand_custom_name)}"
        if order.model_custom_name:
            model_str += f" / {e(order.model_custom_name)}"
        model_str += " *(вручную)*"
    elif order.model_custom_name:
        brand_name = order.model.brand.name if order.model else "—"
        model_str = f"{e(brand_name)} / {e(order.model_custom_name)} *(вручную)*"
    elif order.model:
        model_str = f"{e(order.model.brand.name)} {e(order.model.name)}"
    else:
        model_str = "—"

    # Услуга — скрываем до оплаты
    svc = order.service
    if order.status == "awaiting_payment":
        svc_str = f"_{e(svc.name)}_ (скрыт от клиента)" if svc else "—"
    else:
        svc_str = e(svc.name) if svc else "—"
    if svc and svc.category_rel:
        svc_str += f" ({e(svc.category_rel.name)})"

    # Пользователь
    u = order.user
    tg_link = (
        f"[@{u.username}](https://t.me/{u.username})" if u.username else e(u.full_name)
    )
    user_str = f"{tg_link} (ID: `{u.id}`)"

    lines = [
        f"*Заявка #*`{order.id}`",
        f"*Дата создания:* {order.created_at.strftime('%d.%m.%Y %H:%M')}",
        "",
        f"*Пользователь:* {user_str}",
        f"*Имя:* {e(u.full_name)}",
        "",
        f"*Модель:* {model_str}",
        f"*Сервис:* {svc_str}",
        f"*Тип:* {svc.service_type if svc else '—'}",
        "",
        f"*Метро:* {e(order.metro_station) if order.metro_station else '—'}",
        f"*Дата записи:* {order.scheduled_date or '—'}",
        f"*Время:* {order.scheduled_time or '—'}",
        "",
        f"*Статус:* {_STATUS_RU.get(order.status, order.status)}",
    ]
    if order.upgrade_category:
        lines.append(f"*Категория апгрейда:* {e(order.upgrade_category)}")
    if order.diagnostics_price:
        incl = (
            " (входит в стоимость)"
            if svc and svc.diagnostics_included
            else " (оплачивается отдельно)"
        )
        lines.append(f"*Диагностика:* {order.diagnostics_price:.0f} ₽{incl}")
    if order.payment_id:
        lines.append(f"*ID платежа:* `{order.payment_id}`")
    if order.problem_description:
        lines.append(f"*Описание проблемы:* {e(order.problem_description)}")
    return "\n".join(lines)


async def _get_orders(
    page: int,
    status_filter: str | None = None,
) -> tuple[list[Order], int]:
    """Возвращает (заявки на странице, total_pages)."""
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
    total_pages = max(1, math.ceil(total / ADMIN_PAGE_SIZE))
    return list(orders), total_pages


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


# ══════════════════════════════════════════════════════════════
# Вход в панель
# ══════════════════════════════════════════════════════════════


@router.message(F.text == "Панель администратора")
async def admin_enter(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(Order))
        ).scalar_one()
        stats_rows = await session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
    stat_lines = [f"*Всего заявок:* {total}", ""]
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
        stats_rows = await session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
    stat_lines = [f"*Всего заявок:* {total}", ""]
    for status, cnt in stats_rows:
        stat_lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await cb.message.edit_text("\n".join(stat_lines), reply_markup=admin_main_kb())
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Выход из панели
# ══════════════════════════════════════════════════════════════


@router.message(F.text == "Выйти из панели")
async def admin_exit(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Вы вышли из панели администратора.",
        reply_markup=main_menu_kb(is_admin=True),
    )


# ══════════════════════════════════════════════════════════════
# Фильтр по статусу
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:filter")
async def adm_filter(cb: types.CallbackQuery) -> None:
    await cb.message.edit_text(
        "Выберите статус для фильтрации:", reply_markup=admin_filter_kb()
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

    orders, total_pages = await _get_orders(page, status_filter)
    await state.update_data(orders_page=page, orders_status_filter=status_filter)
    await state.set_state(AdminFSM.orders_list)

    if not orders:
        await cb.message.edit_text(
            "Заявки не найдены.",
            reply_markup=admin_main_kb(),
        )
        await cb.answer()
        return

    header = (
        "Все заявки"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    await cb.message.edit_text(
        f"*{header}* (стр. {page + 1}/{total_pages})",
        reply_markup=admin_orders_kb(orders, page, total_pages, status_filter),
    )
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
        _fmt_order(order), reply_markup=admin_order_detail_kb(order_id)
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
        _fmt_order(order), reply_markup=admin_order_detail_kb(order_id)
    )


# ══════════════════════════════════════════════════════════════
# Назад к списку из детального просмотра
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:back_list")
async def adm_back_to_list(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("orders_page", 0)
    status_filter = data.get("orders_status_filter")
    orders, total_pages = await _get_orders(page, status_filter)

    if not orders:
        await cb.message.edit_text("Заявки не найдены.", reply_markup=admin_main_kb())
        await cb.answer()
        return

    header = (
        "Все заявки"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    await cb.message.edit_text(
        f"*{header}* (стр. {page + 1}/{total_pages})",
        reply_markup=admin_orders_kb(orders, page, total_pages, status_filter),
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# No-op кнопка (номер страницы)
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "adm:noop")
async def adm_noop(cb: types.CallbackQuery) -> None:
    await cb.answer()
