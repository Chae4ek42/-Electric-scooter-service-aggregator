"""Partner bot: order management."""

from __future__ import annotations

import datetime
import logging
import math

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import func, select

from client_bot.core.database import async_session
from client_bot.core.formatting import e
from client_bot.domain.models import Order, ServiceDraft
from client_bot.services.city_search import is_moscow_city
from client_bot.services.notifications import send_by_token
from client_bot.services.order_lifecycle import ACTOR_PARTNER, transition_order_status
from client_bot.services.payment_policy import PaymentPolicy
from client_bot.domain.order_rules import (
    ZERO_MONEY,
    money,
    money_to_float,
    parse_hydro_price_range,
)
from client_bot.domain.schemas import RejectReasonInput
from client_bot.domain.states import PartnerOrderFSM
from partner_bot.handlers.common import _get_owner
from partner_bot.ui.keyboards import (
    partner_order_detail_kb,
    partner_orders_list_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_orders")

PAGE_SIZE = 10


def _model_name(order: Order) -> str:
    """Helper: human-readable model name from order."""
    if order.brand_custom_name:
        return f"{order.brand_custom_name} {order.model_custom_name or ''}".strip()
    if order.model_custom_name:
        return order.model_custom_name
    if order.model:
        return (
            f"{order.model.brand.name} {order.model.name}"
            if order.model.brand
            else order.model.name
        )
    return "—"


def _display_order_code(order: Order) -> str:
    code = (order.order_code or "").strip()
    return code or f"{order.id:06d}"


_STATUS_RU = {
    "awaiting_payment": "Ожидает оплаты",
    "paid": "Оплачено",
    "accepted": "Принята",
    "in_progress": "В работе",
    "ready_for_pickup": "Готов к выдаче",
    "completed": "Завершена",
    "cancelled": "Отменена",
    "rejected_by_partner": "Отклонена",
    "interrupted": "Не пришел",
    "client_refused": "Клиент отказался",
    "disputed": "Оспорена",
    "no_center": "Не найден центр",
}


def _fmt_partner_order(order: Order, show_client: bool = False) -> str:
    # Model name
    if order.brand_custom_name:
        model_str = e(
            f"{order.brand_custom_name} {order.model_custom_name or ''}".strip()
        )
    elif order.model_custom_name:
        model_str = e(order.model_custom_name)
    elif order.model:
        model_str = e(
            f"{order.model.brand.name} {order.model.name}"
            if order.model.brand
            else order.model.name
        )
    else:
        model_str = "—"

    stype = order.service.service_type if order.service else None
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплексный"}

    lines = [
        f"<b>Заявка #{order.id}</b>",
        f"<b>Код заявки:</b> {e(_display_order_code(order))}",
        f"<b>Устройство:</b> {model_str}",
        f"<b>Тип:</b> {type_map.get(stype, stype) if stype else '—'}",
        f"<b>Город:</b> {e(order.city or 'Москва')}",
    ]
    if is_moscow_city(order.city):
        lines.append(f"<b>Метро:</b> {e(order.metro_station or '—')}")
    else:
        lines.append(f"<b>Адрес клиента:</b> {e(order.client_address or '—')}")
    if order.upgrade_category:
        lines.append(f"<b>Категория:</b> {e(order.upgrade_category)}")
    if order.problem_description:
        lines.append(f"<b>Проблема:</b> {e(order.problem_description)}")
    lines.append(
        f"<b>Дата:</b> {order.scheduled_date or '—'} {order.scheduled_time or ''}"
    )
    lines.append(f"<b>Статус:</b> {_STATUS_RU.get(order.status, order.status)}")

    if show_client and order.user:
        u = order.user
        client_info = e(f"@{u.username}" if u.username else u.full_name)
        lines.append(f"<b>Клиент:</b> {client_info}")

    if order.total_cost is not None:
        lines.append(f"<b>Итоговая стоимость:</b> {order.total_cost:.0f} руб.")
    if order.estimate_cost is not None:
        lines.append(f"<b>Смета:</b> {order.estimate_cost:.0f} руб.")
    if order.estimate_items:
        lines.append(f"<b>Работы:</b> {e(order.estimate_items)}")
    if order.estimate_deadline:
        lines.append(f"<b>Срок:</b> {e(order.estimate_deadline)}")
    if order.estimate_description:
        lines.append(f"<b>Описание:</b> {e(order.estimate_description)}")
    if order.partner_comment:
        lines.append(f"<b>Комментарий:</b> {e(order.partner_comment)}")
    if order.reject_reason:
        lines.append(f"<b>Причина отказа:</b> {e(order.reject_reason)}")
    if order.refusal_reason:
        lines.append(f"<b>Причина отказа клиента:</b> {e(order.refusal_reason)}")
    if order.dispute_reason:
        lines.append(f"<b>Причина оспаривания:</b> {e(order.dispute_reason)}")
    if order.client_visited is not None:
        lines.append(
            f"<b>Клиент был в сервисе:</b> {'Да' if order.client_visited else 'Нет'}"
        )

    return "\n".join(lines)


def _build_client_order_notification(
    order: Order,
    title: str,
    extra_lines: list[str] | None = None,
) -> str:
    """Единый полный формат уведомлений клиенту по заявке."""
    stype = order.service.service_type if order.service else None
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплексный"}

    lines = [
        title,
        "",
        f"<b>Заявка #{order.id}</b>",
        f"<b>Код заказа:</b> {e(_display_order_code(order))}",
        "По прибытии в сервис назовите номер заказа.",
        f"<b>Статус:</b> {_STATUS_RU.get(order.status, order.status)}",
        f"<b>Устройство:</b> {e(_model_name(order))}",
        f"<b>Тип услуги:</b> {type_map.get(stype, stype) if stype else '—'}",
        f"<b>Дата:</b> {order.scheduled_date or '—'} {order.scheduled_time or ''}".rstrip(),
    ]

    if order.service:
        lines.append(f"<b>Сервис:</b> {e(order.service.name or '—')}")
        if order.service.address:
            lines.append(f"<b>Адрес:</b> {e(order.service.address)}")
        if order.service.phone:
            lines.append(f"<b>Телефон:</b> {e(order.service.phone)}")
        if order.service.telegram_handle:
            handle = order.service.telegram_handle
            if handle and not handle.startswith("@"):
                handle = f"@{handle}"
            lines.append(f"<b>Telegram:</b> {e(handle)}")

    if order.upgrade_category:
        lines.append(f"<b>Категория апгрейда:</b> {e(order.upgrade_category)}")
    if order.problem_description:
        lines.append(f"<b>Описание проблемы:</b> {e(order.problem_description)}")
    if order.diagnostics_price is not None:
        lines.append(f"<b>Диагностика:</b> {order.diagnostics_price:.0f} руб.")
    if order.estimate_cost is not None:
        lines.append(f"<b>Смета:</b> {order.estimate_cost:.0f} руб.")
    if order.estimate_items:
        lines.append(f"<b>Работы:</b> {e(order.estimate_items)}")
    if order.estimate_deadline:
        lines.append(f"<b>Срок:</b> {e(order.estimate_deadline)}")
    if order.estimate_description:
        lines.append(f"<b>Описание сметы:</b> {e(order.estimate_description)}")
    if order.total_cost is not None:
        lines.append(f"<b>Итоговая стоимость:</b> {order.total_cost:.0f} руб.")
    if order.reject_reason:
        lines.append(f"<b>Причина отказа:</b> {e(order.reject_reason)}")
    if order.refusal_reason:
        lines.append(f"<b>Причина отказа клиента:</b> {e(order.refusal_reason)}")
    if order.dispute_reason:
        lines.append(f"<b>Причина оспаривания:</b> {e(order.dispute_reason)}")
    if order.price_change_reason:
        lines.append(f"<b>Причина изменения цены:</b> {e(order.price_change_reason)}")

    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)

    return "\n".join(lines)


