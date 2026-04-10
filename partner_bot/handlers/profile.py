"""Partner bot: profile editing & quick status toggle."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from bot.core.config import ADMIN_USERNAMES
from bot.core.database import async_session
from bot.domain.models import Service, ServiceOwner
from bot.domain.schemas import (
    AddressInput,
    DiagnosticsPriceInput,
    PhoneInput,
    ServiceNameInput,
    TelegramHandleInput,
    WorkHoursInput,
)
from bot.domain.states import PartnerProfileFSM
from bot.services.sheets_writer import set_service_available, update_service_row
from partner_bot.handlers.common import _get_owner
from partner_bot.ui.keyboards import (
    partner_pending_menu_kb,
    profile_edit_fields_kb,
    quick_status_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_profile")

_FIELD_LABELS = {
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
    "telegram": "Telegram",
    "hours": "Время работы (ЧЧ:ММ-ЧЧ:ММ)",
    "diagnostics": "Стоимость диагностики (руб.)",
}


async def _require_active(event) -> tuple[ServiceOwner | None, Service | None]:
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if not owner or owner.status != "активный" or not owner.service_id:
        text = "Вы не зарегистрированы или не одобрены."
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None, None
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == owner.service_id))
        ).scalar_one_or_none()
    return owner, svc


def _format_profile(svc: Service) -> str:
    lines = [
        "Профиль сервисного центра:",
        "",
        f"Название: {svc.name}",
        f"Тип: {svc.service_type}",
        f"Адрес: {svc.address or '-'}",
        f"Метро: {svc.nearest_metro or '-'}",
        f"Телефон: {svc.phone or '-'}",
        f"Telegram: {svc.telegram_handle or '-'}",
        f"Время: {svc.open_time or '?'}-{svc.close_time or '?'}",
        f"Диагностика: {int(svc.diagnostics_price) if svc.diagnostics_price else 0} руб.",
        f"Доступен: {'Да' if svc.is_available else 'Нет'}",
    ]
    return "\n".join(lines)


# ── Show profile ──────────────────────────────────────────────


@router.message(F.text == "Редактировать профиль")
async def edit_profile(message: types.Message, state: FSMContext) -> None:
    owner, svc = await _require_active(message)
    if not svc:
        return
    await state.clear()
    await message.answer(_format_profile(svc), reply_markup=profile_edit_fields_kb())


# ── Select field to edit ──────────────────────────────────────


@router.callback_query(F.data.startswith("pedit:"))
async def select_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner, svc = await _require_active(callback)
    if not svc:
        return
    field = callback.data.split(":")[1]
    label = _FIELD_LABELS.get(field, field)
    await state.set_state(PartnerProfileFSM.edit_field_value)
    await state.update_data(edit_field=field, edit_service_id=svc.id)
    await callback.message.answer(
        f"⚠️ При изменении профиля сервис будет деактивирован "
        f"до повторной модерации.\n\n"
        f"Введите новое значение для поля '{label}':"
    )
    await callback.answer()


# ── Accept new value ──────────────────────────────────────────


@router.message(PartnerProfileFSM.edit_field_value, F.text)
async def accept_field_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("edit_field")
    service_id = data.get("edit_service_id")
    if not field or not service_id:
        await state.clear()
        return

    text = message.text.strip()

    # Validate
    try:
        if field == "name":
            ServiceNameInput(name=text)
        elif field == "address":
            AddressInput(address=text)
        elif field == "phone":
            PhoneInput(phone=text)
        elif field == "telegram":
            TelegramHandleInput(handle=text)
        elif field == "hours":
            WorkHoursInput(hours=text)
        elif field == "diagnostics":
            DiagnosticsPriceInput(price=int(text))
    except Exception as e:
        await message.answer(f"Ошибка: {e}\nПопробуйте ещё раз:")
        return

    # Update DB
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
        if not svc:
            await message.answer("Сервис не найден.")
            await state.clear()
            return
        if field == "name":
            svc.name = text
        elif field == "address":
            svc.address = text
        elif field == "phone":
            svc.phone = text
        elif field == "telegram":
            svc.telegram_handle = text
        elif field == "hours":
            parts = text.split("-")
            svc.open_time = parts[0].strip()
            svc.close_time = parts[1].strip()
        elif field == "diagnostics":
            svc.diagnostics_price = float(text)

        # Deactivate service and send for re-moderation
        svc.is_available = False
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.service_id == service_id)
            )
        ).scalar_one_or_none()
        if owner:
            owner.status = "ожидает"
        await session.commit()

    # Write back to Sheets
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
    if svc:
        try:
            update_service_row(svc)
        except Exception:
            logger.exception("Sheets write-back failed")

    await state.clear()
    logger.info(
        "partner %s updated field '%s' for service %s — sent for re-moderation",
        message.from_user.id,
        field,
        service_id,
    )

    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES
    await message.answer(
        "Поле обновлено. Ваш сервис деактивирован и отправлен "
        "на повторную модерацию. Ожидайте одобрения.",
        reply_markup=partner_pending_menu_kb(has_draft=False, is_admin=is_admin),
    )


# ── Quick status toggle ───────────────────────────────────────


@router.callback_query(F.data == "pedit:status")
async def show_status_toggle(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not svc:
        return
    await callback.message.edit_text(
        f"Текущий статус: {'Открыт' if svc.is_available else 'Закрыт'}",
        reply_markup=quick_status_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pstatus:"))
async def toggle_status(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not svc:
        return
    new_val = callback.data.split(":")[1] == "open"
    async with async_session() as session:
        svc_db = (
            await session.execute(select(Service).where(Service.id == svc.id))
        ).scalar_one_or_none()
        if svc_db:
            svc_db.is_available = new_val
            await session.commit()

    try:
        set_service_available(svc.name, new_val)
    except Exception:
        logger.exception("Sheets status update failed")

    status_text = "Открыт" if new_val else "Закрыт"
    logger.info(
        "partner %s set status=%s for service %s",
        callback.from_user.id,
        status_text,
        svc.id,
    )
    await callback.answer(f"Статус: {status_text}", show_alert=True)
    await callback.message.edit_text(
        f"Текущий статус: {status_text}",
        reply_markup=quick_status_kb(),
    )


@router.callback_query(F.data == "pedit:back")
async def back_to_profile(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner, svc = await _require_active(callback)
    if not svc:
        return
    await state.clear()
    await callback.message.edit_text(
        _format_profile(svc), reply_markup=profile_edit_fields_kb()
    )
    await callback.answer()
