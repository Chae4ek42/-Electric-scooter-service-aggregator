"""Partner bot: registration FSM."""

from __future__ import annotations

import asyncio
import logging
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from client_bot.core.database import async_session
from client_bot.core.resilience import create_guarded_task
from client_bot.services.admin_notifications import notify_admins
from client_bot.services.notification_settings import ADMIN_SCOPE_PARTNER
from client_bot.domain.models import (
    MetroStation,
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
    ServiceOwnerSettings,
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
    OrgNameInput,
    PhoneInput,
    ServiceNameInput,
    WorkHoursInput,
)
from client_bot.domain.states import RegistrationFSM
from client_bot.services.city_search import (
    city_candidates,
    is_moscow_city,
    top_city_matches,
)
from client_bot.services.geocoder import geocode_with_fallback
from client_bot.services.metro_search import top_metro_matches
from client_bot.services.sheets_writer import add_service_row, update_service_row
from client_bot.texts import TYPE_RU, Btn, PARTNER_MENU_TEXTS, Partner
from partner_bot.handlers.common import _draft_complete, _format_draft, _get_owner
from partner_bot.ui.keyboards import (
    BACK_BTN,
    draft_edit_kb,
    metro_confirm_kb,
    partner_pending_menu_kb,
    reg_back_kb,
    reg_category_kb,
    reg_confirm_kb,
    reg_diag_included_kb,
    reg_legal_form_kb,
    reg_service_type_kb,
    reg_skip_kb,
    reg_start_kb,
    reg_tax_system_kb,
    reg_upgrade_categories_kb,
    reg_city_confirm_kb,
    reg_working_days_kb,
    reg_yes_no_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_registration")

_PARTNER_MENU_TEXTS = PARTNER_MENU_TEXTS

_ADDRESS_PRECISION_HINT = (
    "Уточните адрес: улица, дом, корпус/строение " "(например: ул. Ленина, 15 к2)."
)

_ADDRESS_AND_METRO_HINT = (
    "Уточните адрес и метро. "
    "Для Москвы особенно важны корректные улица, дом и ближайшая станция."
)


def _pydantic_msg(exc: ValidationError) -> str:
    """Extract human-readable error message from Pydantic ValidationError."""
    raw = exc.errors()[0]["msg"] if exc.errors() else "Некорректный ввод"
    if raw.startswith("Value error, "):
        raw = raw[len("Value error, ") :]
    return raw


# ── Bank details sub-steps ────────────────────────────────────

_BANK_FIELDS = [
    ("draft_bank_account", "расчётный счёт", BankAccountInput),
    ("draft_bank_name", "название банка", BankNameInput),
    ("draft_bik", "БИК", BikInput),
    ("draft_corr_account", "корреспондентский счёт", CorrAccountInput),
    (
        "draft_org_name",
        "наименование организации\n(Пример: ИП Звездилин Сергей Леонидович)",
        OrgNameInput,
    ),
    ("draft_inn", "ИНН", InnInput),
]


async def _safe_edit_or_answer(
    event: types.Message | types.CallbackQuery,
    text: str,
    reply_markup=None,
) -> None:
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            await event.message.answer(text, reply_markup=reply_markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=reply_markup)


async def _handle_menu_interrupt(message: types.Message, state: FSMContext) -> bool:
    """Cancel FSM if user presses a menu button during text input."""
    if message.text not in _PARTNER_MENU_TEXTS:
        return False
    await state.clear()
    await message.answer("Процедура прервана.")
    return True


_STYPE_TEXT = Partner.Registration.STYPE_TEXT

_CATEGORY_TEXT = Partner.Registration.CATEGORY_TEXT


async def _ensure_owner(tg_id: int) -> ServiceDraft:
    async with async_session() as session:
        draft = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == tg_id)
            )
        ).scalar_one_or_none()
        if draft is None:
            import datetime

            draft = ServiceDraft(
                owner_user_id=tg_id,
                status="ожидает",
                registered_at=datetime.datetime.now(tz=datetime.timezone.utc),
            )
            session.add(draft)
            await session.commit()
            await session.refresh(draft)
        return draft


async def _update_draft(tg_id: int, **kwargs) -> None:
    async with async_session() as session:
        draft = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == tg_id)
            )
        ).scalar_one_or_none()
        if draft:
            for k, v in kwargs.items():
                setattr(draft, k, v)
            await session.commit()


