"""Partner bot: start, main menu, status."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from bot.core.config import ADMIN_USERNAMES, PARTNER_BOT_NAME
from bot.core.database import async_session
from bot.domain.models import Service, ServiceOwner, User
from partner_bot.ui.keyboards import (
    admin_only_menu_kb,
    partner_main_menu_kb,
    partner_pending_menu_kb,
    reg_start_kb,
)

_TYPE_RU = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплекс"}
_DAY_ORDER = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

logger = logging.getLogger(__name__)
router = Router(name="partner_common")


def _sort_days(raw: str | None) -> str:
    """Sort comma-separated working days Mon→Sun."""
    if not raw:
        return ""
    days = {d.strip() for d in raw.split(",")}
    return ", ".join(d for d in _DAY_ORDER if d in days)


async def _get_owner(tg_id: int) -> ServiceOwner | None:
    async with async_session() as session:
        return (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.telegram_id == tg_id)
            )
        ).scalar_one_or_none()


def _draft_complete(owner: ServiceOwner) -> bool:
    required = [
        owner.draft_name,
        owner.draft_service_type,
        owner.draft_address,
        owner.draft_phone,
        owner.draft_open_time,
        owner.draft_close_time,
        owner.draft_working_days,
        owner.draft_legal_form,
        owner.draft_tax_system,
        owner.draft_bank_account,
        owner.draft_bank_name,
        owner.draft_bik,
        owner.draft_corr_account,
        owner.draft_org_name,
        owner.draft_inn,
    ]
    if owner.draft_service_type == "upgrade":
        required.append(owner.draft_upgrade_categories)
    if owner.draft_service_type == "repair":
        required.append(owner.draft_category)
    return all(required)


def _md_escape(text: str) -> str:
    """Escape Markdown V1 special characters in user-supplied text."""
    for ch in ("\\", "*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_draft(owner: ServiceOwner) -> str:
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд"}
    type_label = type_map.get(
        owner.draft_service_type or "", owner.draft_service_type or "(не заполнено)"
    )
    e = _md_escape
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
    if owner.draft_service_type == "repair":
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
        lines.append("Диагностика: бесплатно")
    lines.append(f"Входит в стоимость: {'Да' if owner.draft_diag_included else 'Нет'}")
    lines += [
        f"Форма: {e(owner.draft_legal_form or '(не заполнено)')}",
        f"Налогообложение: {e(owner.draft_tax_system or '(не заполнено)')}",
        "",
        "Банковские реквизиты:",
        f"  Расч. счёт: {e(owner.draft_bank_account or '—')}",
        f"  Банк: {e(owner.draft_bank_name or '—')}",
        f"  БИК: {e(owner.draft_bik or '—')}",
        f"  Корр. счёт: {e(owner.draft_corr_account or '—')}",
        f"  Организация: {e(owner.draft_org_name or '—')}",
        f"  ИНН: {e(owner.draft_inn or '—')}",
        "",
        f"Статус: {owner.status}",
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
            f"{PARTNER_BOT_NAME}\n\nВы авторизованы как администратор.",
            reply_markup=admin_only_menu_kb(),
        )
        return

    if owner is None:
        await message.answer(
            f"Добро пожаловать в *{_md_escape(PARTNER_BOT_NAME)}*!\n\n"
            "🗺 *Service Map для партнёров*\n\n"
            "Мы — платформа, которая помогает клиентам находить "
            "ближайшие сервисные центры по ремонту и апгрейду электросамокатов.\n\n"
            "Зарегистрируйте свой сервис — и получайте заявки "
            "от клиентов автоматически.\n\n"
            "📋 *Как это работает:*\n"
            "1. Заполните анкету\n"
            "2. Пройдите модерацию\n"
            "3. Получайте заявки и управляйте ими прямо в боте\n\n"
            "Нажмите кнопку ниже, чтобы начать регистрацию.",
            reply_markup=reg_start_kb(),
        )
        return

    if owner.status == "ожидает":
        has_draft = bool(owner.draft_name)
        complete = _draft_complete(owner)
        if complete:
            await message.answer(
                "Ваша анкета отправлена на модерацию. Ожидайте одобрения.",
                reply_markup=partner_pending_menu_kb(
                    has_draft=False, is_admin=is_admin
                ),
            )
        else:
            await message.answer(
                "У вас есть незавершенная анкета. Продолжите заполнение.",
                reply_markup=partner_pending_menu_kb(has_draft=True, is_admin=is_admin),
            )
        return

    if owner.status == "отклонён":
        await message.answer(
            "Ваша заявка была отклонена. Обратитесь в поддержку.",
        )
        return

    if owner.status == "приостановлен":
        await message.answer("Ваш аккаунт приостановлен. Обратитесь в поддержку.")
        return

    # active
    svc_name = ""
    if owner.service_id:
        async with async_session() as session:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            svc_name = svc.name if svc else ""

    greeting = f"{_md_escape(PARTNER_BOT_NAME)} \u2014 {_md_escape(svc_name)}"
    await message.answer(
        f"{greeting}\n\nВыберите действие:",
        reply_markup=partner_main_menu_kb(is_admin=is_admin),
    )


@router.message(F.text == "Моя анкета")
async def show_draft(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Вы не зарегистрированы.", reply_markup=reg_start_kb())
        return
    await message.answer(_format_draft(owner))


@router.message(F.text == "Мой статус")
async def show_status(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner:
        await message.answer("Вы не зарегистрированы.")
        return

    if owner.status != "активный" or not owner.service_id:
        await message.answer(f"Статус: {owner.status}")
        return

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == owner.service_id))
        ).scalar_one_or_none()

    if not svc:
        await message.answer("Сервис не найден.")
        return

    e = _md_escape
    wd = _sort_days(svc.working_days)
    lines = [
        f"Название: {e(svc.name)}",
        f"Тип: {_TYPE_RU.get(svc.service_type, svc.service_type)}",
        f"Адрес: {e(svc.address or '-')}",
        f"Метро: {e(svc.nearest_metro or '-')}",
        f"Телефон: {e(svc.phone or '-')}",
        f"Telegram: {e(svc.telegram_handle or '-')}",
        f"Рабочие дни: {e(wd or '-')}",
        f"Время работы: {svc.open_time or '?'}\u2014{svc.close_time or '?'}",
        f"Гидроизоляция: {'Да' if svc.has_hydroisolation else 'Нет'}",
        f"Цена гидроизоляции: {e(svc.hydroisolation_price or '-')}",
        f"Диагностика: {int(svc.diagnostics_price) if svc.diagnostics_price else 0} руб.",
        f"Входит в стоимость: {'Да' if svc.diagnostics_included else 'Нет'}",
        f"Рейтинг: {svc.yandex_rating or '-'}",
        f"Доступен: {'Да' if svc.is_available else 'Нет'}",
    ]
    if svc.upgrade_categories:
        lines.append(
            f"Категории апгрейда: {e(svc.upgrade_categories.replace(',', ', '))}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "Поддержка")
async def cmd_support(message: types.Message, state: FSMContext) -> None:
    from bot.core.config import SUPPORT_USER, COOPERATION_USER
    from bot.ui.keyboards import support_kb

    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("Процедура прервана.")
    await message.answer(
        "Выберите тему обращения:",
        reply_markup=support_kb(SUPPORT_USER, COOPERATION_USER),
    )


@router.message(Command("admin"))
async def cmd_admin_mode(message: types.Message, state: FSMContext) -> None:
    uname = message.from_user.username or ""
    if uname.lower() not in ADMIN_USERNAMES:
        await message.answer("Недоступно.")
        return
    await state.clear()
    await message.answer(
        "Режим администратора.",
        reply_markup=admin_only_menu_kb(),
    )


@router.message(Command("client"))
async def cmd_client_mode(message: types.Message, state: FSMContext) -> None:
    uname = message.from_user.username or ""
    is_admin = uname.lower() in ADMIN_USERNAMES
    await state.clear()

    owner = await _get_owner(message.from_user.id)
    if owner and owner.status == "активный":
        svc_name = ""
        if owner.service_id:
            async with async_session() as session:
                svc = (
                    await session.execute(
                        select(Service).where(Service.id == owner.service_id)
                    )
                ).scalar_one_or_none()
                svc_name = svc.name if svc else ""
        greeting = f"{_md_escape(PARTNER_BOT_NAME)} \u2014 {_md_escape(svc_name)}"
        await message.answer(
            f"{greeting}\n\nВыберите действие:",
            reply_markup=partner_main_menu_kb(is_admin=is_admin),
        )
    elif owner and owner.status == "ожидает":
        has_draft = bool(owner.draft_name)
        complete = _draft_complete(owner)
        if complete:
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
            f"Добро пожаловать в {_md_escape(PARTNER_BOT_NAME)}!",
            reply_markup=reg_start_kb(),
        )