async def _get_partner_orders(
    service_id: int, page: int, status_filter: str | None = None
) -> tuple[list[Order], int]:
    async with async_session() as session:
        q = select(Order).where(Order.service_id == service_id)
        cq = (
            select(func.count())
            .select_from(Order)
            .where(Order.service_id == service_id)
        )
        if status_filter and status_filter != "all":
            q = q.where(Order.status == status_filter)
            cq = cq.where(Order.status == status_filter)
        total = (await session.execute(cq)).scalar_one()
        orders = (
            (
                await session.execute(
                    q.order_by(Order.created_at.desc())
                    .offset(page * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    return list(orders), total_pages


async def _require_active_owner(event) -> ServiceDraft | None:
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if not owner or owner.status != "активный" or owner.service_id is None:
        text = (
            "Вы не зарегистрированы или не одобрены."
            if not owner
            else "Ваш аккаунт не активен."
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
    return owner


# ── Incoming orders ───────────────────────────────────────────


@router.message(F.text == "Входящие заявки")
async def incoming_orders(message: types.Message, state: FSMContext) -> None:
    owner = await _require_active_owner(message)
    if not owner:
        return

    async with async_session() as session:
        orders = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.service_id == owner.service_id)
                    .where(Order.status.in_(["awaiting_payment", "paid"]))
                    .order_by(Order.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )

    if not orders:
        await message.answer("Нет входящих заявок.")
        return

    lines = [f"Входящих заявок: {len(orders)}", ""]
    for o in orders:
        if o.brand_custom_name:
            m = f"{o.brand_custom_name} {o.model_custom_name or ''}".strip()
        elif o.model and o.model.brand:
            m = f"{o.model.brand.name} {o.model.name}"
        else:
            m = o.model_custom_name or "-"
        stype = (
            "Ремонт" if o.service and o.service.service_type == "repair" else "Апгрейд"
        )
        if o.upgrade_category:
            stype = f"Апгрейд - {o.upgrade_category}"
        client_info = ""
        if o.user and o.user.username:
            client_info = f" | @{o.user.username}"
        lines.append(f"#{o.id}/{_display_order_code(o)} | {m} | {stype}")
        lines.append(
            f"  {o.scheduled_date or '?'} {o.scheduled_time or ''}{client_info}"
        )

    from partner_bot.ui.keyboards import partner_orders_list_kb

    kb = partner_orders_list_kb(orders, 0, 1)
    await message.answer("\n".join(lines), reply_markup=kb)


# ── Order detail ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:detail:"))
async def order_detail(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()

    if not order or order.service_id != owner.service_id:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    client_username = order.user.username if order.user else None
    show_client = True
    text = _fmt_partner_order(order, show_client=show_client)
    kb = partner_order_detail_kb(
        order_id, order.status, client_username if show_client else None
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


# ── Accept order ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:accept:"))
async def accept_order(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status not in ("awaiting_payment", "paid"):
            await callback.answer("Невозможно принять эту заявку.", show_alert=True)
            return
        await transition_order_status(
            session,
            order,
            "accepted",
            actor=ACTOR_PARTNER,
            reason="partner_accept",
        )
        order.accepted_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await session.commit()

    logger.info("partner %s accepted order #%s", callback.from_user.id, order_id)
    await callback.answer("Заявка принята", show_alert=True)

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "Ваша заявка принята сервисом",
            ),
            dedupe_key=f"client_accept:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about acceptance")

    # Refresh detail view
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        text = _fmt_partner_order(order, show_client=True)
        kb = partner_order_detail_kb(
            order_id, order.status, order.user.username if order.user else None
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass


# ── Reject order ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:reject:"))
async def reject_order_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])
    await state.update_data(reject_order_id=order_id)
    await state.set_state(PartnerOrderFSM.reject_reason)
    await callback.message.answer("Укажите причину отклонения:")
    await callback.answer()


@router.message(PartnerOrderFSM.reject_reason, F.text)
async def reject_order_reason(message: types.Message, state: FSMContext) -> None:
    try:
        v = RejectReasonInput(text=message.text)
    except ValidationError as e:
        raw = e.errors()[0]["msg"] if e.errors() else "Ошибка"
        if raw.startswith("Value error, "):
            raw = raw[len("Value error, ") :]
        await message.answer(raw)
        return

    data = await state.get_data()
    order_id = data.get("reject_order_id")
    if not order_id:
        await state.clear()
        return

    owner = await _require_active_owner(message)
    if not owner:
        await state.clear()
        return

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        await transition_order_status(
            session,
            order,
            "rejected_by_partner",
            actor=ACTOR_PARTNER,
            reason="partner_reject",
        )
        order_code = _display_order_code(order)
        order.reject_reason = v.text
        await session.commit()

    logger.info(
        "partner %s rejected order #%s: %s", message.from_user.id, order_id, v.text
    )
    await state.clear()
    await message.answer(f"Заявка #{order_id}/{order_code} отклонена.")

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "Ваша заявка была отклонена сервисом",
            ),
            dedupe_key=f"client_reject:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about rejection")


# ── Client refused ─────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:client_refused:"))
async def client_refused_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "accepted":
            await callback.answer("Невозможно.", show_alert=True)
            return
    await state.update_data(refused_order_id=order_id)
    await state.set_state(PartnerOrderFSM.client_refused_reason)
    await callback.message.answer("Укажите причину отказа клиента:")
    await callback.answer()


@router.message(PartnerOrderFSM.client_refused_reason, F.text)
async def client_refused_reason(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("Слишком короткая причина. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    order_id = data.get("refused_order_id")
    if not order_id:
        await state.clear()
        return

    owner = await _require_active_owner(message)
    if not owner:
        await state.clear()
        return

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        await transition_order_status(
            session,
            order,
            "client_refused",
            actor=ACTOR_PARTNER,
            reason="partner_mark_client_refused",
        )
        order_code = _display_order_code(order)
        order.refusal_reason = text
        order.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await session.commit()
        user_id = order.user_id
        svc_name = order.service.name if order.service else ""
        model_str = _model_name(order)

    logger.info(
        "partner %s: order #%s client_refused: %s",
        message.from_user.id,
        order_id,
        text,
    )
    await state.clear()
    await message.answer(f"Заявка #{order_id}/{order_code} — клиент отказался.")

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN
        from client_bot.ui.keyboards import client_visited_kb

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "❌ Сервис отметил, что вы отказались от ремонта",
            ),
            reply_markup=client_visited_kb(order_id),
            dedupe_key=f"client_refused_notice:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about refusal")


# ── Start work (estimate FSM) ────────────────────────────────


@router.callback_query(F.data.startswith("pord:start_work:"))
async def start_work(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "accepted":
            await callback.answer("Невозможно.", show_alert=True)
            return

    await state.update_data(estimate_order_id=order_id)
    await state.set_state(PartnerOrderFSM.estimate_cost)
    await callback.message.answer("Введите стоимость ремонта (число, руб.):")
    await callback.answer()


@router.message(PartnerOrderFSM.estimate_cost, F.text)
async def estimate_cost_input(message: types.Message, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", ".").replace(" ", "")
    try:
        cost = money(raw)
        if cost <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректное число (например: 3500):")
        return
    await state.update_data(est_cost=money_to_float(cost))
    await state.set_state(PartnerOrderFSM.estimate_items)
    await message.answer(
        "Укажите позиции ремонта (что будет чиниться):\n"
        "Например: Замена колеса, ремонт контроллера"
    )


@router.message(PartnerOrderFSM.estimate_items, F.text)
async def estimate_items_input(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("Слишком короткое описание. Попробуйте ещё раз:")
        return
    await state.update_data(est_items=text)
    await state.set_state(PartnerOrderFSM.estimate_deadline)
    await message.answer(
        "Укажите ожидаемое время завершения:\n" "Например: 2 дня или 15.04.2026"
    )


@router.message(PartnerOrderFSM.estimate_deadline, F.text)
async def estimate_deadline_input(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 1:
        await message.answer("Укажите срок:")
        return
    await state.update_data(est_deadline=text)
    await state.set_state(PartnerOrderFSM.estimate_description)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data="pord:estimate_skip_desc"
                )
            ]
        ]
    )
    await message.answer("Добавьте описание (опционально):", reply_markup=skip_kb)


@router.callback_query(
    PartnerOrderFSM.estimate_description, F.data == "pord:estimate_skip_desc"
)
async def estimate_skip_desc(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(est_description=None)
    await _show_estimate_confirm(callback.message, state)
    await callback.answer()


@router.message(PartnerOrderFSM.estimate_description, F.text)
async def estimate_desc_input(message: types.Message, state: FSMContext) -> None:
    await state.update_data(est_description=message.text.strip())
    await _show_estimate_confirm(message, state)


async def _show_estimate_confirm(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    cost = money(data["est_cost"])
    items = data["est_items"]
    deadline = data["est_deadline"]
    desc = data.get("est_description")
    lines = [
        "Подтвердите смету:",
        "",
        f"Стоимость: {cost:.0f} руб.",
        f"Работы: {items}",
        f"Срок: {deadline}",
    ]
    if desc:
        lines.append(f"Описание: {desc}")

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить", callback_data="pord:estimate_confirm"
                ),
                InlineKeyboardButton(text="Назад", callback_data="pord:estimate_back"),
            ]
        ]
    )
    await state.set_state(PartnerOrderFSM.estimate_confirm)
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(PartnerOrderFSM.estimate_confirm, F.data == "pord:estimate_back")
async def estimate_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PartnerOrderFSM.estimate_cost)
    await callback.message.answer("Введите стоимость ремонта (число, руб.):")
    await callback.answer()


