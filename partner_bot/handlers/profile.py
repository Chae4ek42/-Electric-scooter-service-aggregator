"""Partner bot: profile editing & quick status toggle."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from bot.core.config import ADMIN_USERNAMES
from bot.core.database import async_session
from bot.domain.models import Service
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
from bot.texts import Btn, Partner, PARTNER_MENU_TEXTS
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


_PARTNER_MENU_TEXTS = PARTNER_MENU_TEXTS


async def _require_active(event) -> Service | None:
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if not owner or owner.status != "активный":
        text = "Вы не зарегистрированы или не одобрены."
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
    return owner


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


@router.message(F.text == Btn.EDIT_PROFILE)
async def edit_profile(message: types.Message, state: FSMContext) -> None:
    svc = await _require_active(message)
    if not svc:
        return
    await state.clear()
    await message.answer(_format_profile(svc), reply_markup=profile_edit_fields_kb())


# ── Select field to edit ──────────────────────────────────────


@router.callback_query(F.data.startswith("pedit:"))
async def select_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    svc = await _require_active(callback)
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
    except Exception as exc:
        await message.answer(f"Ошибка: {exc}\nПопробуйте ещё раз:")
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

        if field == "name":
            svc.name = text
            svc.draft_name = text
        elif field == "address":
            svc.address = text
            svc.draft_address = text
        elif field == "phone":
            svc.phone = text
            svc.draft_phone = text
        elif field == "telegram":
            svc.telegram_handle = text
            svc.draft_telegram = f"@{text.lstrip('@')}"
        elif field == "hours":
            parts = text.split("-")
            svc.open_time = parts[0].strip()
            svc.close_time = parts[1].strip()
            svc.draft_open_time = parts[0].strip()
            svc.draft_close_time = parts[1].strip()
        elif field == "diagnostics":
            svc.diagnostics_price = float(text)
            svc.draft_diagnostics_price = float(text)
        elif field == "metro":
            svc.nearest_metro = text
            svc.draft_metro = text
        elif field == "hydro_price":
            svc.hydroisolation_price = text
            svc.draft_hydro_price = text
        elif field == "bank_account":
            svc.draft_bank_account = text
        elif field == "bank_name":
            svc.draft_bank_name = text
        elif field == "bik":
            svc.draft_bik = text
        elif field == "corr_account":
            svc.draft_corr_account = text
        elif field == "org_name":
            svc.draft_org_name = text
        elif field == "inn":
            svc.draft_inn = text

        if needs_remod:
            svc.is_available = False
            svc.status = "ожидает"
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


# ── Combined status toggle (merged "Мой статус" + "Открыт / Закрыт") ───


@router.message(F.text == Btn.SERVICE_STATUS)
async def status_toggle_menu(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer(Partner.PROCEDURE_INTERRUPTED)
    svc = await _require_active(message)
    if not svc:
        return
    import datetime, zoneinfo

    _msk = zoneinfo.ZoneInfo("Europe/Moscow")
    now = datetime.datetime.now(tz=_msk)

    if svc.is_available:
        status_text = "🟢 Открыт"
        pause_info = ""
    else:
        status_text = "🔴 Закрыт"
        if svc.pause_until:
            pause_dt = svc.pause_until.astimezone(_msk)
            pause_info = f"\nОткроется: {pause_dt.strftime('%d.%m.%Y %H:%M')}"
        else:
            pause_info = "\nОткроется: вручную"

    await message.answer(
        f"<b>Статус сервиса:</b> {status_text}{pause_info}\n\n"
        "ℹ️ Эта опция показывает, доступен ли ваш сервис для клиентов. "
        "Если вам нужно временно приостановить приём заявок "
        "(отпуск, непредвиденные обстоятельства и т.д.), "
        "выберите длительность паузы ниже.",
        reply_markup=quick_status_kb(),
    )


# ── Quick status toggle (inline fallback) ─────────────────────


@router.callback_query(F.data == "pedit:status")
async def show_status_toggle(callback: types.CallbackQuery) -> None:
    svc = await _require_active(callback)
    if not svc:
        return
    import datetime, zoneinfo

    _msk = zoneinfo.ZoneInfo("Europe/Moscow")

    if svc.is_available:
        status_text = "🟢 Открыт"
        pause_info = ""
    else:
        status_text = "🔴 Закрыт"
        if svc.pause_until:
            pause_dt = svc.pause_until.astimezone(_msk)
            pause_info = f"\nОткроется: {pause_dt.strftime('%d.%m.%Y %H:%M')}"
        else:
            pause_info = "\nОткроется: вручную"

    await callback.message.edit_text(
        f"<b>Статус сервиса:</b> {status_text}{pause_info}",
        reply_markup=quick_status_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pstatus:"))
async def toggle_status(callback: types.CallbackQuery) -> None:
    svc = await _require_active(callback)
    if not svc:
        return

    action = callback.data.split(":")[1]
    import datetime, zoneinfo

    _msk = zoneinfo.ZoneInfo("Europe/Moscow")
    now = datetime.datetime.now(tz=_msk)

    if action == "open":
        new_val = True
        pause_until = None
        status_text = "🟢 Открыт"
    elif action == "pause_today":
        new_val = False
        # Закрыть до конца текущего дня (23:59 МСК)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        pause_until = end_of_day
        status_text = f"🔴 Закрыт до {end_of_day.strftime('%d.%m %H:%M')}"
    elif action == "pause_week":
        new_val = False
        # Закрыть до конца недели (воскресенье 23:59 МСК)
        days_until_sunday = 6 - now.weekday()
        if days_until_sunday <= 0:
            days_until_sunday = 7
        end_of_week = (now + datetime.timedelta(days=days_until_sunday)).replace(
            hour=23, minute=59, second=59, microsecond=0
        )
        pause_until = end_of_week
        status_text = f"🔴 Закрыт до {end_of_week.strftime('%d.%m %H:%M')}"
    else:  # close — пока не открою
        new_val = False
        pause_until = None
        status_text = "🔴 Закрыт (до ручного открытия)"

    async with async_session() as session:
        svc_db = (
            await session.execute(select(Service).where(Service.id == svc.id))
        ).scalar_one_or_none()
        if svc_db:
            svc_db.is_available = new_val
            svc_db.pause_until = pause_until
            await session.commit()

    try:
        set_service_available(svc.name, new_val)
    except Exception:
        logger.exception("Sheets status update failed")

    logger.info(
        "partner %s set status=%s pause_until=%s for service %s",
        callback.from_user.id,
        "open" if new_val else "closed",
        pause_until,
        svc.id,
    )
    await callback.answer(f"Статус: {status_text}", show_alert=True)
    await callback.message.edit_text(
        f"<b>Статус сервиса:</b> {status_text}",
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
