"""Partner bot: profile editing and quick status toggle."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
import zoneinfo

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.core.database import async_session
from client_bot.core.formatting import e
from client_bot.core.resilience import create_guarded_task
from client_bot.services.admin_notifications import notify_admins
from client_bot.services.notification_settings import ADMIN_SCOPE_PARTNER
from client_bot.domain.models import (
    MetroStation,
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
)
from client_bot.domain.order_rules import parse_hydro_price_range
from client_bot.domain.schemas import (
    AddressInput,
    BankAccountInput,
    BankNameInput,
    BikInput,
    CityInput,
    CorrAccountInput,
    DiagnosticsPriceInput,
    InnInput,
    MetroTextInput,
    OrgNameInput,
    PhoneInput,
    ServiceNameInput,
    WorkHoursInput,
)
from client_bot.domain.states import PartnerProfileFSM
from client_bot.services.city_search import is_moscow_city
from client_bot.services.geocoder import geocode_with_fallback
from client_bot.services.metro_search import best_metro_match
from client_bot.services.sheets_writer import (
    set_service_available,
    update_service_bank_row,
    update_service_row,
)
from client_bot.texts import Btn, Partner, PARTNER_MENU_TEXTS
from partner_bot.handlers.common import _TYPE_RU, _format_draft, _get_owner, _sort_days
from partner_bot.ui.keyboards import (
    bank_edit_fields_kb,
    draft_edit_kb,
    hydro_toggle_kb,
    partner_main_menu_kb,
    profile_edit_fields_kb,
    quick_status_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_profile")

_FIELD_LABELS = {
    "city": "Город",
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
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

_PARTNER_MENU_TEXTS = PARTNER_MENU_TEXTS
_ADDRESS_AND_METRO_HINT = (
    "Уточните адрес: улица, дом, корпус/строение "
    "(например: ул. Ленина, 15 к2). Для Москвы также проверьте метро."
)
try:
    _MSK = zoneinfo.ZoneInfo("Europe/Moscow")
except Exception:
    # Fallback for environments without system tz database / tzdata package.
    _MSK = datetime.timezone(datetime.timedelta(hours=3), name="MSK")


async def _get_owner_and_service(
    tg_id: int,
) -> tuple[ServiceDraft | None, Service | None]:
    owner = await _get_owner(tg_id)
    if not owner or owner.service_id is None:
        return owner, None
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == owner.service_id))
        ).scalar_one_or_none()
    return owner, svc


async def _sync_owner_draft_from_service(owner_user_id: int, service_id: int) -> None:
    """Keep editable draft snapshot aligned with active service profile."""
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one_or_none()
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
        if owner is None or svc is None:
            return

        category_name: str | None = None
        if svc.category_id is not None:
            category_name = (
                await session.execute(
                    select(ServiceCategory.name).where(
                        ServiceCategory.id == svc.category_id
                    )
                )
            ).scalar_one_or_none()

        owner.service_id = svc.id
        owner.status = "активный"
        owner.registration_complete = True
        owner.draft_name = svc.name
        owner.draft_service_type = svc.service_type
        owner.draft_category = category_name
        owner.draft_city = svc.city
        owner.draft_address = svc.address
        owner.draft_latitude = svc.latitude
        owner.draft_longitude = svc.longitude
        owner.draft_metro = svc.nearest_metro
        owner.draft_phone = svc.phone
        owner.draft_telegram = svc.telegram_handle
        owner.draft_open_time = svc.open_time
        owner.draft_close_time = svc.close_time
        owner.draft_hydroisolation = bool(svc.has_hydroisolation)
        owner.draft_hydro_price = svc.hydroisolation_price
        owner.draft_diagnostics_price = svc.diagnostics_price
        owner.draft_diag_included = bool(svc.diagnostics_included)
        owner.draft_upgrade_categories = svc.upgrade_categories
        owner.draft_working_days = svc.working_days
        await session.commit()


async def _notify_admins_about_profile_update(
    event: types.Message | types.CallbackQuery,
    *,
    service_id: int,
    field_label: str,
) -> None:
    actor = event.from_user
    actor_username = actor.username or ""
    actor_name = actor.full_name or ""

    try:
        await notify_admins(
            event.bot,
            scope=ADMIN_SCOPE_PARTNER,
            event_key="profile_update",
            text=(
                "Партнёр обновил профиль\n"
                f"Поле: {field_label}\n"
                f"Service ID: {service_id}\n"
                f"Partner TG ID: {actor.id}\n"
                f"Username: @{actor_username if actor_username else '—'}\n"
                f"Имя: {actor_name or '—'}"
            ),
            dedupe_prefix=f"partner_profile_update:{service_id}:{field_label}",
        )
    except Exception:
        logger.exception("Failed to notify admins about profile update")


async def _require_active(event) -> tuple[ServiceDraft | None, Service | None]:
    owner, svc = await _get_owner_and_service(event.from_user.id)
    if not owner or owner.status != "активный" or svc is None:
        text = "Вы не зарегистрированы или не одобрены."
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None, None
    return owner, svc


def _schedule_sheets_call(operation: str, fn, *args) -> None:
    """Run blocking Sheets call in background to keep UI responsive."""

    async def _run() -> None:
        try:
            await asyncio.to_thread(fn, *args)
        except Exception:
            logger.exception("Sheets async call failed (%s)", operation)

    create_guarded_task(
        _run(),
        logger=logger,
        task_name=f"partner_profile_sheet_call:{operation}",
        action_type="partner_profile_sheet_error",
        payload=operation,
    )


async def _geocode_service_location(
    city: str | None,
    address: str | None,
    metro: str | None,
) -> tuple[float, float] | None:
    if not city or not address:
        return None
    return await geocode_with_fallback(
        city=city,
        address=address,
        metro=metro if is_moscow_city(city) else None,
    )


def _profile_edit_kb(city: str | None):
    return profile_edit_fields_kb(show_metro=is_moscow_city(city or "Москва"))


def _format_profile(svc: Service) -> str:
    wd = _sort_days(svc.working_days)
    lines = [
        "<b>Профиль сервисного центра:</b>",
        "",
        f"<b>Город:</b> {e(svc.city or '-')}",
        f"<b>Название:</b> {e(svc.name)}",
        f"<b>Тип:</b> {_TYPE_RU.get(svc.service_type, svc.service_type)}",
        f"<b>Категория ремонта:</b> {e(svc.category_rel.name if svc.category_rel else '-')}",
        f"<b>Категории апгрейда:</b> {e((svc.upgrade_categories or '-').replace(',', ', '))}",
        f"<b>Адрес:</b> {e(svc.address or '-')}",
        f"<b>Телефон:</b> {e(svc.phone or '-')}",
        f"<b>Telegram:</b> {e(svc.telegram_handle or '-')}",
        f"<b>Рабочие дни:</b> {e(wd or '-')}",
        f"<b>Время:</b> {svc.open_time or '?'}-{svc.close_time or '?'}",
        f"<b>Гидроизоляция:</b> {'Да' if svc.has_hydroisolation else 'Нет'}",
        f"<b>Цена гидроизоляции:</b> {e(svc.hydroisolation_price or '-')}",
        f"<b>Диагностика:</b> {int(svc.diagnostics_price) if svc.diagnostics_price else 0} руб.",
        f"<b>Входит в стоимость:</b> {'Да' if svc.diagnostics_included else 'Нет'}",
        f"<b>Доступен:</b> {'Да' if svc.is_available else 'Нет'}",
    ]
    if is_moscow_city(svc.city):
        lines.insert(7, f"<b>Метро:</b> {e(svc.nearest_metro or '-')}")
    else:
        lines.insert(7, "<b>Метро:</b> не используется")
    return "\n".join(lines)


def _format_bank_details(bank: ServiceBankDetails | None) -> str:
    if bank is None:
        return "\n".join(
            [
                "Банковские реквизиты:",
                "",
                "Форма: —",
                "Налогообложение: —",
                "Расч. счёт: —",
                "Банк: —",
                "БИК: —",
                "Корр. счёт: —",
                "Организация: —",
                "ИНН: —",
            ]
        )
    return "\n".join(
        [
            "Банковские реквизиты:",
            "",
            f"Форма: {e(bank.legal_form or '—')}",
            f"Налогообложение: {e(bank.tax_system or '—')}",
            f"Расч. счёт: {e(bank.bank_account or '—')}",
            f"Банк: {e(bank.bank_name or '—')}",
            f"БИК: {e(bank.bik or '—')}",
            f"Корр. счёт: {e(bank.corr_account or '—')}",
            f"Организация: {e(bank.org_name or '—')}",
            f"ИНН: {e(bank.inn or '—')}",
        ]
    )


async def _load_bank_details(service_id: int) -> ServiceBankDetails | None:
    async with async_session() as session:
        return (
            await session.execute(
                select(ServiceBankDetails).where(
                    ServiceBankDetails.service_id == service_id
                )
            )
        ).scalar_one_or_none()


async def _upsert_bank_field(
    owner_user_id: int,
    service_id: int,
    *,
    legal_form: str | None = None,
    tax_system: str | None = None,
    bank_account: str | None = None,
    bank_name: str | None = None,
    bik: str | None = None,
    corr_account: str | None = None,
    org_name: str | None = None,
    inn: str | None = None,
) -> None:
    async with async_session() as session:
        bank = (
            await session.execute(
                select(ServiceBankDetails).where(
                    ServiceBankDetails.service_id == service_id
                )
            )
        ).scalar_one_or_none()
        if bank is None:
            bank = ServiceBankDetails(service_id=service_id)
            session.add(bank)

        if legal_form is not None:
            bank.legal_form = legal_form
        if tax_system is not None:
            bank.tax_system = tax_system
        if bank_account is not None:
            bank.bank_account = bank_account
        if bank_name is not None:
            bank.bank_name = bank_name
        if bik is not None:
            bank.bik = bik
        if corr_account is not None:
            bank.corr_account = corr_account
        if org_name is not None:
            bank.org_name = org_name
        if inn is not None:
            bank.inn = inn

        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one_or_none()
        if owner:
            if legal_form is not None:
                owner.draft_legal_form = legal_form
            if tax_system is not None:
                owner.draft_tax_system = tax_system
            if bank_account is not None:
                owner.draft_bank_account = bank_account
            if bank_name is not None:
                owner.draft_bank_name = bank_name
            if bik is not None:
                owner.draft_bik = bik
            if corr_account is not None:
                owner.draft_corr_account = corr_account
            if org_name is not None:
                owner.draft_org_name = org_name
            if inn is not None:
                owner.draft_inn = inn

        await session.commit()


@router.message(F.text == Btn.EDIT_PROFILE)
async def edit_profile(message: types.Message, state: FSMContext) -> None:
    owner, svc = await _require_active(message)
    if not owner or not svc:
        return
    await _sync_owner_draft_from_service(owner.owner_user_id, svc.id)
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Анкета не найдена.")
        return
    await state.clear()
    owner_city = (
        getattr(owner, "draft_city", None) or getattr(svc, "city", None) or "Москва"
    )
    await message.answer(
        _format_draft(owner) + "\n\nВыберите поле для изменения:",
        reply_markup=draft_edit_kb(owner.draft_service_type, city=owner_city),
    )


@router.message(F.text == Btn.BANK_DETAILS)
async def show_bank_details(message: types.Message, state: FSMContext) -> None:
    owner, svc = await _require_active(message)
    if not owner or not svc:
        return
    await state.clear()
    bank = await _load_bank_details(svc.id)
    await message.answer(_format_bank_details(bank), reply_markup=bank_edit_fields_kb())


@router.callback_query(F.data == "bedit:legal_form")
async def bedit_select_legal_form(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ИП", callback_data="bedit_legal:ИП")],
            [
                InlineKeyboardButton(
                    text="Юр. лицо", callback_data="bedit_legal:Юр. лицо"
                )
            ],
        ]
    )
    await callback.message.answer("Выберите правовую форму:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("bedit_legal:"))
async def bedit_save_legal_form(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    value = callback.data.split(":", 1)[1]
    await _upsert_bank_field(owner.owner_user_id, svc.id, legal_form=value)
    bank = await _load_bank_details(svc.id)
    _schedule_sheets_call("bank row (legal form)", update_service_bank_row, svc.id)
    await _notify_admins_about_profile_update(
        callback,
        service_id=svc.id,
        field_label="Форма",
    )
    await callback.message.answer(
        _format_bank_details(bank),
        reply_markup=bank_edit_fields_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "bedit:tax_system")
async def bedit_select_tax_system(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ОСНО", callback_data="bedit_tax:ОСНО")],
            [InlineKeyboardButton(text="УСН", callback_data="bedit_tax:УСН")],
            [InlineKeyboardButton(text="АУСН", callback_data="bedit_tax:АУСН")],
            [
                InlineKeyboardButton(
                    text="Патентная система", callback_data="bedit_tax:Патент"
                )
            ],
            [InlineKeyboardButton(text="НПД", callback_data="bedit_tax:НПД")],
        ]
    )
    await callback.message.answer("Выберите систему налогообложения:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("bedit_tax:"))
async def bedit_save_tax_system(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    value = callback.data.split(":", 1)[1]
    await _upsert_bank_field(owner.owner_user_id, svc.id, tax_system=value)
    bank = await _load_bank_details(svc.id)
    _schedule_sheets_call("bank row (tax system)", update_service_bank_row, svc.id)
    await _notify_admins_about_profile_update(
        callback,
        service_id=svc.id,
        field_label="Налогообложение",
    )
    await callback.message.answer(
        _format_bank_details(bank),
        reply_markup=bank_edit_fields_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bedit:"))
async def bedit_select_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    field = callback.data.split(":")[1]
    label = _FIELD_LABELS.get(field, field)
    await state.set_state(PartnerProfileFSM.edit_field_value)
    await state.update_data(
        edit_field=field, edit_service_id=svc.id, edit_section="bank"
    )
    await callback.message.answer(f"Введите новое значение для поля {label}:")
    await callback.answer()


@router.callback_query(F.data.startswith("pedit:hydro:"))
async def profile_toggle_hydro(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    val = callback.data.split(":")[2] == "yes"
    need_hydro_price = False
    async with async_session() as session:
        db_svc = (
            await session.execute(select(Service).where(Service.id == svc.id))
        ).scalar_one_or_none()
        db_owner = (
            await session.execute(
                select(ServiceDraft).where(
                    ServiceDraft.owner_user_id == owner.owner_user_id
                )
            )
        ).scalar_one_or_none()
        if db_svc:
            db_svc.has_hydroisolation = val
            if val and not (db_svc.hydroisolation_price or "").strip():
                need_hydro_price = True
            if not val:
                db_svc.hydroisolation_price = None
        if db_owner:
            db_owner.draft_hydroisolation = val
            if val and not (db_owner.draft_hydro_price or "").strip():
                need_hydro_price = True
            if not val:
                db_owner.draft_hydro_price = None
        await session.commit()

    await _notify_admins_about_profile_update(
        callback,
        service_id=svc.id,
        field_label="Гидроизоляция",
    )

    if need_hydro_price:
        await state.set_state(PartnerProfileFSM.edit_field_value)
        await state.update_data(edit_field="hydro_price", edit_service_id=svc.id)
        await callback.message.answer(
            "Гидроизоляция включена. Укажите стоимость (число или диапазон, напр. 1000 или 1000-2000):"
        )
        await callback.answer()
        return

    _, updated = await _get_owner_and_service(callback.from_user.id)
    if updated:
        _schedule_sheets_call("service row (hydro toggle)", update_service_row, updated)

    await callback.message.answer(
        _format_profile(updated) if updated else "Профиль обновлён.",
        reply_markup=_profile_edit_kb(updated.city if updated else svc.city),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pedit:"))
async def select_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    field = callback.data.split(":")[1]
    if field in ("status", "back"):
        return
    if field == "telegram":
        await callback.answer(
            "Поле Telegram недоступно для редактирования.", show_alert=True
        )
        return
    if field == "metro" and not is_moscow_city((svc.city or "Москва")):
        await callback.answer(
            "Для выбранного города поле метро не используется.",
            show_alert=True,
        )
        return
    if field == "hydro":
        await callback.message.answer(
            f"Гидроизоляция сейчас: {'Да' if svc.has_hydroisolation else 'Нет'}. Изменить?",
            reply_markup=hydro_toggle_kb(),
        )
        await callback.answer()
        return

    label = _FIELD_LABELS.get(field, field)
    await state.set_state(PartnerProfileFSM.edit_field_value)
    await state.update_data(edit_field=field, edit_service_id=svc.id)

    prompt = f"Введите новое значение для поля {label}:"
    await callback.message.answer(prompt)
    await callback.answer()


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

    try:
        if field == "name":
            ServiceNameInput(text=text)
        elif field == "city":
            CityInput(text=text)
        elif field == "address":
            AddressInput(text=text)
        elif field == "phone":
            PhoneInput(text=text)
        elif field == "telegram":
            raise ValueError("Поле Telegram недоступно для редактирования")
        elif field == "hours":
            WorkHoursInput(text=text)
        elif field == "diagnostics":
            DiagnosticsPriceInput(text=text)
        elif field == "metro":
            MetroTextInput(text=text)
            async with async_session() as session:
                stations = (await session.execute(select(MetroStation))).scalars().all()
            match = best_metro_match(text, stations)
            if match is None:
                raise ValueError("Станция не найдена. Укажите название точнее")
            text = match.name
        elif field == "hydro_price":
            if not re.match(r"^\d+(-\d+)?$", text):
                raise ValueError(
                    "Формат: число или диапазон (напр. 1000 или 1000-2000)"
                )
            low, high = parse_hydro_price_range(text)
            text = f"{low:.0f}" if low == high else f"{low:.0f}-{high:.0f}"
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

    owner, svc = await _require_active(message)
    if not owner or not svc:
        await state.clear()
        return

    bank_updates: dict[str, str] = {
        "bank_account": "bank_account",
        "bank_name": "bank_name",
        "bik": "bik",
        "corr_account": "corr_account",
        "org_name": "org_name",
        "inn": "inn",
    }
    if field in bank_updates:
        kwargs = {bank_updates[field]: text}
        await _upsert_bank_field(owner.owner_user_id, service_id, **kwargs)
        await state.clear()
        _schedule_sheets_call(
            "bank row (bank profile update)", update_service_bank_row, service_id
        )
        await _notify_admins_about_profile_update(
            message,
            service_id=service_id,
            field_label=_FIELD_LABELS.get(field, str(field)),
        )
        bank = await _load_bank_details(service_id)
        await message.answer(
            _format_bank_details(bank),
            reply_markup=bank_edit_fields_kb(),
        )
        return

    location_changed = field in {"city", "address"}
    new_city = svc.city or "Москва"
    new_address = svc.address
    new_metro = svc.nearest_metro

    if field == "city":
        new_city = text
        if not is_moscow_city(new_city):
            new_metro = None
    elif field == "address":
        new_address = text
    elif field == "metro":
        if not is_moscow_city((svc.city or "Москва")):
            await message.answer("Для выбранного города поле метро не используется.")
            return
        new_metro = text

    coords: tuple[float, float] | None = None
    if location_changed:
        if not new_city or not new_address:
            await message.answer("Сначала заполните город и адрес сервиса.")
            return
        can_skip_geocode = (
            field == "city" and is_moscow_city(new_city) and not new_metro
        )
        if is_moscow_city(new_city) and not new_metro and not can_skip_geocode:
            await message.answer(
                "Для Москвы необходимо указать метро, чтобы обновить координаты."
            )
            return
        if not can_skip_geocode:
            coords = await _geocode_service_location(new_city, new_address, new_metro)
            if coords is None:
                await message.answer(
                    "Не удалось определить координаты. " f"{_ADDRESS_AND_METRO_HINT}"
                )
                return

    async with async_session() as session:
        db_svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one_or_none()
        db_owner = (
            await session.execute(
                select(ServiceDraft).where(
                    ServiceDraft.owner_user_id == owner.owner_user_id
                )
            )
        ).scalar_one_or_none()

        if not db_svc or not db_owner:
            await message.answer("Сервис не найден.")
            await state.clear()
            return

        if field == "name":
            db_svc.name = text
            db_owner.draft_name = text
        elif field == "city":
            db_svc.city = text
            db_owner.draft_city = text
            if not is_moscow_city(text):
                db_svc.nearest_metro = None
                db_owner.draft_metro = None
        elif field == "address":
            db_svc.address = text
            db_owner.draft_address = text
        elif field == "phone":
            db_svc.phone = text
            db_owner.draft_phone = text
        elif field == "hours":
            hours = WorkHoursInput(text=text).text
            parts = re.split(r"\s*[-–]\s*", hours)
            db_svc.open_time = parts[0]
            db_svc.close_time = parts[1]
            db_owner.draft_open_time = parts[0]
            db_owner.draft_close_time = parts[1]
        elif field == "diagnostics":
            value = float(text)
            db_svc.diagnostics_price = value
            db_owner.draft_diagnostics_price = value
        elif field == "metro":
            db_svc.nearest_metro = text
            db_owner.draft_metro = text
        elif field == "hydro_price":
            db_svc.hydroisolation_price = text
            db_svc.has_hydroisolation = True
            db_owner.draft_hydro_price = text
            db_owner.draft_hydroisolation = True

        if location_changed and coords is not None:
            db_svc.latitude = coords[0]
            db_svc.longitude = coords[1]
            db_owner.draft_latitude = coords[0]
            db_owner.draft_longitude = coords[1]
        elif field == "city" and is_moscow_city(text) and not db_svc.nearest_metro:
            db_svc.latitude = None
            db_svc.longitude = None
            db_owner.draft_latitude = None
            db_owner.draft_longitude = None

        db_svc.partnership_status = "активный"
        db_owner.status = "активный"

        await session.commit()

    _, updated_svc = await _get_owner_and_service(message.from_user.id)
    if updated_svc:
        _schedule_sheets_call(
            "service row (profile update)", update_service_row, updated_svc
        )

    if data.get("edit_section") == "bank":
        _schedule_sheets_call(
            "bank row (profile update)", update_service_bank_row, service_id
        )

    await _notify_admins_about_profile_update(
        message,
        service_id=service_id,
        field_label=_FIELD_LABELS.get(field, str(field)),
    )

    await state.clear()

    if data.get("edit_section") == "bank":
        bank = await _load_bank_details(service_id)
        await message.answer(
            _format_bank_details(bank),
            reply_markup=bank_edit_fields_kb(),
        )
        return

    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES
    await message.answer(
        "Поле обновлено.",
        reply_markup=partner_main_menu_kb(is_admin=is_admin),
    )


@router.message(F.text == Btn.SERVICE_STATUS)
async def status_toggle_menu(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer(Partner.PROCEDURE_INTERRUPTED)

    owner, svc = await _require_active(message)
    if not owner or not svc:
        return

    now = datetime.datetime.now(tz=_MSK)
    if svc.is_available:
        status_text = "🟢 Открыт"
        pause_info = ""
    else:
        status_text = "🔴 Закрыт"
        if svc.pause_until:
            pause_dt = svc.pause_until.astimezone(_MSK)
            pause_info = f"\nОткроется: {pause_dt.strftime('%d.%m.%Y %H:%M')}"
        else:
            pause_info = "\nОткроется: вручную"

    await message.answer(
        f"<b>Статус сервиса:</b> {status_text}{pause_info}\n\n"
        "ℹ️ Эта опция показывает, доступен ли ваш сервис для клиентов. "
        "Если нужно временно приостановить приём заявок, выберите длительность паузы ниже.",
        reply_markup=quick_status_kb(),
    )


@router.callback_query(F.data == "pedit:status")
async def show_status_toggle(callback: types.CallbackQuery) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return

    if svc.is_available:
        status_text = "🟢 Открыт"
        pause_info = ""
    else:
        status_text = "🔴 Закрыт"
        if svc.pause_until:
            pause_dt = svc.pause_until.astimezone(_MSK)
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
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return

    action = callback.data.split(":")[1]
    now = datetime.datetime.now(tz=_MSK)

    if action == "open":
        new_val = True
        pause_until = None
        status_text = "🟢 Открыт"
    elif action == "pause_today":
        new_val = False
        pause_until = now.replace(hour=23, minute=59, second=59, microsecond=0)
        status_text = f"🔴 Закрыт до {pause_until.strftime('%d.%m %H:%M')}"
    elif action == "pause_week":
        new_val = False
        days_until_sunday = 6 - now.weekday()
        if days_until_sunday <= 0:
            days_until_sunday = 7
        pause_until = (now + datetime.timedelta(days=days_until_sunday)).replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )
        status_text = f"🔴 Закрыт до {pause_until.strftime('%d.%m %H:%M')}"
    else:
        new_val = False
        pause_until = None
        status_text = "🔴 Закрыт (до ручного открытия)"

    async with async_session() as session:
        db_svc = (
            await session.execute(select(Service).where(Service.id == svc.id))
        ).scalar_one_or_none()
        if db_svc:
            db_svc.is_available = new_val
            db_svc.pause_until = pause_until
            await session.commit()

    _schedule_sheets_call(
        "service availability",
        set_service_available,
        svc.id,
        new_val,
    )

    await callback.answer(f"Статус: {status_text}", show_alert=True)
    await callback.message.edit_text(
        f"<b>Статус сервиса:</b> {status_text}",
        reply_markup=quick_status_kb(),
    )


@router.callback_query(F.data == "pedit:back")
async def back_to_profile(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner, svc = await _require_active(callback)
    if not owner or not svc:
        return
    await state.clear()
    await callback.message.edit_text(
        _format_profile(svc),
        reply_markup=_profile_edit_kb(svc.city),
    )
    await callback.answer()