@router.callback_query(
    PartnerOrderFSM.estimate_confirm, F.data == "pord:estimate_confirm"
)
async def estimate_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("estimate_order_id")
    if not order_id:
        await state.clear()
        await callback.answer("Ошибка.", show_alert=True)
        return

    owner = await _require_active_owner(callback)
    if not owner:
        await state.clear()
        return

    cost = money(data["est_cost"])
    items = data["est_items"]
    deadline = data["est_deadline"]
    desc = data.get("est_description")

    prepayment = ZERO_MONEY
    order_code = "000000"
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            await state.clear()
            return
        if order.status != "accepted":
            await callback.answer("Невозможно.", show_alert=True)
            await state.clear()
            return

        hydro_raw = order.service.hydroisolation_price if order.service else None
        if order.upgrade_category == "Гидроизоляция" and hydro_raw:
            try:
                low, high = parse_hydro_price_range(hydro_raw)
            except ValueError:
                await callback.answer(
                    "В профиле сервиса некорректно задана цена гидроизоляции.",
                    show_alert=True,
                )
                return
            valid, _ = PaymentPolicy.validate_hydro_total(cost, hydro_raw)
            if not valid:
                await callback.answer(
                    f"Цена должна быть в диапазоне {low:.0f}-{high:.0f} руб.",
                    show_alert=True,
                )
                return

        await transition_order_status(
            session,
            order,
            "in_progress",
            actor=ACTOR_PARTNER,
            reason="estimate_confirmed",
        )
        order.estimate_cost = money_to_float(cost)
        order.estimate_items = items
        order.estimate_deadline = deadline
        order.estimate_description = desc
        if order.total_cost is None:
            order.total_cost = money_to_float(cost)
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category,
            order.diagnostics_price,
        )
        await session.commit()
        user_id = order.user_id
        svc_name = order.service.name if order.service else ""
        model_str = _model_name(order)

    remainder = PaymentPolicy.remainder(
        cost,
        order.upgrade_category,
        order.diagnostics_price,
    )
    await state.clear()
    await callback.answer("Заявка принята в работу", show_alert=True)

    logger.info(
        "partner %s: order #%s in_progress with estimate",
        callback.from_user.id,
        order_id,
    )

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN
        from client_bot.ui.keyboards import client_confirm_estimate_kb

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "🔧 Заявка принята в работу",
                extra_lines=[
                    f"Предоплата: {prepayment:.0f} руб.",
                    f"Остаток: {remainder:.0f} руб.",
                ],
            ),
            reply_markup=client_confirm_estimate_kb(order_id),
            dedupe_key=f"client_estimate:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about estimate")

    # Refresh partner view
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        text = _fmt_partner_order(order, show_client=True)
        kb = partner_order_detail_kb(
            order_id, order.status, order.user.username if order.user else None
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass


# ── Ready for pickup ──────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:ready:"))
async def order_ready(callback: types.CallbackQuery) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])

    prepayment = ZERO_MONEY
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "in_progress":
            await callback.answer("Невозможно.", show_alert=True)
            return
        await transition_order_status(
            session,
            order,
            "ready_for_pickup",
            actor=ACTOR_PARTNER,
            reason="partner_ready_for_pickup",
        )
        await session.commit()
        user_id = order.user_id
        svc_name = order.service.name if order.service else ""
        svc_address = order.service.address if order.service else ""
        model_str = _model_name(order)
        total = money(order.total_cost or order.estimate_cost or 0)
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category,
            order.diagnostics_price,
        )

    remainder = PaymentPolicy.remainder(
        total,
        order.upgrade_category,
        order.diagnostics_price,
    )

    logger.info(
        "partner %s: order #%s ready_for_pickup", callback.from_user.id, order_id
    )
    await callback.answer("Готов к выдаче", show_alert=True)

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN
        from client_bot.ui.keyboards import client_ready_kb

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "✅ Заявка готова к выдаче",
                extra_lines=[
                    f"Предоплата: {prepayment:.0f} руб.",
                    f"К оплате: {remainder:.0f} руб.",
                ],
            ),
            reply_markup=client_ready_kb(order_id),
            dedupe_key=f"client_ready:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about ready")

    # Refresh partner view
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        text = _fmt_partner_order(order, show_client=True)
        kb = partner_order_detail_kb(
            order_id, order.status, order.user.username if order.user else None
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass


# ── Set total cost ────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:set_cost:"))
async def set_cost_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status not in ("accepted", "in_progress"):
            await callback.answer("Невозможно.", show_alert=True)
            return

    await state.update_data(cost_order_id=order_id)
    await state.set_state(PartnerOrderFSM.set_total_cost_items)
    await callback.message.answer("Укажите смету (что будет сделано):")
    await callback.answer()


@router.message(PartnerOrderFSM.set_total_cost_items, F.text)
async def set_cost_items_input(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("Смета слишком короткая. Опишите работы подробнее:")
        return

    await state.update_data(cost_items=text)
    await state.set_state(PartnerOrderFSM.set_total_cost)
    await message.answer("Введите итоговую стоимость ремонта (число, руб.):")


@router.message(PartnerOrderFSM.set_total_cost, F.text)
async def set_cost_value(message: types.Message, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", ".").replace(" ", "")
    try:
        cost = money(raw)
        if cost <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректное число (например: 3500):")
        return

    data = await state.get_data()
    order_id = data.get("cost_order_id")
    cost_items = (data.get("cost_items") or "").strip()
    if not order_id:
        await state.clear()
        return

    owner = await _require_active_owner(message)
    if not owner:
        await state.clear()
        return

    prepayment = ZERO_MONEY
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return

        hydro_raw = order.service.hydroisolation_price if order.service else None
        if order.upgrade_category == "Гидроизоляция" and hydro_raw:
            try:
                low, high = parse_hydro_price_range(hydro_raw)
            except ValueError:
                await message.answer(
                    "В профиле сервиса некорректно задана цена гидроизоляции."
                )
                await state.clear()
                return
            valid, _ = PaymentPolicy.validate_hydro_total(cost, hydro_raw)
            if not valid:
                await message.answer(
                    f"Итоговая сумма должна быть в диапазоне {low:.0f}-{high:.0f} руб."
                )
                return

        if order.status == "accepted":
            await transition_order_status(
                session,
                order,
                "in_progress",
                actor=ACTOR_PARTNER,
                reason="set_total_cost_auto_progress",
            )
        if order.status == "in_progress":
            await transition_order_status(
                session,
                order,
                "ready_for_pickup",
                actor=ACTOR_PARTNER,
                reason="set_total_cost_auto_ready",
            )

        order.total_cost = money_to_float(cost)
        if cost_items:
            order.estimate_items = cost_items
        if order.estimate_cost is None:
            order.estimate_cost = money_to_float(cost)
        order_code = _display_order_code(order)
        await session.commit()
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category,
            order.diagnostics_price,
        )
        order_upgrade_category = order.upgrade_category
        order_diagnostics_price = order.diagnostics_price

    remainder = PaymentPolicy.remainder(
        cost,
        order_upgrade_category,
        order_diagnostics_price,
    )
    await state.clear()
    await message.answer(
        f"Смета: {cost_items or '—'}\n"
        f"Итоговая стоимость заявки #{order_id}/{order_code}: {cost:.0f} руб.\n"
        f"Предоплата: {prepayment:.0f} руб.\n"
        f"Остаток к оплате: {remainder:.0f} руб.\n"
        "Заявка переведена в статус «Готов к выдаче»."
    )

    logger.info(
        "partner %s set total_cost=%s for order #%s",
        message.from_user.id,
        cost,
        order_id,
    )

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN
        from client_bot.ui.keyboards import client_ready_kb

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "💰 По вашей заявке обновлена итоговая стоимость",
                extra_lines=[
                    f"Предоплата: {prepayment:.0f} руб.",
                    f"Остаток к оплате: {remainder:.0f} руб.",
                    "Чтобы завершить заявку, нажмите «Оплатить и завершить».",
                ],
            ),
            reply_markup=client_ready_kb(order_id),
            dedupe_key=f"client_total_cost:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about total cost")


