"""Partner bot: notification settings."""

from __future__ import annotations

import logging

from aiogram import F, Router, types

from client_bot.core.database import async_session
from client_bot.services.notification_settings import get_or_create_owner_settings
from client_bot.texts import Btn, Partner
from partner_bot.handlers.common import _get_owner
from partner_bot.ui.keyboards import notif_settings_kb

logger = logging.getLogger(__name__)
router = Router(name="partner_notifications")

_TOGGLE_TO_ATTR = {
    "enabled": "notif_enabled",
    "new_order": "notif_new_order",
    "cancel": "notif_cancel",
    "client_comment": "notif_client_comment",
    "estimate": "notif_estimate",
    "dispute": "notif_dispute",
    "completed": "notif_completed",
}


def _settings_markup(settings) -> types.InlineKeyboardMarkup:
    return notif_settings_kb(
        enabled=bool(settings.notif_enabled),
        new_order=bool(settings.notif_new_order),
        cancel=bool(settings.notif_cancel),
        client_comment=bool(settings.notif_client_comment),
        estimate=bool(settings.notif_estimate),
        dispute=bool(settings.notif_dispute),
        completed=bool(settings.notif_completed),
    )


def _apply_preset(settings, preset: str) -> bool:
    if preset == "all_on":
        value = True
    elif preset == "all_off":
        value = False
    else:
        return False

    settings.notif_enabled = value
    settings.notif_new_order = value
    settings.notif_cancel = value
    settings.notif_client_comment = value
    settings.notif_estimate = value
    settings.notif_dispute = value
    settings.notif_completed = value
    return True


@router.message(F.text == Btn.NOTIF_SETTINGS)
async def notif_menu(message: types.Message) -> None:
    owner = await _get_owner(message.from_user.id)
    if not owner or owner.status != "активный" or owner.service_id is None:
        await message.answer(Partner.Notifications.ACTIVE_ONLY)
        return
    async with async_session() as session:
        settings = await get_or_create_owner_settings(
            session,
            service_id=owner.service_id,
            owner_user_id=owner.owner_user_id,
        )
        await session.commit()
    await message.answer(
        "Настройки уведомлений сервиса:\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_settings_markup(settings),
    )


@router.callback_query(F.data.startswith("notif:toggle:"))
async def toggle_notif(callback: types.CallbackQuery) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner or owner.status != "активный" or owner.service_id is None:
        await callback.answer(Partner.Notifications.UNAVAILABLE, show_alert=True)
        return

    field = callback.data.split(":")[2]
    attr = _TOGGLE_TO_ATTR.get(field)
    if attr is None:
        await callback.answer("Неизвестная настройка.", show_alert=True)
        return

    async with async_session() as session:
        settings = await get_or_create_owner_settings(
            session,
            service_id=owner.service_id,
            owner_user_id=owner.owner_user_id,
        )

        current = bool(getattr(settings, attr))
        setattr(settings, attr, not current)
        await session.commit()

    await callback.message.edit_text(
        "Настройки уведомлений сервиса:\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_settings_markup(settings),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("notif:preset:"))
async def notif_preset(callback: types.CallbackQuery) -> None:
    owner = await _get_owner(callback.from_user.id)
    if not owner or owner.status != "активный" or owner.service_id is None:
        await callback.answer(Partner.Notifications.UNAVAILABLE, show_alert=True)
        return

    preset = callback.data.split(":")[2]
    async with async_session() as session:
        settings = await get_or_create_owner_settings(
            session,
            service_id=owner.service_id,
            owner_user_id=owner.owner_user_id,
        )
        if not _apply_preset(settings, preset):
            await callback.answer("Неизвестный пресет.", show_alert=True)
            return
        await session.commit()

    await callback.message.edit_text(
        "Настройки уведомлений сервиса:\n\n"
        "[v] = уведомление включено, [ ] = выключено.",
        reply_markup=_settings_markup(settings),
    )
    await callback.answer("Настройки обновлены")
