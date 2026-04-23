"""Partner bot: notification settings."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from sqlalchemy import select

from client_bot.core.database import async_session
from client_bot.domain.models import ServiceOwnerSettings
from client_bot.texts import Btn, Partner
from partner_bot.handlers.common import _get_owner
from partner_bot.ui.keyboards import notif_settings_kb

logger = logging.getLogger(__name__)
router = Router(name="partner_notifications")


async def _get_or_create_settings(owner_id: int) -> ServiceOwnerSettings:
    async with async_session() as session:
        settings = (
            await session.execute(
                select(ServiceOwnerSettings).where(
                    ServiceOwnerSettings.owner_id == owner_id
                )
            )
        ).scalar_one_or_none()
        if not settings:
            settings = ServiceOwnerSettings(owner_id=owner_id)
            session.add(settings)
            await session.commit()
            settings = (
                await session.execute(
                    select(ServiceOwnerSettings).where(
                        ServiceOwnerSettings.owner_id == owner_id
                    )
                )
            ).scalar_one_or_none()
    return settings


@router.message(F.text == Btn.NOTIF_SETTINGS)
async def notif_menu(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner or owner.status != "активный":
        await message.answer(Partner.Notifications.ACTIVE_ONLY)
        return
    settings = await _get_or_create_settings(owner.id)
    await message.answer(
        Partner.Notifications.HEADER,
        reply_markup=notif_settings_kb(settings.notif_new_order, settings.notif_cancel),
    )


@router.callback_query(F.data.startswith("notif:toggle:"))
async def toggle_notif(callback: types.CallbackQuery) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner or owner.status != "активный":
        await callback.answer(Partner.Notifications.UNAVAILABLE, show_alert=True)
        return

    field = callback.data.split(":")[2]  # new_order | cancel
    async with async_session() as session:
        settings = (
            await session.execute(
                select(ServiceOwnerSettings).where(
                    ServiceOwnerSettings.owner_id == owner.id
                )
            )
        ).scalar_one_or_none()
        if not settings:
            settings = ServiceOwnerSettings(owner_id=owner.id)
            session.add(settings)
            await session.flush()

        if field == "new_order":
            settings.notif_new_order = not settings.notif_new_order
        elif field == "cancel":
            settings.notif_cancel = not settings.notif_cancel
        await session.commit()

        new_order = settings.notif_new_order
        cancel = settings.notif_cancel

    await callback.message.edit_text(
        Partner.Notifications.HEADER,
        reply_markup=notif_settings_kb(new_order, cancel),
    )
    await callback.answer()
