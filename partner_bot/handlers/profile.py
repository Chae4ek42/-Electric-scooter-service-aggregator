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
    BankAccountInput,
    BankNameInput,
    BikInput,
    CorrAccountInput,
    DiagnosticsPriceInput,
    InnInput,
    OrgNameInput,
    PhoneInput,
    ServiceNameInput,
    TelegramHandleInput,
    WorkHoursInput,
)
from bot.domain.states import PartnerProfileFSM
from bot.services.sheets_writer import set_service_available, update_service_row
from bot.core.formatting import e
from partner_bot.handlers.common import _get_owner, _sort_days, _TYPE_RU
from partner_bot.ui.keyboards import (
    partner_main_menu_kb,
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
    "metro": "Ближайшее метро",
    "hydro_price": "Цена гидроизоляции (число или диапазон, напр. 1000 или 1000-2000)",
    "bank_account": "Расчётный счёт (20 цифр)",
    "bank_name": "Название банка",
    "bik": "БИК (9 цифр)",
    "corr_account": "Корр. счёт (20 цифр)",
    "org_name": "Название организации",
    "inn": "ИНН (10 или 12 цифр)",
}

# Fields that do NOT require re-moderation
_NO_REMOD_FIELDS = {
    "diagnostics",
    "hydro_price",
    "bank_account",
    "bank_name",
    "bik",
    "corr_account",
    "org_name",
    "inn",
}


_PARTNER_MENU_TEXTS = (
    "Входящие заявки",
    "История заявок",
    "Редактировать профиль",
    "Настройки уведомлений",
    "Мой статус",
    "Поддержка",
    "Открыт / Закрыт",
    "Панель администратора",
    "Моя анкета",
    "Продолжить заполнение",
    "Изменить анкету",
)


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
    # e is imported from bot.core.formatting
    wd = _sort_days(svc.working_days)
    lines = [
        "Профиль сервисного центра:",
        "",
        f"Название: {e(svc.name)}",
        f"Тип: {_TYPE_RU.get(svc.service_type, svc.service_type)}",
        f"Адрес: {e(svc.address or '-')}",
        f"Метро: {e(svc.nearest_metro or '-')}",
        f"Телефон: {e(svc.phone or '-')}",
        f"Telegram: {e(svc.telegram_handle or '-')}",
        f"Рабочие дни: {e(wd or '-')}",
        f"Время: {svc.open_time or '?'}\u2014{svc.close_time or '?'}",
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
    if field in ("status", "back"):
        return  # handled by dedicated handlers
    label = _FIELD_LABELS.get(field, field)
    await state.set_state(PartnerProfileFSM.edit_field_value)
    await state.update_data(edit_field=field, edit_service_id=svc.id)
    if field in _NO_REMOD_FIELDS:
        prompt = f"Введите новое значение для поля '{label}':"
    else:
        prompt = (
            f"⚠️ При изменении этого поля сервис будет деактивирован "
            f"до повторной модерации.\n\n"
            f"Введите новое значение для поля '{label}':"
        )
    await callback.message.answer(prompt)
    await callback.answer()


# ── Accept new value ──────────────────────────────────────────


@router.message(PartnerProfileFSM.edit_field_value, F.text)
async def accept_field_value(message: types.Message, state: FSMContext) -> None:
    if message.text in _PARTNER_MENU_TEXTS:
        await state.clear()
        await message.answer("Процедура прервана.")
        return

    data = await state.get_data()
    field = data.get("edit_field")
    service_id = data.get("edit_service_id")
    if not field or not service_id:
        await state.clear()
        return

    text = message.text.strip()

    # Validate
    import re as _re

    try:
        if field == "name":
            ServiceNameInput(text=text)
        elif field == "address":
            AddressInput(text=text)
        elif field == "phone":
            PhoneInput(text=text)
        elif field == "telegram":
            TelegramHandleInput(text=text)
        elif field == "hours":
            WorkHoursInput(text=text)
        elif field == "diagnostics":
            DiagnosticsPriceInput(text=text)
        elif field == "metro":
            if len(text) < 2:
                raise ValueError("Минимум 2 символа")
        elif field == "hydro_price":
            if not _re.match(r"^\d+(-\d+)?$", text):
                raise ValueError(
                    "Формат: число или диапазон (напр. 1000 или 1000-2000)"
                )
        elif field == "bank_account":
            BankAccountInput(text=text)
        elif field == "bank_name":
            BankNameInput(text=text)
        elif field == "bik":
            BikInput(text=text)
        elif field == "corr_account":
            CorrAccountInput(text=text)
        elif field == "org_name":
            OrgNameInput(text=text)
        elif field == "inn":
            InnInput(text=text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}\nПопробуйте ещё раз:")
        return

    needs_remod = field not in _NO_REMOD_FIELDS

    # Update DB
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
        if not svc:
            await message.answer("Сервис не найден.")
            await state.clear()
            return
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.service_id == service_id)
            )
        ).scalar_one_or_none()

        if field == "name":
            svc.name = text
            if owner:
                owner.draft_name = text
        elif field == "address":
            svc.address = text
            if owner:
                owner.draft_address = text
        elif field == "phone":
            svc.phone = text
            if owner:
                owner.draft_phone = text
        elif field == "telegram":
            svc.telegram_handle = text
            if owner:
                owner.draft_telegram = f"@{text.lstrip('@')}"
        elif field == "hours":
            parts = text.split("-")
            svc.open_time = parts[0].strip()
            svc.close_time = parts[1].strip()
            if owner:
                owner.draft_open_time = parts[0].strip()
                owner.draft_close_time = parts[1].strip()
        elif field == "diagnostics":
            svc.diagnostics_price = float(text)
            if owner:
                owner.draft_diagnostics_price = float(text)
        elif field == "metro":
            svc.nearest_metro = text
            if owner:
                owner.draft_metro = text
        elif field == "hydro_price":
            svc.hydroisolation_price = text
            if owner:
                owner.draft_hydro_price = text
        elif field == "bank_account":
            if owner:
                owner.draft_bank_account = text
        elif field == "bank_name":
            if owner:
                owner.draft_bank_name = text
        elif field == "bik":
            if owner:
                owner.draft_bik = text
        elif field == "corr_account":
            if owner:
                owner.draft_corr_account = text
        elif field == "org_name":
            if owner:
                owner.draft_org_name = text
        elif field == "inn":
            if owner:
                owner.draft_inn = text

        if needs_remod:
            svc.is_available = False
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
        "partner %s updated field '%s' for service %s (remod=%s)",
        message.from_user.id,
        field,
        service_id,
        needs_remod,
    )

    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES
    if needs_remod:
        await message.answer(
            "Поле обновлено. Ваш сервис деактивирован и отправлен "
            "на повторную модерацию. Ожидайте одобрения.",
            reply_markup=partner_pending_menu_kb(has_draft=False, is_admin=is_admin),
        )
    else:
        await message.answer(
            "Поле обновлено.",
            reply_markup=partner_main_menu_kb(is_admin=is_admin),
        )


# ── Quick status toggle (text menu button) ────────────────────


@router.message(F.text == "Открыт / Закрыт")
async def status_toggle_menu(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("Процедура прервана.")
    owner, svc = await _require_active(message)
    if not svc:
        return
    await message.answer(
        f"Текущий статус: {'Открыт' if svc.is_available else 'Закрыт'}",
        reply_markup=quick_status_kb(),
    )


# ── Quick status toggle (inline fallback) ─────────────────────


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
