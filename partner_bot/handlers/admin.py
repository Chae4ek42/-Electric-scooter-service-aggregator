"""Partner bot: admin panel — partner application management."""

from __future__ import annotations

import datetime
import logging
import math
from typing import Union

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select

from bot.core.config import ADMIN_USERNAMES
from bot.core.database import async_session
from bot.domain.models import Service, ServiceCategory, ServiceOwner
from bot.services.sheets_writer import add_service_row
from partner_bot.ui.keyboards import (
    padm_main_kb,
    padm_partner_detail_kb,
    padm_partners_kb,
    partner_main_menu_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_admin")

PARTNER_PAGE_SIZE = 10


# ── Filter ────────────────────────────────────────────────────


class IsAdmin(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        uname = event.from_user.username if event.from_user else None
        return bool(uname) and uname.lower() in ADMIN_USERNAMES


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ── Helpers ───────────────────────────────────────────────────

_STATUS_RU = {
    "ожидает": "Ожидает модерации",
    "активный": "Активный",
    "отклонён": "Отклонён",
    "приостановлен": "Приостановлен",
}


def _md_escape(text: str) -> str:
    """Escape Markdown V1 special characters in user-supplied text."""
    for ch in ("\\", "*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _fmt_partner(owner: ServiceOwner) -> str:
    type_map = {"repair": "Ремонт", "upgrade": "Апгрейд"}
    type_label = type_map.get(
        owner.draft_service_type or "", owner.draft_service_type or "—"
    )
    e = _md_escape
    lines = [
        f"*Партнёр #*`{owner.id}`",
        f"*Telegram ID:* `{owner.telegram_id}`",
        f"*Статус:* {_STATUS_RU.get(owner.status, owner.status)}",
        "",
        f"*Название:* {e(owner.draft_name or '—')}",
        f"*Тип:* {type_label}",
    ]
    if owner.draft_service_type == "upgrade":
        cats = (owner.draft_upgrade_categories or "").replace(",", ", ") or "—"
        lines.append(f"*Категории апгрейда:* {e(cats)}")
    if owner.draft_service_type == "repair":
        lines.append(f"*Категория ремонта:* {e(owner.draft_category or '—')}")
    lines.append(f"*Гидроизоляция:* {'Да' if owner.draft_hydroisolation else 'Нет'}")
    if owner.draft_hydroisolation:
        lines.append(f"*Цена гидроизоляции:* {e(owner.draft_hydro_price or '—')}")
    lines += [
        f"*Адрес:* {e(owner.draft_address or '—')}",
        f"*Метро:* {e(owner.draft_metro or '—')}",
        f"*Телефон:* {e(owner.draft_phone or '—')}",
        f"*Telegram:* {e(owner.draft_telegram or '—')}",
        f"*Рабочие дни:* {e((owner.draft_working_days or '').replace(',', ', ') or '—')}",
        f"*Часы:* {owner.draft_open_time or '?'}—{owner.draft_close_time or '?'}",
    ]
    if owner.draft_diagnostics_price is not None and owner.draft_diagnostics_price > 0:
        lines.append(f"*Диагностика:* {int(owner.draft_diagnostics_price)} ₽")
    else:
        lines.append("*Диагностика:* бесплатно")
    lines.append(
        f"*Входит в стоимость:* {'Да' if owner.draft_diag_included else 'Нет'}"
    )
    lines += [
        f"*Форма:* {e(owner.draft_legal_form or '—')}",
        f"*Налогообложение:* {e(owner.draft_tax_system or '—')}",
        "",
        "*Банковские реквизиты:*",
        f"  Р/с: {e(owner.draft_bank_account or '—')}",
        f"  Банк: {e(owner.draft_bank_name or '—')}",
        f"  БИК: {e(owner.draft_bik or '—')}",
        f"  К/с: {e(owner.draft_corr_account or '—')}",
        f"  Организация: {e(owner.draft_org_name or '—')}",
        f"  ИНН: {e(owner.draft_inn or '—')}",
    ]
    return "\n".join(lines)


async def _send_or_edit(
    event: Union[types.Message, types.CallbackQuery],
    text: str,
    markup,
) -> None:
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


# ══════════════════════════════════════════════════════════════
# Вход в панель
# ══════════════════════════════════════════════════════════════


@router.message(F.text == "Панель администратора")
async def padm_enter(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(ServiceOwner))
        ).scalar_one()
        stats_rows = await session.execute(
            select(ServiceOwner.status, func.count(ServiceOwner.id)).group_by(
                ServiceOwner.status
            )
        )
    stat_lines = [f"*Партнёры:* {total}", ""]
    for status, cnt in stats_rows:
        stat_lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await message.answer("\n".join(stat_lines), reply_markup=padm_main_kb())


# ══════════════════════════════════════════════════════════════
# Главное меню
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "padm:main")
async def padm_main(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(ServiceOwner))
        ).scalar_one()
        stats_rows = await session.execute(
            select(ServiceOwner.status, func.count(ServiceOwner.id)).group_by(
                ServiceOwner.status
            )
        )
    stat_lines = [f"*Партнёры:* {total}", ""]
    for status, cnt in stats_rows:
        stat_lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await cb.message.edit_text("\n".join(stat_lines), reply_markup=padm_main_kb())
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Выход из панели
# ══════════════════════════════════════════════════════════════


