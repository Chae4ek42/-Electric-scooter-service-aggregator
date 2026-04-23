"""Partner bot: admin panel for partner application moderation."""

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

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.core.database import async_session
from client_bot.core.formatting import e
from client_bot.domain.models import (
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
    ServiceOwnerSettings,
)
from client_bot.services.sheets_writer import update_service_row
from client_bot.texts import Btn, PARTNER_STATUS_RU, Partner, TYPE_RU
from partner_bot.ui.keyboards import (
    padm_main_kb,
    padm_partner_detail_kb,
    padm_partners_kb,
    partner_main_menu_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="partner_admin")

PARTNER_PAGE_SIZE = 10
_STATUS_RU = PARTNER_STATUS_RU


class IsAdmin(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        uname = event.from_user.username if event.from_user else None
        return bool(uname) and uname.lower() in ADMIN_USERNAMES


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _fmt_partner(owner: ServiceDraft) -> str:
    type_label = TYPE_RU.get(
        owner.draft_service_type or "", owner.draft_service_type or "—"
    )
    lines = [
        f"<b>Партнёр #</b><code>{owner.id}</code>",
        f"<b>Telegram ID:</b> <code>{owner.owner_user_id}</code>",
        f"<b>Статус:</b> {_STATUS_RU.get(owner.status, owner.status)}",
        "",
        f"<b>Название:</b> {e(owner.draft_name or '—')}",
        f"<b>Тип:</b> {type_label}",
    ]
    if owner.draft_service_type == "upgrade":
        cats = (owner.draft_upgrade_categories or "").replace(",", ", ") or "—"
        lines.append(f"<b>Категории апгрейда:</b> {e(cats)}")
    if owner.draft_service_type in ("repair", "complex"):
        lines.append(f"<b>Категория ремонта:</b> {e(owner.draft_category or '—')}")
    lines.append(
        f"<b>Гидроизоляция:</b> {'Да' if owner.draft_hydroisolation else 'Нет'}"
    )
    if owner.draft_hydroisolation:
        lines.append(f"<b>Цена гидроизоляции:</b> {e(owner.draft_hydro_price or '—')}")

    lines += [
        f"<b>Адрес:</b> {e(owner.draft_address or '—')}",
        f"<b>Метро:</b> {e(owner.draft_metro or '—')}",
        f"<b>Телефон:</b> {e(owner.draft_phone or '—')}",
        f"<b>Telegram:</b> {e(owner.draft_telegram or '—')}",
        f"<b>Рабочие дни:</b> {e((owner.draft_working_days or '').replace(',', ', ') or '—')}",
        f"<b>Часы:</b> {owner.draft_open_time or '?'}—{owner.draft_close_time or '?'}",
    ]
    if owner.draft_diagnostics_price is not None and owner.draft_diagnostics_price > 0:
        lines.append(f"<b>Диагностика:</b> {int(owner.draft_diagnostics_price)} ₽")
    else:
        lines.append("<b>Диагностика:</b> бесплатно")

    lines.append(
        f"<b>Входит в стоимость:</b> {'Да' if owner.draft_diag_included else 'Нет'}"
    )
    lines += [
        f"<b>Форма:</b> {e(owner.draft_legal_form or '—')}",
        f"<b>Налогообложение:</b> {e(owner.draft_tax_system or '—')}",
        "",
        "<b>Банковские реквизиты:</b>",
        f"  Р/с: {e(owner.draft_bank_account or '—')}",
        f"  Банк: {e(owner.draft_bank_name or '—')}",
        f"  БИК: {e(owner.draft_bik or '—')}",
        f"  К/с: {e(owner.draft_corr_account or '—')}",
        f"  Организация: {e(owner.draft_org_name or '—')}",
        f"  ИНН: {e(owner.draft_inn or '—')}",
    ]
    return "\n".join(lines)


async def _panel_stats() -> tuple[int, list[tuple[str, int]]]:
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(ServiceDraft)
                .where(ServiceDraft.registration_complete.is_(True))
            )
        ).scalar_one()
        stats_rows = (
            await session.execute(
                select(ServiceDraft.status, func.count(ServiceDraft.id))
                .where(ServiceDraft.registration_complete.is_(True))
                .group_by(ServiceDraft.status)
            )
        ).all()
    return total, [(status, int(cnt)) for status, cnt in stats_rows]


