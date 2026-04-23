"""Partner bot: start, main menu, status."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.core.database import async_session
from client_bot.domain.models import Service, User
from client_bot.domain.states import (
    RegistrationFSM,
)
from partner_bot.ui.keyboards import (
    admin_only_menu_kb,
    partner_main_menu_kb,
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

from client_bot.core.formatting import e
from client_bot.texts import (
    TYPE_RU,
    PARTNER_STATUS_RU,
    Btn,
    PARTNER_MENU_TEXTS,
    Partner,
    Client,
)

# Keep _md_escape as alias so profile.py imports keep working
_md_escape = e

_TYPE_RU = TYPE_RU
_DAY_ORDER = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

logger = logging.getLogger(__name__)
router = Router(name="partner_common")


def _sort_days(raw: str | None) -> str:
    """Sort comma-separated working days Mon→Sun."""
    if not raw:
        return ""
    days = {d.strip() for d in raw.split(",")}
    return ", ".join(d for d in _DAY_ORDER if d in days)


async def _get_owner(tg_id: int) -> Service | None:
    async with async_session() as session:
        return (
            await session.execute(select(Service).where(Service.telegram_id == tg_id))
        ).scalar_one_or_none()


def _draft_complete(owner: Service) -> bool:
    """Returns True when all main required fields are filled."""
    required = [
        owner.draft_name,
        owner.draft_service_type,
        owner.draft_address,
        owner.draft_phone,
        owner.draft_open_time,
        owner.draft_close_time,
        owner.draft_working_days,
    ]
    if owner.draft_service_type == "upgrade":
        required.append(owner.draft_upgrade_categories)
    if owner.draft_service_type in ("repair", "complex"):
        required.append(owner.draft_category)
    return all(required)


def _format_draft(owner: Service) -> str:
    type_map = TYPE_RU
    _status_ru = PARTNER_STATUS_RU
    type_label = type_map.get(
        owner.draft_service_type or "", owner.draft_service_type or "(не заполнено)"
    )
    lines = [
        "Анкета сервисного центра:",
        "",
        f"Название: {e(owner.draft_name or '(не заполнено)')}",
        f"Тип услуг: {type_label}",
    ]
    if owner.draft_service_type == "upgrade":
        cats = (owner.draft_upgrade_categories or "").replace(
            ",", ", "
        ) or "(не выбрано)"
        lines.append(f"Категории апгрейда: {e(cats)}")
    if owner.draft_service_type in ("repair", "complex"):
        lines.append(f"Категория ремонта: {e(owner.draft_category or '(не выбрано)')}")
    lines.append(f"Гидроизоляция: {'Да' if owner.draft_hydroisolation else 'Нет'}")
    if owner.draft_hydroisolation:
        lines.append(
            f"Цена гидроизоляции: {e(owner.draft_hydro_price or '(не указана)')}"
        )
    lines += [
        f"Адрес: {e(owner.draft_address or '(не заполнено)')}",
        f"Метро: {e(owner.draft_metro or '(не заполнено)')}",
        f"Телефон: {e(owner.draft_phone or '(не заполнено)')}",
        f"Telegram: {e(owner.draft_telegram or '—')}",
        f"Рабочие дни: {e(_sort_days(owner.draft_working_days) or '(не выбрано)')}",
        f"Время работы: {owner.draft_open_time or '?'}-{owner.draft_close_time or '?'}",
    ]
    if owner.draft_diagnostics_price is not None and owner.draft_diagnostics_price > 0:
        lines.append(f"Диагностика: {int(owner.draft_diagnostics_price)} руб.")
    else:
        lines.append("Диагностика: бесплатно (0 руб.)")
    lines.append(f"Входит в стоимость: {'Да' if owner.draft_diag_included else 'Нет'}")
    if owner.draft_legal_form or owner.draft_tax_system or owner.draft_bank_account:
        lines.append("")
        lines.append("Реквизиты:")
        if owner.draft_legal_form:
            lines.append(f"  Форма: {e(owner.draft_legal_form)}")
        if owner.draft_tax_system:
            lines.append(f"  Налогообложение: {e(owner.draft_tax_system)}")
        if owner.draft_bank_account:
            lines += [
                f"  Расч. счёт: {e(owner.draft_bank_account)}",
                f"  Банк: {e(owner.draft_bank_name or '—')}",
                f"  БИК: {e(owner.draft_bik or '—')}",
                f"  Корр. счёт: {e(owner.draft_corr_account or '—')}",
                f"  Организация: {e(owner.draft_org_name or '—')}",
                f"  ИНН: {e(owner.draft_inn or '—')}",
            ]
    return "\n".join(lines)


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    # Upsert user
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == message.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=message.from_user.id,
                username=message.from_user.username or "",
                full_name=message.from_user.full_name or "Unknown",
            )
            session.add(user)
            await session.commit()

    owner = await _get_owner(message.from_user.id)

    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES

    # Администраторы видят только панель администратора (не партнёрский интерфейс)
    if is_admin and (owner is None or owner.status != "активный"):
        await message.answer(
            Partner.Common.WELCOME_ADMIN,
            reply_markup=admin_only_menu_kb(),
        )
        return

    if owner is None:
        await message.answer(
            Partner.Common.WELCOME_NEW,
            reply_markup=reg_start_kb(),
        )
        return

    if owner.status == "ожидает":
        if owner.registration_complete:
            await message.answer(
                Partner.Common.DRAFT_PENDING,
                reply_markup=partner_pending_menu_kb(
                    has_draft=False, is_admin=is_admin
                ),
            )
        else:
            await message.answer(
                Partner.Common.DRAFT_INCOMPLETE,
                reply_markup=partner_pending_menu_kb(has_draft=True, is_admin=is_admin),
            )
        return

    if owner.status == "отклонён":
        await message.answer(Partner.Common.REJECTED)
        return

    if owner.status == "приостановлен":
        await message.answer(Partner.Common.SUSPENDED)
        return

    # active
    greeting = f"Service Map \u2014 {e(owner.name)}"
    await message.answer(
        f"{greeting}\n\nВыберите действие:",
        reply_markup=partner_main_menu_kb(is_admin=is_admin),
    )


@router.message(F.text == Btn.MY_DRAFT)
async def show_draft(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer(Partner.NOT_REGISTERED, reply_markup=reg_start_kb())
        return
    await message.answer(_format_draft(owner))


@router.message(F.text == Btn.MY_PROFILE)
async def show_profile(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer(Partner.NOT_REGISTERED)
        return

    if owner.status != "активный":
        await message.answer(f"Статус: {owner.status}")
        return

    wd = _sort_days(owner.working_days)
    lines = [
        f"Название: {e(owner.name)}",
        f"Тип: {_TYPE_RU.get(owner.service_type, owner.service_type)}",
        f"Адрес: {e(owner.address or '-')}",
        f"Метро: {e(owner.nearest_metro or '-')}",
        f"Телефон: {e(owner.phone or '-')}",
        f"Telegram: {e(owner.telegram_handle or '-')}",
        f"Рабочие дни: {e(wd or '-')}",
        f"Время работы: {owner.open_time or '?'}\u2014{owner.close_time or '?'}",
        f"Гидроизоляция: {'Да' if owner.has_hydroisolation else 'Нет'}",
        f"Цена гидроизоляции: {e(owner.hydroisolation_price or '-')}",
        f"Диагностика: {int(owner.diagnostics_price) if owner.diagnostics_price else 0} руб.",
        f"Входит в стоимость: {'Да' if owner.diagnostics_included else 'Нет'}",
        f"Рейтинг: {owner.yandex_rating or '-'}",
        f"Доступен: {'Да' if owner.is_available else 'Нет'}",
    ]
    if owner.upgrade_categories:
        lines.append(
            f"Категории апгрейда: {e(owner.upgrade_categories.replace(',', ', '))}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == Btn.SUPPORT)
async def cmd_support(message: types.Message, state: FSMContext) -> None:
    from client_bot.core.config import SUPPORT_USER, COOPERATION_USER
    from client_bot.ui.keyboards import support_kb

    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer(Partner.PROCEDURE_INTERRUPTED)
    await message.answer(
        Client.Common.CHOOSE_SUPPORT_TOPIC,
        reply_markup=support_kb(SUPPORT_USER, COOPERATION_USER),
    )


@router.message(Command("admin"))
async def cmd_admin_mode(message: types.Message, state: FSMContext) -> None:
    uname = message.from_user.username or ""
    if uname.lower() not in ADMIN_USERNAMES:
        await message.answer(Client.Common.UNAVAILABLE)
        return
    await state.clear()
    await message.answer(
        Partner.Common.ADMIN_MODE,
        reply_markup=admin_only_menu_kb(),
    )


@router.message(Command("client"))
async def cmd_client_mode(message: types.Message, state: FSMContext) -> None:
    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES
    await state.clear()

    owner = await _get_owner(message.from_user.id)
    if owner and owner.status == "активный":
        greeting = f"Service Map \u2014 {e(owner.name)}"
        await message.answer(
            f"{greeting}\n\nВыберите действие:",
            reply_markup=partner_main_menu_kb(is_admin=is_admin),
        )
    elif owner and owner.status == "ожидает":
        if owner.registration_complete:
            await message.answer(
                "Ваша анкета отправлена на модерацию.",
                reply_markup=partner_pending_menu_kb(
                    has_draft=False, is_admin=is_admin
                ),
            )
        else:
            await message.answer(
                "У вас есть незавершенная анкета.",
                reply_markup=partner_pending_menu_kb(has_draft=True, is_admin=is_admin),
            )
    else:
        await message.answer(
            "Добро пожаловать в Service Map!",
            reply_markup=reg_start_kb(),
        )


# ── FSM reminder handlers ────────────────────────────────────


@router.callback_query(F.data == "fsm_remind:cancel")
async def fsm_remind_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(Partner.FORM_CANCELLED)
    except Exception:
        await callback.message.answer(Partner.FORM_CANCELLED)
    await callback.answer()


@router.callback_query(F.data == "fsm_remind:continue")
async def fsm_remind_continue(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Re-send the message for the current FSM step."""
    current = await state.get_state()
    data = await state.get_data()

    if not current:
        try:
            await callback.message.edit_text(Partner.NO_ACTIVE_FORM)
        except Exception:
            pass
        await callback.answer()
        return

    await callback.answer(Partner.LETS_CONTINUE)

    # ── RegistrationFSM ──
    if current == RegistrationFSM.reg_name.state:
        await callback.message.answer("Введите название сервиса:")
    elif current == RegistrationFSM.reg_service_type.state:
        await callback.message.answer(
            "Выберите тип услуг:", reply_markup=reg_service_type_kb()
        )
    elif current == RegistrationFSM.reg_category.state:
        await callback.message.answer(
            "Выберите категорию ремонта:", reply_markup=reg_category_kb()
        )
    elif current == RegistrationFSM.reg_upgrade_categories.state:
        selected = set(data.get("upgrade_categories", []))
        await callback.message.answer(
            "Выберите категории апгрейда:",
            reply_markup=reg_upgrade_categories_kb(selected),
        )
    elif current == RegistrationFSM.reg_hydroisolation.state:
        await callback.message.answer(
            "Делаете ли вы гидроизоляцию?",
            reply_markup=reg_yes_no_kb("reg_hydro"),
        )
    elif current == RegistrationFSM.reg_hydro_price.state:
        await callback.message.answer(
            "Введите стоимость гидроизоляции (число или диапазон, напр. 1000 или 1000-2000):"
        )
    elif current == RegistrationFSM.reg_address.state:
        await callback.message.answer("Введите адрес сервиса:")
    elif current == RegistrationFSM.reg_metro_search.state:
        await callback.message.answer("Введите название ближайшего метро:")
    elif current == RegistrationFSM.reg_metro_confirm.state:
        metro = data.get("metro_name", "")
        if metro:
            from partner_bot.ui.keyboards import metro_confirm_kb

            await callback.message.answer(
                f"Ваша станция — {metro}?",
                reply_markup=metro_confirm_kb(metro),
            )
        else:
            await callback.message.answer("Введите название ближайшего метро:")
    elif current == RegistrationFSM.reg_phone.state:
        await callback.message.answer(
            "Введите номер телефона:", reply_markup=reg_skip_kb()
        )
    elif current == RegistrationFSM.reg_working_days.state:
        selected = set(data.get("working_days", []))
        await callback.message.answer(
            "Выберите рабочие дни:",
            reply_markup=reg_working_days_kb(selected),
        )
    elif current == RegistrationFSM.reg_hours.state:
        await callback.message.answer("Введите время работы (ЧЧ:ММ-ЧЧ:ММ):")
    elif current == RegistrationFSM.reg_diagnostics.state:
        await callback.message.answer("Введите стоимость диагностики (0 — бесплатно):")
    elif current == RegistrationFSM.reg_diag_included.state:
        await callback.message.answer(
            "Диагностика входит в стоимость ремонта?",
            reply_markup=reg_diag_included_kb(),
        )
    elif current == RegistrationFSM.reg_legal_form.state:
        await callback.message.answer(
            "Выберите организационно-правовую форму:",
            reply_markup=reg_legal_form_kb(),
        )
    elif current == RegistrationFSM.reg_tax_system.state:
        await callback.message.answer(
            "Выберите систему налогообложения:",
            reply_markup=reg_tax_system_kb(),
        )
    elif current == RegistrationFSM.reg_bank_details.state:
        await callback.message.answer("Введите расчётный счёт (20 цифр):")
    elif current == RegistrationFSM.reg_confirm.state:
        owner = await _get_owner(callback.from_user.id)
        if owner:
            from partner_bot.handlers.common import _format_draft

            await callback.message.answer(
                _format_draft(owner),
                reply_markup=reg_confirm_kb(has_bank=bool(owner.draft_bank_account)),
            )
        else:
            await callback.message.answer(
                "Нажмите кнопку подтверждения:", reply_markup=reg_confirm_kb()
            )
    else:
        # Unknown state — should not happen since reminder only fires for RegistrationFSM
        await callback.message.answer("Нет активной формы.")