@router.message(F.text == "Выйти из панели")
async def padm_exit(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Вы вышли из панели администратора.",
        reply_markup=partner_main_menu_kb(is_admin=True),
    )


# ══════════════════════════════════════════════════════════════
# Список партнёров (с фильтром по статусу)
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("padm:partners:"))
async def padm_partners_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    page = int(parts[2])
    status_filter = parts[4] if len(parts) >= 5 and parts[3] == "status" else None

    async with async_session() as session:
        q = select(ServiceOwner)
        cq = select(func.count()).select_from(ServiceOwner)
        if status_filter:
            q = q.where(ServiceOwner.status == status_filter)
            cq = cq.where(ServiceOwner.status == status_filter)
        total = (await session.execute(cq)).scalar_one()
        owners = (
            (
                await session.execute(
                    q.order_by(ServiceOwner.registered_at.desc())
                    .offset(page * PARTNER_PAGE_SIZE)
                    .limit(PARTNER_PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    total_pages = max(1, math.ceil(total / PARTNER_PAGE_SIZE))
    if not owners:
        try:
            await cb.message.edit_text(
                "Нет заявок партнёров.", reply_markup=padm_main_kb()
            )
        except TelegramBadRequest:
            pass
        await cb.answer()
        return
    header = (
        "Все партнёры"
        if not status_filter
        else f"Статус: {_STATUS_RU.get(status_filter, status_filter)}"
    )
    await cb.message.edit_text(
        f"*{header}:* {total} (стр. {page + 1}/{total_pages})",
        reply_markup=padm_partners_kb(list(owners), page, total_pages, status_filter),
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Фильтр по статусу
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "padm:filter")
async def padm_filter(cb: types.CallbackQuery) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    statuses = [
        ("ожидает", "Ожид. модерации"),
        ("активный", "Активные"),
        ("приостановлен", "Приостановленные"),
        ("отклонён", "Отклонённые"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"padm:partners:0:status:{key}",
            )
        ]
        for key, label in statuses
    ]
    rows.append([InlineKeyboardButton(text="Все", callback_data="padm:partners:0")])
    await cb.message.edit_text(
        "Фильтр по статусу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Карточка партнёра
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("padm:partner:"))
async def padm_partner_detail(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
    if not owner:
        await cb.answer("Партнёр не найден.", show_alert=True)
        return
    await cb.message.edit_text(
        _fmt_partner(owner),
        reply_markup=padm_partner_detail_kb(owner.id, owner.status),
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════
# Одобрить партнёра
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("padm:approve:"))
async def padm_approve_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    admin_username = cb.from_user.username or str(cb.from_user.id)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Партнёр не найден.", show_alert=True)
            return
        if owner.status != "ожидает":
            await cb.answer("Нельзя одобрить — статус не 'ожидает'.", show_alert=True)
            return

        cat_id = None
        if owner.draft_category:
            cat = (
                await session.execute(
                    select(ServiceCategory).where(
                        ServiceCategory.name == owner.draft_category
                    )
                )
            ).scalar_one_or_none()
            if cat:
                cat_id = cat.id

        svc = Service(
            name=owner.draft_name or "Без названия",
            service_type=owner.draft_service_type or "repair",
            category_id=cat_id,
            address=owner.draft_address,
            nearest_metro=owner.draft_metro,
            phone=owner.draft_phone,
            telegram_handle=owner.draft_telegram,
            open_time=owner.draft_open_time,
            close_time=owner.draft_close_time,
            has_hydroisolation=owner.draft_hydroisolation,
            hydroisolation_price=owner.draft_hydro_price,
            diagnostics_price=owner.draft_diagnostics_price,
            diagnostics_included=owner.draft_diag_included,
            upgrade_categories=owner.draft_upgrade_categories,
            working_days=owner.draft_working_days,
            is_available=True,
            yandex_rating=0.0,
            partnership_status="активный",
        )
        session.add(svc)
        await session.flush()

        owner.service_id = svc.id
        owner.status = "активный"
        owner.approved_at = datetime.datetime.now(tz=datetime.timezone.utc)
        owner.approved_by = admin_username
        await session.commit()

        svc_id = svc.id

    # Write to Google Sheets
    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == svc_id))
        ).scalar_one_or_none()
    if svc:
        try:
            add_service_row(svc)
        except Exception:
            logger.exception("Failed to write approved service to Sheets")

    logger.info(
        "admin %s approved partner #%s, created service #%s",
        admin_username,
        owner_id,
        svc_id,
    )
    await cb.answer("Партнёр одобрен!", show_alert=True)

    # Notify partner (same bot — direct message) with new keyboard
    try:
        bot = cb.bot
        partner_uname = ""
        try:
            chat = await bot.get_chat(owner.telegram_id)
            partner_uname = chat.username or ""
        except Exception:
            pass
        partner_is_admin = (
            partner_uname.lower() in ADMIN_USERNAMES if partner_uname else False
        )
        await bot.send_message(
            owner.telegram_id,
            "Ваша заявка одобрена! Теперь вы можете принимать заявки.",
            reply_markup=partner_main_menu_kb(is_admin=partner_is_admin),
        )
    except Exception:
        logger.exception("Failed to notify partner about approval")

    # Refresh view
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


# ══════════════════════════════════════════════════════════════
# Отклонить партнёра
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("padm:reject_partner:"))
async def padm_reject_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Партнёр не найден.", show_alert=True)
            return
        owner.status = "отклонён"
        await session.commit()

    logger.info("admin rejected partner #%s", owner_id)
    await cb.answer("Партнёр отклонён.", show_alert=True)

    try:
        await cb.bot.send_message(
            owner.telegram_id,
            "Ваша заявка на регистрацию сервисного центра была отклонена.",
        )
    except Exception:
        logger.exception("Failed to notify partner about rejection")

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


# ══════════════════════════════════════════════════════════════
# Приостановить / восстановить
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("padm:suspend:"))
async def padm_suspend_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Не найден.", show_alert=True)
            return
        owner.status = "приостановлен"
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = False
        await session.commit()
    logger.info("admin suspended partner #%s", owner_id)
    await cb.answer("Партнёр приостановлен.", show_alert=True)
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


@router.callback_query(F.data.startswith("padm:unsuspend:"))
async def padm_unsuspend_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Не найден.", show_alert=True)
            return
        owner.status = "активный"
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = True
        await session.commit()
    logger.info("admin unsuspended partner #%s", owner_id)
    await cb.answer("Партнёр восстановлен.", show_alert=True)
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceOwner).where(ServiceOwner.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


# ══════════════════════════════════════════════════════════════
# No-op
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "padm:noop")
async def padm_noop(cb: types.CallbackQuery) -> None:
    await cb.answer()