# ── Update price (in_progress) ────────────────────────────────


@router.callback_query(F.data.startswith("pord:update_price:"))
async def update_price_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "in_progress":
            await callback.answer(
                "Изменение цены доступно только для заявок в работе.", show_alert=True
            )
            return

    await state.update_data(update_price_order_id=order_id)
    await state.set_state(PartnerOrderFSM.update_price_cost)
    await callback.message.answer("Введите новую стоимость ремонта (число, руб.):")
    await callback.answer()


@router.message(PartnerOrderFSM.update_price_cost, F.text)
async def update_price_cost_input(message: types.Message, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", ".").replace(" ", "")
    try:
        new_cost = money(raw)
        if new_cost <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректное число (например: 4200):")
        return

    await state.update_data(update_price_new_cost=money_to_float(new_cost))
    await state.set_state(PartnerOrderFSM.update_price_reason)
    await message.answer("Укажите причину изменения цены (минимум 5 символов):")


@router.message(PartnerOrderFSM.update_price_reason, F.text)
async def update_price_reason_input(message: types.Message, state: FSMContext) -> None:
    reason = message.text.strip()
    if len(reason) < 5:
        await message.answer("Причина слишком короткая. Пожалуйста, опишите подробнее:")
        return

    data = await state.get_data()
    order_id = data.get("update_price_order_id")
    raw_new_cost = data.get("update_price_new_cost")
    if not order_id or raw_new_cost is None:
        await state.clear()
        return
    new_cost = money(raw_new_cost)

    owner = await _require_active_owner(message)
    if not owner:
        await state.clear()
        return

    old_cost: float | None = None
    user_id: int | None = None
    order_code = "000000"
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return

        hydro_raw = order.service.hydroisolation_price if order.service else None
        if order.upgrade_category == "Гидроизоляция" and hydro_raw:
            try:
                low, high = parse_hydro_price_range(hydro_raw)
            except ValueError:
                await message.answer(
                    "В профиле сервиса некорректно задана цена гидроизоляции."
                )
                await state.clear()
                return
            valid, _ = PaymentPolicy.validate_hydro_total(new_cost, hydro_raw)
            if not valid:
                await message.answer(
                    f"Новая цена должна быть в диапазоне {low:.0f}-{high:.0f} руб."
                )
                return

        old_cost = order.total_cost
        user_id = order.user_id
        order_code = _display_order_code(order)
        order.total_cost = money_to_float(new_cost)
        order.price_change_reason = reason
        order.price_updated_at = datetime.datetime.now(datetime.timezone.utc)
        await session.commit()

    await state.clear()
    old_str = f"{money(old_cost):.0f} руб." if old_cost is not None else "не указана"
    await message.answer(
        f"Цена заявки #{order_id}/{order_code} обновлена.\n"
        f"Было: {old_str} → Стало: {new_cost:.0f} руб."
    )
    logger.info(
        "partner %s updated price for order #%s: %s → %s (reason: %s)",
        message.from_user.id,
        order_id,
        old_cost,
        new_cost,
        reason,
    )

    # Notify client
    try:
        async with async_session() as session:
            notify_order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if not notify_order:
            raise RuntimeError("order_not_found_for_client_notify")

        from client_bot.core.config import CLIENT_BOT_TOKEN

        await send_by_token(
            CLIENT_BOT_TOKEN,
            notify_order.user_id,
            _build_client_order_notification(
                notify_order,
                "Сервис изменил стоимость вашей заявки",
                extra_lines=[
                    f"Было: {old_str}",
                    f"Стало: {new_cost:.0f} руб.",
                ],
            ),
            dedupe_key=f"client_price_update:{order_id}:{notify_order.user_id}",
        )
    except Exception:
        logger.exception("Failed to notify client about price update")


# ── History ───────────────────────────────────────────────────


@router.message(F.text == "История заявок")
async def orders_history(message: types.Message, state: FSMContext) -> None:
    owner = await _require_active_owner(message)
    if not owner:
        return
    await state.update_data(history_page=0, history_filter=None)
    await _show_history(message, owner.service_id, 0, None)


async def _show_history(
    event: types.Message | types.CallbackQuery,
    service_id: int,
    page: int,
    status_filter: str | None,
) -> None:
    orders, total_pages = await _get_partner_orders(service_id, page, status_filter)
    if not orders:
        text = "Заявок не найдено."
        if isinstance(event, types.CallbackQuery):
            try:
                await event.message.edit_text(text)
            except Exception:
                await event.message.answer(text)
            await event.answer()
        else:
            await event.answer(text)
        return

    lines = [f"Заявки (стр. {page + 1}/{total_pages}):", ""]
    for o in orders:
        status = _STATUS_RU.get(o.status, o.status)
        lines.append(
            f"#{o.id}/{_display_order_code(o)} | {o.scheduled_date or '?'} | {status}"
        )

    kb = partner_orders_list_kb(orders, page, total_pages)
    text = "\n".join(lines)
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except Exception:
            await event.message.answer(text, reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("pord:page:"))
async def orders_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    page = int(callback.data.split(":")[2])
    data = await state.get_data()
    await state.update_data(history_page=page)
    await _show_history(callback, owner.service_id, page, data.get("history_filter"))


@router.callback_query(F.data == "pord:back_list")
async def back_to_list(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    data = await state.get_data()
    page = data.get("history_page", 0)
    await _show_history(callback, owner.service_id, page, data.get("history_filter"))


@router.callback_query(F.data.startswith("pord:filter:"))
async def orders_filter(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _require_active_owner(callback)
    if not owner:
        return
    f = callback.data.split(":")[2]
    await state.update_data(history_filter=f if f != "all" else None, history_page=0)
    await _show_history(callback, owner.service_id, 0, f if f != "all" else None)


@router.callback_query(F.data == "noop")
async def noop(callback: types.CallbackQuery) -> None:
    await callback.answer()