async def _load_city_options() -> list[str]:
    async with async_session() as session:
        svc_cities = (
            (
                await session.execute(
                    select(Service.city).where(Service.city.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        draft_cities = (
            (
                await session.execute(
                    select(ServiceDraft.draft_city).where(
                        ServiceDraft.draft_city.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    merged = [city for city in [*svc_cities, *draft_cities] if city and city.strip()]
    return city_candidates(merged)


async def _geocode_draft_location(owner: ServiceDraft) -> tuple[float, float] | None:
    city = (owner.draft_city or "").strip()
    address = (owner.draft_address or "").strip()
    if not city or not address:
        return None
    metro = owner.draft_metro if is_moscow_city(city) else None
    return await geocode_with_fallback(
        city=city,
        address=address,
        metro=metro,
    )


_EDIT_FIELD_LABELS = {
    "city": "Город",
    "name": "Название",
    "service_type": "Тип услуг",
    "category": "Категория ремонта",
    "upgrade_cats": "Категории апгрейда",
    "hydro": "Гидроизоляция",
    "hydro_price": "Цена гидроизоляции",
    "address": "Адрес",
    "metro": "Метро",
    "phone": "Телефон",
    "working_days": "Рабочие дни",
    "hours": "Время работы",
    "diagnostics": "Диагностика",
    "diag_included": "Входит в стоимость",
}


def _schedule_service_sheet_sync(svc: Service) -> None:
    async def _run() -> None:
        try:
            await asyncio.to_thread(update_service_row, svc)
        except Exception:
            logger.exception("Sheets write-back failed after active profile edit")

    create_guarded_task(
        _run(),
        logger=logger,
        task_name=f"registration_sheet_sync:{svc.id}",
        action_type="registration_sheet_sync_error",
        payload=f"service_id={svc.id}",
    )


async def _sync_active_service_from_draft(owner_user_id: int) -> Service | None:
    async with async_session() as session:
        draft = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one_or_none()
        if draft is None or draft.service_id is None:
            return None

        svc = (
            await session.execute(select(Service).where(Service.id == draft.service_id))
        ).scalar_one_or_none()
        if svc is None:
            return None

        category_id: int | None = None
        if draft.draft_service_type in ("repair", "complex") and draft.draft_category:
            cat = (
                await session.execute(
                    select(ServiceCategory).where(
                        ServiceCategory.name == draft.draft_category
                    )
                )
            ).scalar_one_or_none()
            if cat is None:
                cat = ServiceCategory(name=draft.draft_category)
                session.add(cat)
                await session.flush()
            category_id = cat.id

        svc.name = draft.draft_name or svc.name
        svc.service_type = draft.draft_service_type or svc.service_type
        svc.category_id = category_id
        svc.city = draft.draft_city
        svc.address = draft.draft_address
        svc.latitude = draft.draft_latitude
        svc.longitude = draft.draft_longitude
        svc.nearest_metro = (
            draft.draft_metro if is_moscow_city(draft.draft_city) else None
        )
        svc.phone = draft.draft_phone
        svc.telegram_handle = draft.draft_telegram
        svc.open_time = draft.draft_open_time
        svc.close_time = draft.draft_close_time
        svc.has_hydroisolation = bool(draft.draft_hydroisolation)
        svc.hydroisolation_price = (
            draft.draft_hydro_price if draft.draft_hydroisolation else None
        )
        svc.diagnostics_price = draft.draft_diagnostics_price
        svc.diagnostics_included = bool(draft.draft_diag_included)
        svc.upgrade_categories = (
            draft.draft_upgrade_categories
            if svc.service_type in ("upgrade", "complex")
            else None
        )
        svc.working_days = draft.draft_working_days
        svc.partnership_status = "активный"
        svc.registration_complete = True

        draft.status = "активный"
        draft.registration_complete = True

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
                    owner_user_id=owner_user_id,
                )
            )
        else:
            settings.owner_user_id = owner_user_id

        await session.commit()

        return (
            await session.execute(
                select(Service)
                .options(selectinload(Service.category_rel))
                .where(Service.id == svc.id)
            )
        ).scalar_one_or_none()


async def _notify_admins_about_active_profile_edit(
    event: types.Message | types.CallbackQuery,
    *,
    service_id: int,
    field_key: str | None,
) -> None:
    field_label = _EDIT_FIELD_LABELS.get(field_key or "", field_key or "Профиль")
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
            dedupe_prefix=f"partner_profile_update:{service_id}:{field_key or 'profile'}",
        )
    except Exception:
        logger.exception("Failed to notify admins about active profile edit")


def _next_empty_state(owner: ServiceDraft) -> str | None:
    """Find next state that needs filling."""
    if not owner.draft_city:
        return RegistrationFSM.reg_city_search.state
    if not owner.draft_name:
        return RegistrationFSM.reg_name.state
    if not owner.draft_service_type:
        return RegistrationFSM.reg_service_type.state
    if owner.draft_service_type == "repair" and not owner.draft_category:
        return RegistrationFSM.reg_category.state
    if owner.draft_service_type == "upgrade" and not owner.draft_upgrade_categories:
        return RegistrationFSM.reg_upgrade_categories.state
    if owner.draft_service_type == "complex":
        if not owner.draft_category:
            return RegistrationFSM.reg_category.state
        if not owner.draft_upgrade_categories:
            return RegistrationFSM.reg_upgrade_categories.state
    if not owner.draft_address:
        return RegistrationFSM.reg_address.state
    if is_moscow_city(owner.draft_city) and not owner.draft_metro:
        return RegistrationFSM.reg_metro_search.state
    more = [
        ("draft_phone", RegistrationFSM.reg_phone.state),
        ("draft_working_days", RegistrationFSM.reg_working_days.state),
        ("draft_open_time", RegistrationFSM.reg_hours.state),
        ("draft_close_time", RegistrationFSM.reg_hours.state),
    ]
    for field, state in more:
        if not getattr(owner, field, None):
            return state
    if owner.draft_hydroisolation and not owner.draft_hydro_price:
        return RegistrationFSM.reg_hydro_price.state
    if owner.draft_diagnostics_price is None:
        return RegistrationFSM.reg_diagnostics.state
    return None


def _draft_started_edit_fields(owner: ServiceDraft) -> set[str]:
    """Return only fields that are already started/completed in pending draft."""
    started: set[str] = set()

    draft_city = getattr(owner, "draft_city", None)
    draft_name = getattr(owner, "draft_name", None)
    draft_service_type = getattr(owner, "draft_service_type", None)
    draft_category = getattr(owner, "draft_category", None)
    draft_upgrade_categories = getattr(owner, "draft_upgrade_categories", None)
    draft_hydroisolation = bool(getattr(owner, "draft_hydroisolation", False))
    draft_hydro_price = getattr(owner, "draft_hydro_price", None)
    draft_address = getattr(owner, "draft_address", None)
    draft_metro = getattr(owner, "draft_metro", None)
    draft_phone = getattr(owner, "draft_phone", None)
    draft_working_days = getattr(owner, "draft_working_days", None)
    draft_open_time = getattr(owner, "draft_open_time", None)
    draft_close_time = getattr(owner, "draft_close_time", None)
    draft_diagnostics_price = getattr(owner, "draft_diagnostics_price", None)

    if draft_city:
        started.add("city")
    if draft_name:
        started.add("name")
    if draft_service_type:
        started.add("service_type")

    stype = draft_service_type or ""
    if stype in ("repair", "complex") and draft_category:
        started.add("category")
    if stype in ("upgrade", "complex") and draft_upgrade_categories:
        started.add("upgrade_cats")

    hydro_step_reached = bool(
        stype
        and (
            draft_hydroisolation
            or draft_hydro_price
            or draft_address
            or draft_metro
            or draft_phone
            or draft_working_days
            or draft_open_time
            or draft_close_time
            or draft_diagnostics_price is not None
        )
    )
    if hydro_step_reached:
        started.add("hydro")
    if draft_hydro_price:
        started.add("hydro_price")
    if draft_address:
        started.add("address")
    if draft_metro and is_moscow_city(draft_city):
        started.add("metro")
    if draft_phone:
        started.add("phone")
    if draft_working_days:
        started.add("working_days")
    if draft_open_time and draft_close_time:
        started.add("hours")
    if draft_diagnostics_price is not None:
        started.add("diagnostics")
        started.add("diag_included")

    if not started:
        started.add("city")

    return started


# ── Start / continue / edit ───────────────────────────────────


@router.callback_query(F.data == "reg:start")
async def reg_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _ensure_owner(callback.from_user.id)
    await state.set_state(RegistrationFSM.reg_city_search)
    await _safe_edit_or_answer(
        callback, "Введите город сервисного центра:", reg_back_kb()
    )


@router.message(F.text == Btn.CONTINUE_DRAFT)
async def reg_continue(message: types.Message, state: FSMContext) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Начните регистрацию.", reply_markup=reg_start_kb())
        return
    next_state = _next_empty_state(owner)
    if next_state is None:
        await state.set_state(RegistrationFSM.reg_confirm)
        await message.answer(
            _format_draft(owner),
            reply_markup=reg_confirm_kb(has_bank=bool(owner.draft_bank_account)),
        )
        return

    selected_upgrade = set((owner.draft_upgrade_categories or "").split(",")) - {""}
    selected_days = set((owner.draft_working_days or "").split(",")) - {""}

    await state.set_state(next_state)
    prompts = {
        RegistrationFSM.reg_city_search.state: (
            "Введите город сервисного центра:",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_name.state: (
            "Введите название сервисного центра:",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_service_type.state: (
            _STYPE_TEXT,
            reg_service_type_kb(),
        ),
        RegistrationFSM.reg_category.state: (
            _CATEGORY_TEXT,
            reg_category_kb(),
        ),
        RegistrationFSM.reg_upgrade_categories.state: (
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(selected_upgrade),
        ),
        RegistrationFSM.reg_hydroisolation.state: (
            "Выполняете гидроизоляцию?",
            reg_yes_no_kb("reg_hydro"),
        ),
        RegistrationFSM.reg_hydro_price.state: (
            "Укажите стоимость гидроизоляции (число или диапазон, напр. 1000 или 1000-2000):",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_address.state: ("Введите адрес:", reg_back_kb()),
        RegistrationFSM.reg_metro_search.state: (
            "Введите ближайшую станцию метро:",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_phone.state: (
            "Введите контактный телефон:",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_working_days.state: (
            "Выберите рабочие дни:",
            reg_working_days_kb(selected_days),
        ),
        RegistrationFSM.reg_hours.state: (
            "Введите время работы (HH:MM-HH:MM):",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_diagnostics.state: (
            "Стоимость диагностики (руб.):",
            reg_back_kb(),
        ),
        RegistrationFSM.reg_diag_included.state: (
            "Диагностика входит в стоимость?",
            reg_diag_included_kb(),
        ),
        RegistrationFSM.reg_legal_form.state: (
            "Выберите орг.-правовую форму:",
            reg_legal_form_kb(),
        ),
        RegistrationFSM.reg_tax_system.state: (
            "Выберите систему налогообложения:",
            reg_tax_system_kb(),
        ),
        RegistrationFSM.reg_bank_details.state: (
            "⚠️ Банковские реквизиты\nПо этим реквизитам будут производиться выплаты.\n\nВведите расчётный счёт:",
            reg_back_kb(),
        ),
    }
    prompt_text, prompt_markup = prompts.get(
        next_state,
        ("Продолжите заполнение анкеты:", reg_back_kb()),
    )

    if next_state == RegistrationFSM.reg_bank_details.state:
        await state.update_data(bank_step=0)
    if next_state == RegistrationFSM.reg_upgrade_categories.state:
        await state.update_data(selected_upgrade_cats=list(selected_upgrade))
    if next_state == RegistrationFSM.reg_working_days.state:
        await state.update_data(selected_days=list(selected_days))

    await message.answer(
        _format_draft(owner)
        + "\n\n<b>Продолжаем заполнение анкеты</b>\n"
        + prompt_text,
        reply_markup=prompt_markup,
    )


@router.message(F.text == Btn.EDIT_DRAFT)
async def edit_draft(message: types.Message, state: FSMContext) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Анкета не найдена.", reply_markup=reg_start_kb())
        return
    if owner.status == "активный":
        await message.answer("Используйте Редактировать профиль.")
        return
    await message.answer(
        _format_draft(owner) + "\n\nВыберите поле для изменения:",
        reply_markup=draft_edit_kb(
            owner.draft_service_type,
            started_fields=_draft_started_edit_fields(owner),
            city=owner.draft_city,
        ),
    )


# ── Draft field edit callbacks ────────────────────────────────


_EDIT_DRAFT_MAP = {
    "edit_draft:city": (RegistrationFSM.reg_city_search, "Введите город сервиса:"),
    "edit_draft:name": (RegistrationFSM.reg_name, "Введите название:"),
    "edit_draft:service_type": (RegistrationFSM.reg_service_type, None),
    "edit_draft:category": (RegistrationFSM.reg_category, None),
    "edit_draft:upgrade_cats": (RegistrationFSM.reg_upgrade_categories, None),
    "edit_draft:hydro": (RegistrationFSM.reg_hydroisolation, None),
    "edit_draft:hydro_price": (
        RegistrationFSM.reg_hydro_price,
        "Укажите стоимость гидроизоляции (число или диапазон):",
    ),
    "edit_draft:address": (RegistrationFSM.reg_address, "Введите адрес:"),
    "edit_draft:metro": (RegistrationFSM.reg_metro_search, "Введите станцию метро:"),
    "edit_draft:phone": (RegistrationFSM.reg_phone, "Введите телефон:"),
    "edit_draft:working_days": (RegistrationFSM.reg_working_days, None),
    "edit_draft:hours": (RegistrationFSM.reg_hours, "Время работы (HH:MM-HH:MM):"),
    "edit_draft:diagnostics": (
        RegistrationFSM.reg_diagnostics,
        "Стоимость диагностики (руб.):",
    ),
    "edit_draft:diag_included": (RegistrationFSM.reg_diag_included, None),
}


@router.callback_query(F.data.startswith("edit_draft:"))
async def edit_draft_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    entry = _EDIT_DRAFT_MAP.get(callback.data)
    if not entry:
        await callback.answer("Неизвестное поле")
        return
    fsm_state, prompt = entry
    field_key = callback.data.split(":", 1)[1]
    await state.update_data(editing_draft=True, editing_draft_field=field_key)
    await state.set_state(fsm_state)
    if callback.data == "edit_draft:service_type":
        await _safe_edit_or_answer(callback, _STYPE_TEXT, reg_service_type_kb())
    elif callback.data == "edit_draft:category":
        await _safe_edit_or_answer(callback, _CATEGORY_TEXT, reg_category_kb())
    elif callback.data == "edit_draft:upgrade_cats":
        owner = await _get_owner(callback.from_user.id)
        selected = set((owner.draft_upgrade_categories or "").split(",")) - {""}
        await state.update_data(selected_upgrade_cats=list(selected))
        await _safe_edit_or_answer(
            callback,
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(selected),
        )
    elif callback.data == "edit_draft:hydro":
        await _safe_edit_or_answer(
            callback, "Выполняете гидроизоляцию?", reg_yes_no_kb("reg_hydro")
        )
    elif callback.data == "edit_draft:diag_included":
        await _safe_edit_or_answer(
            callback, "Диагностика входит в стоимость?", reg_diag_included_kb()
        )
    elif callback.data == "edit_draft:working_days":
        owner = await _get_owner(callback.from_user.id)
        selected = set((owner.draft_working_days or "").split(",")) - {""}
        await state.update_data(selected_days=list(selected))
        await _safe_edit_or_answer(
            callback, "Выберите рабочие дни:", reg_working_days_kb(selected)
        )
    else:
        await _safe_edit_or_answer(callback, prompt or "Введите значение:")


async def _after_edit(event, state: FSMContext) -> bool:
    """After editing a single field, return to draft view. Returns True if handled."""
    data = await state.get_data()
    if not data.get("editing_draft"):
        return False
    edited_field = data.get("editing_draft_field")
    await state.clear()
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if owner and owner.status == "активный":
        updated_svc = await _sync_active_service_from_draft(owner.owner_user_id)
        if updated_svc is not None:
            _schedule_service_sheet_sync(updated_svc)
            await _notify_admins_about_active_profile_edit(
                event,
                service_id=updated_svc.id,
                field_key=str(edited_field) if edited_field is not None else None,
            )
            refreshed_owner = await _get_owner(tg_id)
            if refreshed_owner is not None:
                owner = refreshed_owner

    if owner:
        owner_city = getattr(owner, "draft_city", None) or "Москва"
        await _safe_edit_or_answer(
            event,
            _format_draft(owner) + "\n\nВыберите поле для изменения:",
            draft_edit_kb(
                owner.draft_service_type,
                started_fields=_draft_started_edit_fields(owner),
                city=owner_city,
            ),
        )
    return True


# ── Step 1: Name ──────────────────────────────────────────────


@router.message(RegistrationFSM.reg_city_search, F.text)
async def reg_city_search(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = CityInput(text=message.text)
    except ValidationError as exc:
        await message.answer(_pydantic_msg(exc))
        return

    options = await _load_city_options()
    matches = top_city_matches(v.text, options, limit=5)
    if not matches:
        await message.answer("Город не найден. Попробуйте ввести название точнее:")
        return

    if len(matches) == 1 or matches[0][1] > 0.85:
        city = matches[0][0]
        await state.update_data(pending_city=city, city_options=[city])
        await state.set_state(RegistrationFSM.reg_city_confirm)
        await message.answer(
            f"Найден город: {city}. Верно?",
            reply_markup=reg_city_confirm_kb(city),
        )
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    city_options = [city for city, _ in matches]
    await state.update_data(city_options=city_options)
    await state.set_state(RegistrationFSM.reg_city_confirm)

    rows = [
        [
            InlineKeyboardButton(
                text=city,
                callback_data=f"reg_city_pick:{idx}",
            )
        ]
        for idx, city in enumerate(city_options)
    ]
    rows.append(
        [InlineKeyboardButton(text="Ввести заново", callback_data="reg_city_retry")]
    )
    rows.append([BACK_BTN])
    await message.answer(
        "Найдено несколько городов. Выберите нужный:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _save_draft_city(tg_id: int, city: str) -> None:
    update_kwargs: dict[str, object] = {
        "draft_city": city,
        "draft_latitude": None,
        "draft_longitude": None,
    }
    if not is_moscow_city(city):
        update_kwargs["draft_metro"] = None
    await _update_draft(tg_id, **update_kwargs)


@router.callback_query(RegistrationFSM.reg_city_confirm, F.data == "reg_city_ok")
async def reg_city_ok(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    city = data.get("pending_city")
    if not city:
        await callback.answer("Город не выбран", show_alert=True)
        return
    await _save_draft_city(callback.from_user.id, city)
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_name)
    await _safe_edit_or_answer(
        callback,
        "Введите название вашего сервисного центра:",
        reg_back_kb(),
    )


@router.callback_query(
    RegistrationFSM.reg_city_confirm, F.data.startswith("reg_city_pick:")
)
async def reg_city_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    data = await state.get_data()
    options = data.get("city_options", [])
    if not isinstance(options, list) or idx < 0 or idx >= len(options):
        await callback.answer("Город не найден", show_alert=True)
        return

    city = options[idx]
    await _save_draft_city(callback.from_user.id, city)
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_name)
    await _safe_edit_or_answer(
        callback,
        "Введите название вашего сервисного центра:",
        reg_back_kb(),
    )


@router.callback_query(RegistrationFSM.reg_city_confirm, F.data == "reg_city_retry")
async def reg_city_retry(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationFSM.reg_city_search)
    await _safe_edit_or_answer(
        callback, "Введите город сервисного центра:", reg_back_kb()
    )


# ── Step 1: Name ──────────────────────────────────────────────


@router.message(RegistrationFSM.reg_name, F.text)
async def reg_name(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = ServiceNameInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    await _update_draft(message.from_user.id, draft_name=v.text)
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_service_type)
    await message.answer(_STYPE_TEXT, reply_markup=reg_service_type_kb())


# ── Step 2: Service type ──────────────────────────────────────


@router.callback_query(
    RegistrationFSM.reg_service_type, F.data.startswith("reg_stype:")
)
async def reg_service_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    stype = callback.data.split(":")[1]
    data = await state.get_data()
    editing = data.get("editing_draft", False)

    # Clear incompatible fields when type changes
    clear_kwargs: dict = {"draft_service_type": stype}
    if stype == "upgrade":
        clear_kwargs["draft_category"] = None
    elif stype == "repair":
        clear_kwargs["draft_upgrade_categories"] = None
    await _update_draft(callback.from_user.id, **clear_kwargs)

    # When editing from draft, do NOT call _after_edit here —
    # chain to the follow-up question so the dependent field is also filled.
    if not editing:
        pass  # normal registration flow — proceed below
    # else: keep editing_draft=True, the chained handler will call _after_edit

    owner = await _get_owner(callback.from_user.id)

    if stype == "upgrade":
        selected = set((owner.draft_upgrade_categories or "").split(",")) - {""}
        await state.update_data(selected_upgrade_cats=list(selected))
        await state.set_state(RegistrationFSM.reg_upgrade_categories)
        await _safe_edit_or_answer(
            callback,
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(selected),
        )
    elif stype == "complex" and owner and owner.draft_category:
        selected = set((owner.draft_upgrade_categories or "").split(",")) - {""}
        await state.update_data(selected_upgrade_cats=list(selected))
        await state.set_state(RegistrationFSM.reg_upgrade_categories)
        await _safe_edit_or_answer(
            callback,
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(selected),
        )
    else:
        # repair / complex → ask category (Электрика / Механика)
        await state.set_state(RegistrationFSM.reg_category)
        await _safe_edit_or_answer(callback, _CATEGORY_TEXT, reg_category_kb())


# ── Step 2b: Repair category ──────────────────────────────────


@router.callback_query(RegistrationFSM.reg_category, F.data.startswith("reg_cat:"))
async def reg_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":")[1]
    await _update_draft(callback.from_user.id, draft_category=cat)
    owner = await _get_owner(callback.from_user.id)
    if owner and owner.draft_service_type == "complex":
        selected = set((owner.draft_upgrade_categories or "").split(",")) - {""}
        await state.update_data(selected_upgrade_cats=list(selected))
        await state.set_state(RegistrationFSM.reg_upgrade_categories)
        await _safe_edit_or_answer(
            callback,
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(selected),
        )
        return
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_hydroisolation)
    await _safe_edit_or_answer(
        callback, "Выполняете гидроизоляцию?", reg_yes_no_kb("reg_hydro")
    )


# ── Step 3: Upgrade categories (multi-select) ────────────────


@router.callback_query(
    RegistrationFSM.reg_upgrade_categories, F.data.startswith("reg_upcat:")
)
async def reg_upcat_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":", 1)[1]
    if cat == "done":
        return  # handled by separate handler
    data = await state.get_data()
    selected = list(data.get("selected_upgrade_cats", []))
    if cat in selected:
        selected.remove(cat)
    else:
        selected.append(cat)
    await state.update_data(selected_upgrade_cats=selected)
    await _safe_edit_or_answer(
        callback,
        "Выберите категории апгрейда:",
        reg_upgrade_categories_kb(set(selected)),
    )


@router.callback_query(
    RegistrationFSM.reg_upgrade_categories, F.data == "reg_upcat_done"
)
async def reg_upcat_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("selected_upgrade_cats", [])
    if not selected:
        await callback.answer("Выберите хотя бы одну категорию", show_alert=True)
        return
    await _update_draft(
        callback.from_user.id, draft_upgrade_categories=",".join(selected)
    )
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_hydroisolation)
    await _safe_edit_or_answer(
        callback, "Выполняете гидроизоляцию?", reg_yes_no_kb("reg_hydro")
    )


# ── Step 4: Hydroisolation ───────────────────────────────────


@router.callback_query(
    RegistrationFSM.reg_hydroisolation, F.data.startswith("reg_hydro:")
)
async def reg_hydro(callback: types.CallbackQuery, state: FSMContext) -> None:
    val = callback.data.split(":")[1] == "yes"
    await _update_draft(callback.from_user.id, draft_hydroisolation=val)
    if not val:
        await _update_draft(callback.from_user.id, draft_hydro_price=None)
    if not val:
        if await _after_edit(callback, state):
            return
        await state.set_state(RegistrationFSM.reg_address)
        await _safe_edit_or_answer(
            callback, "Введите адрес сервисного центра:", reg_back_kb()
        )
        return

    await state.set_state(RegistrationFSM.reg_hydro_price)
    await _safe_edit_or_answer(
        callback,
        "💧 Укажите стоимость гидроизоляции.\n\n"
        "Введите фиксированную цену или диапазон:\n"
        "• Фиксированная: <code>1000</code>\n"
        "• Диапазон: <code>1000-2000</code>",
        reg_back_kb(),
    )


@router.message(RegistrationFSM.reg_hydro_price, F.text)
async def reg_hydro_price(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    text = message.text.strip()
    if not re.match(r"^\d+(-\d+)?$", text):
        await message.answer(
            "Неверный формат. Введите число (напр. 1000) "
            "или диапазон (напр. 1000-2000):"
        )
        return

    try:
        low, high = parse_hydro_price_range(text)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    normalized = f"{low:.0f}" if low == high else f"{low:.0f}-{high:.0f}"
    await _update_draft(
        message.from_user.id,
        draft_hydro_price=normalized,
        draft_hydroisolation=True,
    )
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_address)
    await message.answer("Введите адрес сервисного центра:", reply_markup=reg_back_kb())


# ── Step 5: Address ───────────────────────────────────────────


@router.message(RegistrationFSM.reg_address, F.text)
async def reg_address(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = AddressInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    await _update_draft(message.from_user.id, draft_address=v.text)
    owner = await _get_owner(message.from_user.id)
    if owner is None:
        await message.answer("Анкета не найдена.")
        await state.clear()
        return

    if is_moscow_city(owner.draft_city):
        if owner.draft_metro:
            coords = await _geocode_draft_location(owner)
            if coords is None:
                await message.answer(
                    "Не удалось определить координаты по адресу. "
                    f"{_ADDRESS_AND_METRO_HINT}"
                )
                return
            await _update_draft(
                message.from_user.id,
                draft_latitude=coords[0],
                draft_longitude=coords[1],
            )
        else:
            await _update_draft(
                message.from_user.id,
                draft_latitude=None,
                draft_longitude=None,
            )

        if await _after_edit(message, state):
            return
        await state.set_state(RegistrationFSM.reg_metro_search)
        await message.answer(
            "Введите ближайшую станцию метро (или часть названия):",
            reply_markup=reg_back_kb(),
        )
        return

    coords = await _geocode_draft_location(owner)
    if coords is None:
        await message.answer(
            "Не удалось определить координаты по адресу. " f"{_ADDRESS_PRECISION_HINT}"
        )
        return
    await _update_draft(
        message.from_user.id,
        draft_latitude=coords[0],
        draft_longitude=coords[1],
    )

    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_phone)
    await message.answer("Введите контактный телефон:", reply_markup=reg_back_kb())


# ── Step 6: Metro search ─────────────────────────────────────


@router.message(RegistrationFSM.reg_metro_search, F.text)
async def reg_metro_search(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    owner = await _get_owner(message.from_user.id)
    if owner is None:
        await message.answer("Анкета не найдена.")
        await state.clear()
        return
    if not is_moscow_city(owner.draft_city):
        await state.set_state(RegistrationFSM.reg_phone)
        await message.answer("Введите контактный телефон:", reply_markup=reg_back_kb())
        return

    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы 2 символа.")
        return
    async with async_session() as session:
        stations = (await session.execute(select(MetroStation))).scalars().all()
    matches = top_metro_matches(query, stations, limit=5)
    if not matches:
        await message.answer("Станция не найдена. Попробуйте ещё раз:")
        return
    if len(matches) == 1 or matches[0][1] > 0.85:
        station = matches[0][0]
        await state.update_data(pending_metro=station.name)
        await state.set_state(RegistrationFSM.reg_metro_confirm)
        await message.answer(
            f"Найдена: {station.name} ({station.line}). Верно?",
            reply_markup=metro_confirm_kb(station.name),
        )
    else:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = []
        for st, _ in matches:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{st.name} ({st.line})",
                        callback_data=f"reg_metro_pick:{st.id}",
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Ввести заново", callback_data="reg_metro_retry"
                )
            ]
        )
        buttons.append([BACK_BTN])
        await state.set_state(RegistrationFSM.reg_metro_confirm)
        await message.answer(
            "Найдено несколько станций:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(RegistrationFSM.reg_metro_confirm, F.data == "reg_metro_ok")
async def reg_metro_ok(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    metro_name = data.get("pending_metro", "")
    await _update_draft(callback.from_user.id, draft_metro=metro_name)
    owner = await _get_owner(callback.from_user.id)
    if owner is None:
        await callback.answer("Анкета не найдена", show_alert=True)
        return
    coords = await _geocode_draft_location(owner)
    if coords is None:
        await state.set_state(RegistrationFSM.reg_metro_search)
        await _safe_edit_or_answer(
            callback,
            "Не удалось определить координаты по адресу и метро. "
            f"{_ADDRESS_AND_METRO_HINT}",
            reg_back_kb(),
        )
        return
    await _update_draft(
        callback.from_user.id,
        draft_latitude=coords[0],
        draft_longitude=coords[1],
    )
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_phone)
    await _safe_edit_or_answer(callback, "Введите контактный телефон:", reg_back_kb())


@router.callback_query(
    RegistrationFSM.reg_metro_confirm, F.data.startswith("reg_metro_pick:")
)
async def reg_metro_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    station_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        station = (
            await session.execute(
                select(MetroStation).where(MetroStation.id == station_id)
            )
        ).scalar_one_or_none()
    if not station:
        await callback.answer("Станция не найдена")
        return
    await _update_draft(callback.from_user.id, draft_metro=station.name)
    owner = await _get_owner(callback.from_user.id)
    if owner is None:
        await callback.answer("Анкета не найдена", show_alert=True)
        return
    coords = await _geocode_draft_location(owner)
    if coords is None:
        await state.set_state(RegistrationFSM.reg_metro_search)
        await _safe_edit_or_answer(
            callback,
            "Не удалось определить координаты по адресу и метро. "
            f"{_ADDRESS_AND_METRO_HINT}",
            reg_back_kb(),
        )
        return
    await _update_draft(
        callback.from_user.id,
        draft_latitude=coords[0],
        draft_longitude=coords[1],
    )
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_phone)
    await _safe_edit_or_answer(callback, "Введите контактный телефон:", reg_back_kb())


@router.callback_query(RegistrationFSM.reg_metro_confirm, F.data == "reg_metro_retry")
async def reg_metro_retry(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationFSM.reg_metro_search)
    await _safe_edit_or_answer(callback, "Введите название станции метро:")


# ── Step 7: Phone ─────────────────────────────────────────────


@router.message(RegistrationFSM.reg_phone, F.text)
async def reg_phone(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = PhoneInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    uname = message.from_user.username
    tg = f"@{uname}" if uname else ""
    await _update_draft(message.from_user.id, draft_phone=v.text, draft_telegram=tg)
    if await _after_edit(message, state):
        return
    await state.update_data(selected_days=[])
    await state.set_state(RegistrationFSM.reg_working_days)
    await message.answer(
        "Выберите рабочие дни:", reply_markup=reg_working_days_kb(set())
    )


# ── Step 9: Working days (multi-select) ───────────────────────


@router.callback_query(RegistrationFSM.reg_working_days, F.data.startswith("reg_day:"))
async def reg_day_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    day = callback.data.split(":")[1]
    data = await state.get_data()
    selected = list(data.get("selected_days", []))
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    await state.update_data(selected_days=selected)
    await _safe_edit_or_answer(
        callback, "Выберите рабочие дни:", reg_working_days_kb(set(selected))
    )


@router.callback_query(RegistrationFSM.reg_working_days, F.data == "reg_days_done")
async def reg_days_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("selected_days", [])
    if not selected:
        await callback.answer("Выберите хотя бы один день", show_alert=True)
        return
    ordered = [d for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] if d in selected]
    await _update_draft(callback.from_user.id, draft_working_days=",".join(ordered))
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_hours)
    await _safe_edit_or_answer(
        callback,
        "Введите время работы (формат: HH:MM-HH:MM, например 09:00-21:00):",
        reg_back_kb(),
    )


# ── Step 10: Hours ────────────────────────────────────────────


@router.message(RegistrationFSM.reg_hours, F.text)
async def reg_hours(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = WorkHoursInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    m = re.match(r"^(\d{2}:\d{2})\s*[-\u2013]\s*(\d{2}:\d{2})$", v.text)
    open_t, close_t = m.group(1), m.group(2)
    await _update_draft(
        message.from_user.id, draft_open_time=open_t, draft_close_time=close_t
    )
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_diagnostics)
    await message.answer(
        "💰 <b>Стоимость диагностики</b>\n\n"
        "Укажите стоимость диагностики в рублях.\n\n"
        "• Если диагностика <b>бесплатная</b> — введите 0\n"
        "• Если <b>платная</b> — укажите сумму в рублях\n\n"
        "Пример: 500",
        reply_markup=reg_back_kb(),
    )


# ── Step 11: Diagnostics price ────────────────────────────────


@router.message(RegistrationFSM.reg_diagnostics, F.text)
async def reg_diagnostics(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = DiagnosticsPriceInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    price = int(v.text)
    await _update_draft(message.from_user.id, draft_diagnostics_price=float(price))
    await state.set_state(RegistrationFSM.reg_diag_included)
    await message.answer(
        "🔧 <b>Диагностика входит в стоимость ремонта?</b>\n\n"
        "<b>Входит в стоимость</b> — клиент оплачивает диагностику, "
        "и если ремонт состоится, сумма диагностики вычитается из итогового счёта.\n\n"
        "<b>Оплачивается отдельно</b> — диагностика оплачивается как отдельная услуга.",
        reply_markup=reg_diag_included_kb(),
    )


# ── Step 12: Diag included ────────────────────────────────────


@router.callback_query(
    RegistrationFSM.reg_diag_included, F.data.startswith("reg_diag_incl:")
)
async def reg_diag_included(callback: types.CallbackQuery, state: FSMContext) -> None:
    val = callback.data.split(":")[1] == "yes"
    await _update_draft(callback.from_user.id, draft_diag_included=val)
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_confirm)
    owner = await _get_owner(callback.from_user.id)
    await _safe_edit_or_answer(
        callback,
        _format_draft(owner) + "\n\nВсё верно?",
        reg_confirm_kb(has_bank=bool(owner.draft_bank_account)),
    )


# ── Step 13: Legal form ───────────────────────────────────────


@router.callback_query(RegistrationFSM.reg_legal_form, F.data.startswith("reg_legal:"))
async def reg_legal_form(callback: types.CallbackQuery, state: FSMContext) -> None:
    form = callback.data.split(":", 1)[1]
    await _update_draft(callback.from_user.id, draft_legal_form=form)
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_tax_system)
    await _safe_edit_or_answer(
        callback, "Выберите систему налогообложения:", reg_tax_system_kb()
    )


# ── Step 14: Tax system ───────────────────────────────────────


@router.callback_query(RegistrationFSM.reg_tax_system, F.data.startswith("reg_tax:"))
async def reg_tax_system(callback: types.CallbackQuery, state: FSMContext) -> None:
    tax = callback.data.split(":", 1)[1]
    await _update_draft(callback.from_user.id, draft_tax_system=tax)
    if await _after_edit(callback, state):
        return
    data = await state.get_data()
    if data.get("filling_bank"):
        await state.update_data(bank_step=0, filling_bank=False)
        await state.set_state(RegistrationFSM.reg_bank_details)
        await _safe_edit_or_answer(
            callback,
            "⚠️ <b>Банковские реквизиты</b>\n"
            "По этим реквизитам будут производиться выплаты.\n\n"
            f"Введите {_BANK_FIELDS[0][1]}:",
        )
    else:
        await state.set_state(RegistrationFSM.reg_confirm)
        owner = await _get_owner(callback.from_user.id)
        await _safe_edit_or_answer(
            callback,
            _format_draft(owner) + "\n\nВсё верно?",
            reg_confirm_kb(has_bank=bool(owner.draft_bank_account)),
        )


# ── Step 15: Bank details (6 sequential inputs) ──────────────


@router.message(RegistrationFSM.reg_bank_details, F.text)
async def reg_bank_detail(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    data = await state.get_data()
    step = data.get("bank_step", 0)
    if step >= len(_BANK_FIELDS):
        return
    field_name, label, schema_cls = _BANK_FIELDS[step]
    try:
        v = schema_cls(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    await _update_draft(message.from_user.id, **{field_name: v.text})
    next_step = step + 1
    if next_step < len(_BANK_FIELDS):
        await state.update_data(bank_step=next_step)
        await message.answer(f"Введите {_BANK_FIELDS[next_step][1]}:")
    else:
        if await _after_edit(message, state):
            return
        await state.set_state(RegistrationFSM.reg_confirm)
        owner = await _get_owner(message.from_user.id)
        await message.answer(
            _format_draft(owner) + "\n\nВсё верно?",
            reply_markup=reg_confirm_kb(has_bank=True),
        )


# ── Step 16: Confirm ──────────────────────────────────────────


@router.callback_query(RegistrationFSM.reg_confirm, F.data == "reg:fill_bank")
async def reg_fill_bank(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner.draft_legal_form:
        await state.update_data(filling_bank=True)
        await state.set_state(RegistrationFSM.reg_legal_form)
        await _safe_edit_or_answer(
            callback, "Выберите организационно-правовую форму:", reg_legal_form_kb()
        )
    elif not owner.draft_tax_system:
        await state.update_data(filling_bank=True)
        await state.set_state(RegistrationFSM.reg_tax_system)
        await _safe_edit_or_answer(
            callback, "Выберите систему налогообложения:", reg_tax_system_kb()
        )
    else:
        await state.update_data(bank_step=0)
        await state.set_state(RegistrationFSM.reg_bank_details)
        await _safe_edit_or_answer(
            callback,
            "⚠️ <b>Банковские реквизиты</b>\n"
            "По этим реквизитам будут производиться выплаты.\n\n"
            f"Введите {_BANK_FIELDS[0][1]}:",
        )


@router.callback_query(RegistrationFSM.reg_confirm, F.data == "reg:submit")
async def reg_submit(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner or not _draft_complete(owner):
        await _safe_edit_or_answer(callback, "Анкета не заполнена полностью.")
        return

    coords = await _geocode_draft_location(owner)
    if coords is None:
        await _safe_edit_or_answer(
            callback,
            "Не удалось определить координаты сервиса. " f"{_ADDRESS_AND_METRO_HINT}",
        )
        return
    await _update_draft(
        callback.from_user.id,
        draft_latitude=coords[0],
        draft_longitude=coords[1],
    )
    owner = await _get_owner(callback.from_user.id)
    if owner is None:
        await _safe_edit_or_answer(callback, "Анкета не найдена.")
        return

    svc: Service | None = None
    async with async_session() as session:
        draft = (
            await session.execute(
                select(ServiceDraft).where(
                    ServiceDraft.owner_user_id == callback.from_user.id
                )
            )
        ).scalar_one_or_none()
        if draft is None:
            await _safe_edit_or_answer(callback, "Анкета не найдена.")
            return

        cat_id = None
        if draft.draft_category:
            cat = (
                await session.execute(
                    select(ServiceCategory).where(
                        ServiceCategory.name == draft.draft_category
                    )
                )
            ).scalar_one_or_none()
            if cat:
                cat_id = cat.id

        if draft.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == draft.service_id)
                )
            ).scalar_one_or_none()
        else:
            svc = None

        if svc is None:
            svc = Service(
                name=draft.draft_name or "Без названия",
                service_type=draft.draft_service_type or "repair",
                is_available=False,
            )
            session.add(svc)
            await session.flush()

        svc.name = draft.draft_name or "Без названия"
        svc.service_type = draft.draft_service_type or "repair"
        svc.category_id = cat_id
        svc.city = draft.draft_city
        svc.address = draft.draft_address
        svc.latitude = draft.draft_latitude
        svc.longitude = draft.draft_longitude
        svc.nearest_metro = (
            draft.draft_metro if is_moscow_city(draft.draft_city) else None
        )
        svc.phone = draft.draft_phone
        svc.telegram_handle = draft.draft_telegram
        svc.open_time = draft.draft_open_time
        svc.close_time = draft.draft_close_time
        svc.has_hydroisolation = draft.draft_hydroisolation
        svc.hydroisolation_price = draft.draft_hydro_price
        svc.diagnostics_price = draft.draft_diagnostics_price
        svc.diagnostics_included = draft.draft_diag_included
        svc.upgrade_categories = draft.draft_upgrade_categories
        svc.working_days = draft.draft_working_days
        svc.partnership_status = "ожидает"
        svc.is_available = False
        svc.registration_complete = True

        draft.service_id = svc.id
        draft.status = "ожидает"
        draft.registration_complete = True

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
        bank.legal_form = draft.draft_legal_form
        bank.tax_system = draft.draft_tax_system
        bank.bank_account = draft.draft_bank_account
        bank.bank_name = draft.draft_bank_name
        bank.bik = draft.draft_bik
        bank.corr_account = draft.draft_corr_account
        bank.org_name = draft.draft_org_name
        bank.inn = draft.draft_inn

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
                    owner_user_id=draft.owner_user_id,
                )
            )
        else:
            settings.owner_user_id = draft.owner_user_id

        await session.commit()
        await session.refresh(svc)

    if svc:
        try:
            update_service_row(svc)
        except Exception:
            try:
                add_service_row(svc)
            except Exception:
                logger.exception("Failed to write submitted service to Sheets")

    logger.info("partner %s submitted registration draft", callback.from_user.id)
    await state.clear()
    await _safe_edit_or_answer(
        callback,
        (
            "Ваша анкета отправлена на модерацию. "
            "Мы уведомим вас после проверки. Ожидайте одобрения."
        ),
        reply_markup=partner_pending_menu_kb(has_draft=False),
    )

    # Notify admins about new application
    try:
        _stype_label = {
            "repair": "Ремонт",
            "upgrade": "Апгрейд",
            "complex": "Комплекс",
        }.get(owner.draft_service_type or "", owner.draft_service_type or "")
        await notify_admins(
            callback.bot,
            scope=ADMIN_SCOPE_PARTNER,
            event_key="partner_application",
            text=(
                "Новая заявка на партнёрство.\n"
                f"<b>Сервис:</b> {e(owner.draft_name or '—')}\n"
                f"<b>Тип:</b> {e(_stype_label or '—')}\n"
                "Откройте /start → Панель администратора для проверки."
            ),
            dedupe_prefix=f"partner_application:{owner.id}",
        )
    except Exception:
        logger.exception("Failed to notify admins about new application")


@router.callback_query(RegistrationFSM.reg_confirm, F.data == "reg:restart")
async def reg_restart(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationFSM.reg_city_search)
    await _safe_edit_or_answer(
        callback, "Начнем заново. Введите город сервисного центра:"
    )


# ── Back button ───────────────────────────────────────────────


_STATE_ORDER = [
    RegistrationFSM.reg_city_search,
    RegistrationFSM.reg_name,
    RegistrationFSM.reg_service_type,
    RegistrationFSM.reg_category,
    RegistrationFSM.reg_upgrade_categories,
    RegistrationFSM.reg_hydroisolation,
    RegistrationFSM.reg_hydro_price,
    RegistrationFSM.reg_address,
    RegistrationFSM.reg_metro_search,
    RegistrationFSM.reg_metro_confirm,
    RegistrationFSM.reg_phone,
    RegistrationFSM.reg_working_days,
    RegistrationFSM.reg_hours,
    RegistrationFSM.reg_diagnostics,
    RegistrationFSM.reg_diag_included,
    RegistrationFSM.reg_legal_form,
    RegistrationFSM.reg_tax_system,
    RegistrationFSM.reg_bank_details,
    RegistrationFSM.reg_confirm,
]

_STATE_PROMPTS = {
    RegistrationFSM.reg_city_search.state: (
        "Введите город сервисного центра:",
        reg_back_kb(),
    ),
    RegistrationFSM.reg_name.state: (
        "Введите название сервисного центра:",
        reg_back_kb(),
    ),
    RegistrationFSM.reg_service_type.state: (
        _STYPE_TEXT,
        reg_service_type_kb(),
    ),
    RegistrationFSM.reg_category.state: (
        _CATEGORY_TEXT,
        reg_category_kb(),
    ),
    RegistrationFSM.reg_upgrade_categories.state: (
        "Выберите категории апгрейда:",
        reg_upgrade_categories_kb(set()),
    ),
    RegistrationFSM.reg_hydroisolation.state: (
        "Выполняете гидроизоляцию?",
        reg_yes_no_kb("reg_hydro"),
    ),
    RegistrationFSM.reg_hydro_price.state: (
        "Укажите стоимость гидроизоляции (число или диапазон, напр. 1000 или 1000-2000):",
        reg_back_kb(),
    ),
    RegistrationFSM.reg_address.state: ("Введите адрес:", reg_back_kb()),
    RegistrationFSM.reg_metro_search.state: ("Введите станцию метро:", reg_back_kb()),
    RegistrationFSM.reg_phone.state: ("Введите телефон:", reg_back_kb()),
    RegistrationFSM.reg_working_days.state: (
        "Выберите рабочие дни:",
        reg_working_days_kb(set()),
    ),
    RegistrationFSM.reg_hours.state: (
        "Введите время работы (HH:MM-HH:MM):",
        reg_back_kb(),
    ),
    RegistrationFSM.reg_diagnostics.state: (
        "Стоимость диагностики (руб.):",
        reg_back_kb(),
    ),
    RegistrationFSM.reg_diag_included.state: (
        "Диагностика входит в стоимость?",
        reg_diag_included_kb(),
    ),
    RegistrationFSM.reg_legal_form.state: (
        "Выберите орг.-правовую форму:",
        reg_legal_form_kb(),
    ),
    RegistrationFSM.reg_tax_system.state: (
        "Выберите систему налогообложения:",
        reg_tax_system_kb(),
    ),
    RegistrationFSM.reg_bank_details.state: ("Введите расчётный счёт:", reg_back_kb()),
}


@router.callback_query(F.data == "back")
async def reg_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if not current:
        await callback.answer()
        return

    owner = await _get_owner(callback.from_user.id)
    stype = owner.draft_service_type if owner else None

    if current == RegistrationFSM.reg_city_confirm.state:
        await state.set_state(RegistrationFSM.reg_city_search)
        await _safe_edit_or_answer(
            callback,
            "Введите город сервисного центра:",
            reg_back_kb(),
        )
        return

    if current == RegistrationFSM.reg_hydroisolation.state:
        if stype in ("upgrade", "complex"):
            await state.set_state(RegistrationFSM.reg_upgrade_categories)
            selected = set((owner.draft_upgrade_categories or "").split(",")) - {""}
            await state.update_data(selected_upgrade_cats=list(selected))
            await _safe_edit_or_answer(
                callback,
                "Выберите категории апгрейда:",
                reg_upgrade_categories_kb(selected),
            )
            return
        if stype == "repair":
            await state.set_state(RegistrationFSM.reg_category)
            await _safe_edit_or_answer(callback, _CATEGORY_TEXT, reg_category_kb())
            return

    if current == RegistrationFSM.reg_upgrade_categories.state:
        prev = (
            RegistrationFSM.reg_category
            if stype == "complex"
            else RegistrationFSM.reg_service_type
        )
        await state.set_state(prev)
        prompt_info = _STATE_PROMPTS.get(prev.state, ("Назад:", None))
        await _safe_edit_or_answer(callback, prompt_info[0], prompt_info[1])
        return

    state_strings = [s.state for s in _STATE_ORDER]
    if current in state_strings:
        idx = state_strings.index(current)
        if idx > 0:
            prev = _STATE_ORDER[idx - 1]
            await state.set_state(prev)
            prompt_info = _STATE_PROMPTS.get(prev.state, ("Назад:", None))
            await _safe_edit_or_answer(callback, prompt_info[0], prompt_info[1])
            return
    await state.clear()
    await _safe_edit_or_answer(callback, "Регистрация прервана.")
