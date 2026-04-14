"""Partner bot: order management."""

from __future__ import annotations

import datetime
import logging
import math

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import func, select

from bot.core.database import async_session
from bot.domain.models import Order, Service, ServiceOwner
from bot.domain.schemas import RejectReasonInput
from bot.domain.states import PartnerOrderFSM
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
        model_str = f"{order.brand_custom_name} {order.model_custom_name or ''}".strip()
    elif order.model_custom_name:
        model_str = order.model_custom_name
    elif order.model:
        model_str = (
            f"{order.model.brand.name} {order.model.name}"
            if order.model.brand
            else order.model.name
        )
    else:
        model_str = "—"

    stype = order.service.service_type if order.service else None
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплексный"}

    lines = [
        f"Заявка #{order.id}",
        f"Устройство: {model_str}",
        f"Тип: {type_map.get(stype, stype) if stype else '—'}",
    ]
    if order.upgrade_category:
        lines.append(f"Категория: {order.upgrade_category}")
    if order.problem_description:
        lines.append(f"Проблема: {order.problem_description}")
    lines.append(f"Дата: {order.scheduled_date or '—'} {order.scheduled_time or ''}")
    lines.append(f"Статус: {_STATUS_RU.get(order.status, order.status)}")

    if show_client and order.user:
        u = order.user
        client_info = f"@{u.username}" if u.username else u.full_name
        lines.append(f"Клиент: {client_info}")

    if order.total_cost is not None:
        lines.append(f"Итоговая стоимость: {order.total_cost:.0f} руб.")
    if order.estimate_cost is not None:
        lines.append(f"Смета: {order.estimate_cost:.0f} руб.")
    if order.estimate_items:
        lines.append(f"Работы: {order.estimate_items}")
    if order.estimate_deadline:
        lines.append(f"Срок: {order.estimate_deadline}")
    if order.estimate_description:
        lines.append(f"Описание: {order.estimate_description}")
    if order.partner_comment:
        lines.append(f"Комментарий: {order.partner_comment}")
    if order.reject_reason:
        lines.append(f"Причина отказа: {order.reject_reason}")
    if order.refusal_reason:
        lines.append(f"Причина отказа клиента: {order.refusal_reason}")
    if order.dispute_reason:
        lines.append(f"Причина оспаривания: {order.dispute_reason}")
    if order.client_visited is not None:
        lines.append(f"Клиент был в сервисе: {'Да' if order.client_visited else 'Нет'}")

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


