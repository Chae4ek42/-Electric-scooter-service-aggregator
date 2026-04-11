"""Partner bot: registration FSM."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import select

from bot.core.config import ADMIN_USERNAMES
from bot.core.database import async_session
from bot.domain.models import MetroStation, ServiceOwner, User
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
from bot.domain.states import RegistrationFSM
from bot.services.metro_search import best_metro_match, top_metro_matches
from partner_bot.handlers.common import _draft_complete, _format_draft, _get_owner
from partner_bot.ui.keyboards import (
    BACK_BTN,
    draft_edit_kb,
    metro_confirm_kb,
    partner_pending_menu_kb,
    reg_category_kb,
    reg_confirm_kb,
    reg_diag_included_kb,
    reg_legal_form_kb,
    reg_service_type_kb,
    reg_skip_kb,
    reg_start_kb,
    reg_tax_system_kb,
    reg_upgrade_categories_kb,
    reg_working_days_kb,
    reg_yes_no_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_registration")

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


_STYPE_TEXT = (
    "🔧 *Выберите тип услуг:*\n\n"
    "*Ремонт* — устранение неисправностей: "
    "замена деталей, ремонт электроники, механики.\n\n"
    "*Апгрейд* — модернизация и улучшение самоката: "
    "гидроизоляция, окраска, прошивка, доп. оснащение."
)

_CATEGORY_TEXT = (
    "⚙️ *Выберите категорию ремонта:*\n\n"
    "⚒️ *Механика* — замена колёс, тормозов, подвески, рулевой.\n\n"
    "⚡️ *Электрика* — контроллер, батарея, проводка, дисплей.\n\n"
    "💪 *Комплекс* — и механика, и электрика."
)


async def _ensure_owner(tg_id: int) -> ServiceOwner:
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.telegram_id == tg_id)
            )
        ).scalar_one_or_none()
        if owner is None:
            owner = ServiceOwner(telegram_id=tg_id, status="ожидает")
            session.add(owner)
            await session.commit()
            await session.refresh(owner)
        return owner


async def _update_draft(tg_id: int, **kwargs) -> None:
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.telegram_id == tg_id)
            )
        ).scalar_one_or_none()
        if owner:
            for k, v in kwargs.items():
                setattr(owner, k, v)
            await session.commit()


def _next_empty_state(owner: ServiceOwner) -> str | None:
    """Find next state that needs filling."""
    if not owner.draft_name:
        return RegistrationFSM.reg_name.state
    if not owner.draft_service_type:
        return RegistrationFSM.reg_service_type.state
    if owner.draft_service_type == "upgrade" and not owner.draft_upgrade_categories:
        return RegistrationFSM.reg_upgrade_categories.state
    more = [
        ("draft_address", RegistrationFSM.reg_address.state),
        ("draft_metro", RegistrationFSM.reg_metro_search.state),
        ("draft_phone", RegistrationFSM.reg_phone.state),
        ("draft_working_days", RegistrationFSM.reg_working_days.state),
        ("draft_open_time", RegistrationFSM.reg_hours.state),
        ("draft_legal_form", RegistrationFSM.reg_legal_form.state),
        ("draft_tax_system", RegistrationFSM.reg_tax_system.state),
        ("draft_bank_account", RegistrationFSM.reg_bank_details.state),
    ]
    for field, state in more:
        if not getattr(owner, field, None):
            return state
    return None


# ── Start / continue / edit ───────────────────────────────────


@router.callback_query(F.data == "reg:start")
async def reg_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _ensure_owner(callback.from_user.id)
    await state.set_state(RegistrationFSM.reg_name)
    await _safe_edit_or_answer(callback, "Введите название вашего сервисного центра:")


@router.message(F.text == "Продолжить заполнение")
async def reg_continue(message: types.Message, state: FSMContext) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Начните регистрацию.", reply_markup=reg_start_kb())
        return
    next_state = _next_empty_state(owner)
    if next_state is None:
        await state.set_state(RegistrationFSM.reg_confirm)
        await message.answer(_format_draft(owner), reply_markup=reg_confirm_kb())
        return
    await state.set_state(next_state)
    prompts = {
        RegistrationFSM.reg_name.state: ("Введите название сервисного центра:", None),
        RegistrationFSM.reg_service_type.state: (
            _STYPE_TEXT,
            reg_service_type_kb(),
        ),
        RegistrationFSM.reg_upgrade_categories.state: (
            "Выберите категории апгрейда:",
            reg_upgrade_categories_kb(set()),
        ),
        RegistrationFSM.reg_address.state: ("Введите адрес:", None),
        RegistrationFSM.reg_metro_search.state: (
            "Введите ближайшую станцию метро:",
            None,
        ),
        RegistrationFSM.reg_phone.state: ("Введите контактный телефон:", None),
        RegistrationFSM.reg_working_days.state: (
            "Выберите рабочие дни:",
            reg_working_days_kb(set()),
        ),
        RegistrationFSM.reg_hours.state: ("Введите время работы (HH:MM-HH:MM):", None),
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
            None,
        ),
    }
    prompt = prompts.get(next_state, ("Продолжите заполнение:", None))
    if next_state == RegistrationFSM.reg_bank_details.state:
        await state.update_data(bank_step=0)
    if next_state == RegistrationFSM.reg_upgrade_categories.state:
        await state.update_data(selected_upgrade_cats=[])
    if next_state == RegistrationFSM.reg_working_days.state:
        await state.update_data(selected_days=[])
    if isinstance(prompt, tuple):
        await message.answer(prompt[0], reply_markup=prompt[1])
    else:
        await message.answer(prompt)


@router.message(F.text == "Изменить анкету")
async def edit_draft(message: types.Message, state: FSMContext) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Анкета не найдена.", reply_markup=reg_start_kb())
        return
    if owner.status == "активный":
        await message.answer("Используйте 'Редактировать профиль'.")
        return
    await message.answer(
        _format_draft(owner) + "\n\nВыберите поле для изменения:",
        reply_markup=draft_edit_kb(),
    )


# ── Draft field edit callbacks ────────────────────────────────


_EDIT_DRAFT_MAP = {
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
    "edit_draft:telegram": (RegistrationFSM.reg_telegram, "Введите Telegram:"),
    "edit_draft:working_days": (RegistrationFSM.reg_working_days, None),
    "edit_draft:hours": (RegistrationFSM.reg_hours, "Время работы (HH:MM-HH:MM):"),
    "edit_draft:diagnostics": (
        RegistrationFSM.reg_diagnostics,
        "Стоимость диагностики (руб.):",
    ),
    "edit_draft:diag_included": (RegistrationFSM.reg_diag_included, None),
    "edit_draft:legal_form": (RegistrationFSM.reg_legal_form, None),
    "edit_draft:tax_system": (RegistrationFSM.reg_tax_system, None),
    "edit_draft:bank": (RegistrationFSM.reg_bank_details, None),
}


@router.callback_query(F.data.startswith("edit_draft:"))
async def edit_draft_field(callback: types.CallbackQuery, state: FSMContext) -> None:
    entry = _EDIT_DRAFT_MAP.get(callback.data)
    if not entry:
        await callback.answer("Неизвестное поле")
        return
    fsm_state, prompt = entry
    await state.update_data(editing_draft=True)
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
    elif callback.data == "edit_draft:legal_form":
        await _safe_edit_or_answer(
            callback, "Выберите орг.-правовую форму:", reg_legal_form_kb()
        )
    elif callback.data == "edit_draft:tax_system":
        await _safe_edit_or_answer(
            callback, "Выберите систему налогообложения:", reg_tax_system_kb()
        )
    elif callback.data == "edit_draft:bank":
        await state.update_data(bank_step=0)
        await _safe_edit_or_answer(
            callback,
            "⚠️ Банковские реквизиты\nПо этим реквизитам будут производиться выплаты.\n\nВведите расчётный счёт:",
        )
    else:
        await _safe_edit_or_answer(callback, prompt or "Введите значение:")


async def _after_edit(event, state: FSMContext) -> bool:
    """After editing a single field, return to draft view. Returns True if handled."""
    data = await state.get_data()
    if not data.get("editing_draft"):
        return False
    await state.clear()
    tg_id = event.from_user.id
    owner = await _get_owner(tg_id)
    if owner:
        await _safe_edit_or_answer(
            event,
            _format_draft(owner) + "\n\nВыберите поле для изменения:",
            draft_edit_kb(),
        )
    return True


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
    if stype == "repair":
        clear_kwargs["draft_upgrade_categories"] = None
    else:
        clear_kwargs["draft_category"] = None
    await _update_draft(callback.from_user.id, **clear_kwargs)

    # When editing from draft, do NOT call _after_edit here —
    # chain to the follow-up question so the dependent field is also filled.
    if not editing:
        pass  # normal registration flow — proceed below
    # else: keep editing_draft=True, the chained handler will call _after_edit

    if stype == "upgrade":
        await state.update_data(selected_upgrade_cats=[])
        await state.set_state(RegistrationFSM.reg_upgrade_categories)
        await _safe_edit_or_answer(
            callback, "Выберите категории апгрейда:", reg_upgrade_categories_kb(set())
        )
    else:
        # repair → ask category (Электроника / Механика / Комплекс)
        await state.set_state(RegistrationFSM.reg_category)
        await _safe_edit_or_answer(callback, _CATEGORY_TEXT, reg_category_kb())


# ── Step 2b: Repair category ──────────────────────────────────


@router.callback_query(RegistrationFSM.reg_category, F.data.startswith("reg_cat:"))
async def reg_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":")[1]
    await _update_draft(callback.from_user.id, draft_category=cat)
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
    if await _after_edit(callback, state):
        return
    if val:
        await state.set_state(RegistrationFSM.reg_hydro_price)
        await _safe_edit_or_answer(
            callback,
            "💧 Укажите стоимость гидроизоляции.\n\n"
            "Введите фиксированную цену или диапазон:\n"
            "• Фиксированная: `1000`\n"
            "• Диапазон: `1000-2000`",
        )
    else:
        await state.set_state(RegistrationFSM.reg_address)
        await _safe_edit_or_answer(callback, "Введите адрес сервисного центра:")


# ── Step 4b: Hydro price ──────────────────────────────────────


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
    await _update_draft(message.from_user.id, draft_hydro_price=text)
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_address)
    await message.answer("Введите адрес сервисного центра:")


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
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_metro_search)
    await message.answer("Введите ближайшую станцию метро (или часть названия):")


# ── Step 6: Metro search ─────────────────────────────────────


@router.message(RegistrationFSM.reg_metro_search, F.text)
async def reg_metro_search(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
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
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_phone)
    await _safe_edit_or_answer(callback, "Введите контактный телефон:")


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
    if await _after_edit(callback, state):
        return
    await state.set_state(RegistrationFSM.reg_phone)
    await _safe_edit_or_answer(callback, "Введите контактный телефон:")


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
    await _update_draft(message.from_user.id, draft_phone=v.text)
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_telegram)
    await message.answer(
        "Введите Telegram-аккаунт (или нажмите пропустить):", reply_markup=reg_skip_kb()
    )


# ── Step 8: Telegram ──────────────────────────────────────────


@router.message(RegistrationFSM.reg_telegram, F.text)
async def reg_telegram(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        v = TelegramHandleInput(text=message.text)
    except ValidationError as e:
        await message.answer(_pydantic_msg(e))
        return
    await _update_draft(message.from_user.id, draft_telegram=f"@{v.text}")
    if await _after_edit(message, state):
        return
    await state.update_data(selected_days=[])
    await state.set_state(RegistrationFSM.reg_working_days)
    await message.answer(
        "Выберите рабочие дни:", reply_markup=reg_working_days_kb(set())
    )


@router.callback_query(RegistrationFSM.reg_telegram, F.data == "reg_skip")
async def reg_telegram_skip(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _update_draft(callback.from_user.id, draft_telegram="")
    if await _after_edit(callback, state):
        return
    await state.update_data(selected_days=[])
    await state.set_state(RegistrationFSM.reg_working_days)
    await _safe_edit_or_answer(
        callback, "Выберите рабочие дни:", reg_working_days_kb(set())
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
        callback, "Введите время работы (формат: HH:MM-HH:MM, например 09:00-21:00):"
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
        "💰 *Стоимость диагностики*\n\n"
        "Укажите стоимость диагностики в рублях.\n\n"
        "• Если диагностика *бесплатная* — введите 0\n"
        "• Если *платная* — укажите сумму в рублях\n\n"
        "Пример: 500"
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
    if await _after_edit(message, state):
        return
    await state.set_state(RegistrationFSM.reg_diag_included)
    await message.answer(
        "🔧 *Диагностика входит в стоимость ремонта?*\n\n"
        "*Входит в стоимость* — клиент оплачивает диагностику, "
        "и если ремонт состоится, сумма диагностики вычитается из итогового счёта.\n\n"
        "*Оплачивается отдельно* — диагностика оплачивается как отдельная услуга.",
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
    await state.set_state(RegistrationFSM.reg_legal_form)
    await _safe_edit_or_answer(
        callback, "Выберите организационно-правовую форму:", reg_legal_form_kb()
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
    await state.update_data(bank_step=0)
    await state.set_state(RegistrationFSM.reg_bank_details)
    await _safe_edit_or_answer(
        callback,
        "⚠️ *Банковские реквизиты*\n"
        "По этим реквизитам будут производиться выплаты.\n\n"
        f"Введите {_BANK_FIELDS[0][1]}:",
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
            _format_draft(owner) + "\n\nВсё верно?", reply_markup=reg_confirm_kb()
        )


# ── Step 16: Confirm ──────────────────────────────────────────


@router.callback_query(RegistrationFSM.reg_confirm, F.data == "reg:submit")
async def reg_submit(callback: types.CallbackQuery, state: FSMContext) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner or not _draft_complete(owner):
        await _safe_edit_or_answer(callback, "Анкета не заполнена полностью.")
        return

    logger.info("partner %s submitted registration draft", callback.from_user.id)
    await state.clear()
    await _safe_edit_or_answer(
        callback,
        "Ваша анкета отправлена на модерацию. Мы уведомим вас после проверки.",
    )
    await callback.message.answer(
        "Ожидайте одобрения.",
        reply_markup=partner_pending_menu_kb(has_draft=False),
    )

    # Notify admins about new application
    try:
        admin_list = [u.strip().lower() for u in ADMIN_USERNAMES if u.strip()]
        if admin_list:
            async with async_session() as session:
                from sqlalchemy import func

                admins = (
                    (
                        await session.execute(
                            select(User).where(
                                func.lower(User.username).in_(admin_list)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for admin_user in admins:
                try:
                    await callback.bot.send_message(
                        admin_user.id,
                        f"📋 Новая заявка на партнёрство!\n"
                        f"Сервис: {owner.draft_name}\n"
                        f"Тип: {owner.draft_service_type}\n"
                        f"Откройте /start → Панель администратора для проверки.",
                    )
                except Exception:
                    logger.warning("Failed to notify admin %s", admin_user.id)
    except Exception:
        logger.exception("Failed to notify admins about new application")


@router.callback_query(RegistrationFSM.reg_confirm, F.data == "reg:restart")
async def reg_restart(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationFSM.reg_name)
    await _safe_edit_or_answer(
        callback, "Начнем заново. Введите название сервисного центра:"
    )


# ── Back button ───────────────────────────────────────────────


_STATE_ORDER = [
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
    RegistrationFSM.reg_telegram,
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
    RegistrationFSM.reg_name.state: ("Введите название сервисного центра:", None),
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
        None,
    ),
    RegistrationFSM.reg_address.state: ("Введите адрес:", None),
    RegistrationFSM.reg_metro_search.state: ("Введите станцию метро:", None),
    RegistrationFSM.reg_phone.state: ("Введите телефон:", None),
    RegistrationFSM.reg_telegram.state: ("Введите Telegram:", reg_skip_kb()),
    RegistrationFSM.reg_working_days.state: (
        "Выберите рабочие дни:",
        reg_working_days_kb(set()),
    ),
    RegistrationFSM.reg_hours.state: ("Введите время работы (HH:MM-HH:MM):", None),
    RegistrationFSM.reg_diagnostics.state: ("Стоимость диагностики (руб.):", None),
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
    RegistrationFSM.reg_bank_details.state: ("Введите расчётный счёт:", None),
}


@router.callback_query(F.data == "back")
async def reg_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if not current:
        await callback.answer()
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