@router.message(F.text == Btn.ADMIN_PANEL)
async def padm_enter(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    total, stats_rows = await _panel_stats()
    lines = [f"<b>Партнёров:</b> {total}", ""]
    for status, cnt in stats_rows:
        lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await message.answer("\n".join(lines), reply_markup=padm_main_kb())


@router.callback_query(F.data == "padm:main")
async def padm_main(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    total, stats_rows = await _panel_stats()
    lines = [f"<b>Партнёров:</b> {total}", ""]
    for status, cnt in stats_rows:
        lines.append(f"• {_STATUS_RU.get(status, status)}: {cnt}")
    await cb.message.edit_text("\n".join(lines), reply_markup=padm_main_kb())
    await cb.answer()


@router.message(F.text == Btn.EXIT_PANEL)
async def padm_exit(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        Partner.Admin.EXIT,
        reply_markup=partner_main_menu_kb(is_admin=True),
    )


@router.callback_query(F.data.startswith("padm:partners:"))
async def padm_partners_list(cb: types.CallbackQuery) -> None:
    parts = cb.data.split(":")
    page = int(parts[2])
    status_filter = parts[4] if len(parts) >= 5 and parts[3] == "status" else None

    async with async_session() as session:
        q = select(ServiceDraft).where(ServiceDraft.registration_complete.is_(True))
        if status_filter:
            q = q.where(ServiceDraft.status == status_filter)

        all_owners = (
            (await session.execute(q.order_by(ServiceDraft.registered_at.desc())))
            .scalars()
            .all()
        )

    total = len(all_owners)
    owners = all_owners[page * PARTNER_PAGE_SIZE : (page + 1) * PARTNER_PAGE_SIZE]
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
        f"<b>{header}:</b> {total} (стр. {page + 1}/{total_pages})",
        reply_markup=padm_partners_kb(list(owners), page, total_pages, status_filter),
    )
    await cb.answer()


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
                text=label, callback_data=f"padm:partners:0:status:{key}"
            )
        ]
        for key, label in statuses
    ]
    rows.append([InlineKeyboardButton(text="Все", callback_data="padm:partners:0")])

    await cb.message.edit_text(
        "Фильтр по статусу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("padm:partner:"))
async def padm_partner_detail(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
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


@router.callback_query(F.data.startswith("padm:approve:"))
async def padm_approve_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    admin_username = cb.from_user.username or str(cb.from_user.id)

    svc_id: int | None = None
    partner_tg_id: int | None = None

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
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

        svc = None
        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()

        if svc is None:
            svc = Service(
                name=owner.draft_name or "Без названия",
                service_type=owner.draft_service_type or "repair",
                is_available=True,
            )
            session.add(svc)
            await session.flush()

        svc.name = owner.draft_name or "Без названия"
        svc.service_type = owner.draft_service_type or "repair"
        svc.category_id = cat_id
        svc.address = owner.draft_address
        svc.nearest_metro = owner.draft_metro
        svc.phone = owner.draft_phone
        svc.telegram_handle = owner.draft_telegram
        svc.open_time = owner.draft_open_time
        svc.close_time = owner.draft_close_time
        svc.has_hydroisolation = owner.draft_hydroisolation
        svc.hydroisolation_price = owner.draft_hydro_price
        svc.diagnostics_price = owner.draft_diagnostics_price
        svc.diagnostics_included = owner.draft_diag_included
        svc.upgrade_categories = owner.draft_upgrade_categories
        svc.working_days = owner.draft_working_days
        svc.is_available = True
        svc.registration_complete = True
        svc.partnership_status = "активный"

        owner.service_id = svc.id
        owner.status = "активный"
        owner.registration_complete = True
        owner.approved_at = datetime.datetime.now(tz=datetime.timezone.utc)
        owner.approved_by = admin_username

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
        bank.legal_form = owner.draft_legal_form
        bank.tax_system = owner.draft_tax_system
        bank.bank_account = owner.draft_bank_account
        bank.bank_name = owner.draft_bank_name
        bank.bik = owner.draft_bik
        bank.corr_account = owner.draft_corr_account
        bank.org_name = owner.draft_org_name
        bank.inn = owner.draft_inn

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
                    owner_user_id=owner.owner_user_id,
                )
            )
        else:
            settings.owner_user_id = owner.owner_user_id

        await session.commit()
        svc_id = svc.id
        partner_tg_id = owner.owner_user_id

    if svc_id is not None:
        async with async_session() as session:
            svc = (
                await session.execute(select(Service).where(Service.id == svc_id))
            ).scalar_one_or_none()
        if svc:
            try:
                update_service_row(svc)
            except Exception:
                logger.exception("Failed to update approved service in Sheets")

    await cb.answer("Партнёр одобрен!", show_alert=True)

    if partner_tg_id is not None:
        try:
            partner_uname = ""
            try:
                chat = await cb.bot.get_chat(partner_tg_id)
                partner_uname = chat.username or ""
            except Exception:
                pass
            partner_is_admin = (
                partner_uname.lower() in ADMIN_USERNAMES if partner_uname else False
            )
            await cb.bot.send_message(
                partner_tg_id,
                "Ваша заявка одобрена! Теперь вы можете принимать заявки.",
                reply_markup=partner_main_menu_kb(is_admin=partner_is_admin),
            )
        except Exception:
            logger.exception("Failed to notify partner about approval")

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


@router.callback_query(F.data.startswith("padm:reject_partner:"))
async def padm_reject_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])
    partner_tg_id: int | None = None

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner:
            await cb.answer("Партнёр не найден.", show_alert=True)
            return

        owner.status = "отклонён"
        partner_tg_id = owner.owner_user_id

        if owner.service_id:
            svc = (
                await session.execute(
                    select(Service).where(Service.id == owner.service_id)
                )
            ).scalar_one_or_none()
            if svc:
                svc.is_available = False
                svc.partnership_status = "отклонён"

        await session.commit()

    await cb.answer("Партнёр отклонён.", show_alert=True)

    if partner_tg_id is not None:
        try:
            await cb.bot.send_message(
                partner_tg_id,
                "Ваша заявка на регистрацию сервисного центра была отклонена.",
            )
        except Exception:
            logger.exception("Failed to notify partner about rejection")

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


@router.callback_query(F.data.startswith("padm:suspend:"))
async def padm_suspend_partner(cb: types.CallbackQuery) -> None:
    owner_id = int(cb.data.split(":")[2])

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
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
                svc.partnership_status = "приостановлен"

        await session.commit()

    await cb.answer("Партнёр приостановлен.", show_alert=True)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
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
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
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
                svc.partnership_status = "активный"

        await session.commit()

    await cb.answer("Партнёр восстановлен.", show_alert=True)

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.id == owner_id)
            )
        ).scalar_one_or_none()
    if owner:
        await cb.message.edit_text(
            _fmt_partner(owner),
            reply_markup=padm_partner_detail_kb(owner.id, owner.status),
        )


@router.callback_query(F.data == "padm:noop")
async def padm_noop(cb: types.CallbackQuery) -> None:
    await cb.answer()