async def _require_active_owner(event) -> ServiceOwner | None:
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if not owner or owner.status != "активный" or not owner.service_id:
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
                    .where(Order.status == "awaiting_payment")
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
            stype = f"Апгрейд -- {o.upgrade_category}"
        lines.append(f"#{o.id} | {m} | {stype}")
        lines.append(f"  {o.scheduled_date or '?'} {o.scheduled_time or ''}")

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
    show_client = order.status not in ("awaiting_payment",)
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
        if order.status != "awaiting_payment":
            await callback.answer("Невозможно принять эту заявку.", show_alert=True)
            return
        order.status = "accepted"
        order.accepted_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await session.commit()

    logger.info("partner %s accepted order #%s", callback.from_user.id, order_id)
    await callback.answer("Заявка принята", show_alert=True)

    # Notify client
    try:
        svc_name = ""
        async with async_session() as session:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            svc_name = svc.name if svc else ""
            svc_address = svc.address if svc else ""
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            order.user_id,
            f"Ваша заявка #{order_id} принята сервисом {svc_name}.\n"
            f"Адрес: {svc_address}",
        )
        await client_bot.session.close()
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
        order.status = "rejected_by_partner"
        order.reject_reason = v.text
        await session.commit()

    logger.info(
        "partner %s rejected order #%s: %s", message.from_user.id, order_id, v.text
    )
    await state.clear()
    await message.answer(f"Заявка #{order_id} отклонена.")

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            order.user_id,
            f"Ваша заявка #{order_id} была отклонена сервисом.\n" f"Причина: {v.text}",
        )
        await client_bot.session.close()
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
        order.status = "client_refused"
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
    await message.answer(f"Заявка #{order_id} — клиент отказался.")

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot
        from bot.ui.keyboards import client_visited_kb

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            user_id,
            f"❌ Заявка #{order_id}\n\n"
            f"Сервис сообщил, что вы отказались от ремонта.\n"
            f"Причина: {text}\n\n"
            f"Устройство: {model_str}\n"
            f"Сервис: {svc_name}",
            reply_markup=client_visited_kb(order_id),
        )
        await client_bot.session.close()
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
        cost = float(raw)
        if cost <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректное число (например: 3500):")
        return
    await state.update_data(est_cost=cost)
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
    cost = data["est_cost"]
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

    cost = data["est_cost"]
    items = data["est_items"]
    deadline = data["est_deadline"]
    desc = data.get("est_description")

    prepayment: float = 0
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
        order.status = "in_progress"
        order.estimate_cost = cost
        order.estimate_items = items
        order.estimate_deadline = deadline
        order.estimate_description = desc
        if order.total_cost is None:
            order.total_cost = cost
        # Determine prepayment
        if order.upgrade_category == "Гидроизоляция":
            prepayment = 500.0
        elif order.diagnostics_price:
            prepayment = order.diagnostics_price
        await session.commit()
        user_id = order.user_id
        svc_name = order.service.name if order.service else ""
        model_str = _model_name(order)

    remainder = max(0.0, cost - prepayment)
    await state.clear()
    await callback.answer("Заявка принята в работу", show_alert=True)

    logger.info(
        "partner %s: order #%s in_progress with estimate",
        callback.from_user.id,
        order_id,
    )

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot
        from bot.ui.keyboards import client_confirm_estimate_kb

        client_bot = Bot(token=BOT_TOKEN)
        est_lines = [
            f"🔧 Заявка #{order_id} — принята в работу\n",
            f"Устройство: {model_str}",
            f"Сервис: {svc_name}\n",
            "Смета:",
            f"  Стоимость: {cost:.0f} руб.",
            f"  Работы: {items}",
            f"  Срок: {deadline}",
        ]
        if desc:
            est_lines.append(f"  Описание: {desc}")
        est_lines += [
            "",
            f"Предоплата: {prepayment:.0f} руб.",
            f"Остаток: {remainder:.0f} руб.",
        ]
        await client_bot.send_message(
            user_id,
            "\n".join(est_lines),
            reply_markup=client_confirm_estimate_kb(order_id),
        )
        await client_bot.session.close()
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

    prepayment: float = 0
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
        order.status = "ready_for_pickup"
        await session.commit()
        user_id = order.user_id
        svc_name = order.service.name if order.service else ""
        svc_address = order.service.address if order.service else ""
        model_str = _model_name(order)
        total = order.total_cost or order.estimate_cost or 0
        if order.upgrade_category == "Гидроизоляция":
            prepayment = 500.0
        elif order.diagnostics_price:
            prepayment = order.diagnostics_price

    remainder = max(0.0, total - prepayment)

    logger.info(
        "partner %s: order #%s ready_for_pickup", callback.from_user.id, order_id
    )
    await callback.answer("Готов к выдаче", show_alert=True)

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot
        from bot.ui.keyboards import client_ready_kb

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            user_id,
            f"✅ Заявка #{order_id} — готов к выдаче!\n\n"
            f"Устройство: {model_str}\n"
            f"Сервис: {svc_name}\n"
            f"Адрес: {svc_address}\n\n"
            f"Итоговая стоимость: {total:.0f} руб.\n"
            f"Предоплата: {prepayment:.0f} руб.\n"
            f"К оплате: {remainder:.0f} руб.",
            reply_markup=client_ready_kb(order_id),
        )
        await client_bot.session.close()
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
    await state.set_state(PartnerOrderFSM.set_total_cost)
    await callback.message.answer("Введите итоговую стоимость ремонта (число, руб.):")
    await callback.answer()


@router.message(PartnerOrderFSM.set_total_cost, F.text)
async def set_cost_value(message: types.Message, state: FSMContext) -> None:
    raw = message.text.strip().replace(",", ".").replace(" ", "")
    try:
        cost = float(raw)
        if cost <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректное число (например: 3500):")
        return

    data = await state.get_data()
    order_id = data.get("cost_order_id")
    if not order_id:
        await state.clear()
        return

    owner = await _require_active_owner(message)
    if not owner:
        await state.clear()
        return

    prepayment: float = 0
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.service_id != owner.service_id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        order.total_cost = cost
        await session.commit()

        # Determine prepayment
        if order.upgrade_category == "Гидроизоляция":
            prepayment = 500.0
        elif order.diagnostics_price:
            prepayment = order.diagnostics_price

    remainder = max(0.0, cost - prepayment)
    await state.clear()
    await message.answer(
        f"Итоговая стоимость заявки #{order_id}: {cost:.0f} руб.\n"
        f"Предоплата: {prepayment:.0f} руб.\n"
        f"Остаток к оплате: {remainder:.0f} руб."
    )

    logger.info(
        "partner %s set total_cost=%s for order #%s",
        message.from_user.id,
        cost,
        order_id,
    )

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            order.user_id,
            f"По вашей заявке #{order_id} определена итоговая стоимость: "
            f"{cost:.0f} руб.\n"
            f"Предоплата: {prepayment:.0f} руб.\n"
            f"Остаток к оплате: {remainder:.0f} руб.",
        )
        await client_bot.session.close()
    except Exception:
        logger.exception("Failed to notify client about total cost")


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
        lines.append(f"#{o.id} | {o.scheduled_date or '?'} | {status}")

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
