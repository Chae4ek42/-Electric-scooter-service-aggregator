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

_STATUS_RU = {
    "awaiting_payment": "Ожидает оплаты",
    "accepted": "Принята",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
    "rejected_by_partner": "Отклонена",
    "interrupted": "Не пришел",
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
        model_str = "-"

    stype = order.service.service_type if order.service else "-"
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплексный"}

    lines = [
        f"Заявка #{order.id}",
        f"Устройство: {model_str}",
        f"Тип: {type_map.get(stype, stype)}",
    ]
    if order.upgrade_category:
        lines.append(f"Категория: {order.upgrade_category}")
    if order.problem_description:
        lines.append(f"Проблема: {order.problem_description}")
    lines.append(f"Дата: {order.scheduled_date or '-'} {order.scheduled_time or ''}")
    lines.append(f"Статус: {_STATUS_RU.get(order.status, order.status)}")

    if show_client and order.user:
        u = order.user
        client_info = f"@{u.username}" if u.username else u.full_name
        lines.append(f"Клиент: {client_info}")

    if order.partner_comment:
        lines.append(f"Комментарий: {order.partner_comment}")
    if order.reject_reason:
        lines.append(f"Причина отказа: {order.reject_reason}")

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
    show_client = order.status in ("accepted", "in_progress", "completed")
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


# ── In progress ───────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:in_progress:"))
async def order_in_progress(callback: types.CallbackQuery) -> None:
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
        order.status = "in_progress"
        await session.commit()

    logger.info("partner %s: order #%s in_progress", callback.from_user.id, order_id)
    await callback.answer("Устройство принято в работу", show_alert=True)

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


# ── Complete ──────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pord:complete:"))
async def order_complete(callback: types.CallbackQuery) -> None:
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
            await callback.answer("Невозможно.", show_alert=True)
            return
        order.status = "completed"
        order.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await session.commit()

    logger.info("partner %s: order #%s completed", callback.from_user.id, order_id)
    await callback.answer("Заявка завершена", show_alert=True)

    # Notify client
    try:
        from bot.core.config import BOT_TOKEN
        from aiogram import Bot

        client_bot = Bot(token=BOT_TOKEN)
        await client_bot.send_message(
            order.user_id,
            f"Ваша заявка #{order_id} завершена. Спасибо за обращение!",
        )
        await client_bot.session.close()
    except Exception:
        logger.exception("Failed to notify client about completion")

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        text = _fmt_partner_order(order, show_client=True)
        kb = partner_order_detail_kb(order_id, order.status)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass


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
