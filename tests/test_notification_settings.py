from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import AdminNotificationSettings, User
from client_bot.services import notification_settings as notif_settings
from tests._helpers import init_db_once, uniq_user_id


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


def test_partner_notification_enabled_with_master_switch() -> None:
    settings = SimpleNamespace(
        notif_enabled=False,
        notif_new_order=True,
        notif_cancel=True,
        notif_client_comment=True,
        notif_estimate=True,
        notif_dispute=True,
        notif_completed=True,
    )

    assert (
        notif_settings.is_partner_notification_enabled(settings, "new_order") is False
    )

    settings.notif_enabled = True
    settings.notif_estimate = False
    assert notif_settings.is_partner_notification_enabled(settings, "estimate") is False
    assert (
        notif_settings.is_partner_notification_enabled(settings, "client_cancel")
        is True
    )


def test_admin_notification_enabled_scope_specific() -> None:
    settings = SimpleNamespace(
        notif_enabled=True,
        notif_client_dispute=False,
        notif_client_cancel=True,
        notif_no_center=True,
        notif_order_completed=True,
        notif_partner_application=False,
        notif_partner_profile_update=True,
        notif_partner_status_change=True,
    )

    assert (
        notif_settings.is_admin_notification_enabled(
            settings,
            scope=notif_settings.ADMIN_SCOPE_CLIENT,
            event_key="dispute",
        )
        is False
    )
    assert (
        notif_settings.is_admin_notification_enabled(
            settings,
            scope=notif_settings.ADMIN_SCOPE_PARTNER,
            event_key="partner_application",
        )
        is False
    )


@pytest.mark.asyncio
async def test_get_admin_recipients_respects_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notif_settings,
        "ADMIN_USERNAMES",
        {"notif_admin_one", "notif_admin_two"},
    )

    admin_one_id = uniq_user_id()
    admin_two_id = uniq_user_id()

    async with async_session() as session:
        session.add_all(
            [
                User(
                    id=admin_one_id,
                    username="notif_admin_one",
                    full_name="Admin One",
                ),
                User(
                    id=admin_two_id,
                    username="notif_admin_two",
                    full_name="Admin Two",
                ),
            ]
        )
        session.add(
            AdminNotificationSettings(
                admin_user_id=admin_one_id,
                scope=notif_settings.ADMIN_SCOPE_CLIENT,
                notif_enabled=True,
                notif_client_dispute=False,
            )
        )
        await session.commit()

    async with async_session() as session:
        recipients = await notif_settings.get_admin_recipients_for_event(
            session,
            scope=notif_settings.ADMIN_SCOPE_CLIENT,
            event_key="dispute",
        )

    recipient_ids = {user.id for user in recipients}
    assert admin_one_id not in recipient_ids
    assert admin_two_id in recipient_ids


def test_notification_keyboards_build() -> None:
    from client_bot.ui.keyboards import adm_notif_settings_kb
    from partner_bot.ui.keyboards import notif_settings_kb, padm_notif_settings_kb

    partner_kb = notif_settings_kb(
        enabled=True,
        new_order=True,
        cancel=False,
        client_comment=True,
        estimate=False,
        dispute=True,
        completed=True,
    )
    client_admin_kb = adm_notif_settings_kb(
        enabled=True,
        dispute=True,
        client_cancel=False,
        no_center=True,
        completed=True,
    )
    partner_admin_kb = padm_notif_settings_kb(
        enabled=True,
        partner_application=True,
        profile_update=False,
        status_change=True,
    )

    partner_labels = [btn.text for row in partner_kb.inline_keyboard for btn in row]
    client_admin_labels = [
        btn.text for row in client_admin_kb.inline_keyboard for btn in row
    ]
    partner_admin_labels = [
        btn.text for row in partner_admin_kb.inline_keyboard for btn in row
    ]

    assert "[v] Все уведомления" in partner_labels
    assert "[ ] Отмены клиентом" in partner_labels
    assert "[ ] Отмены клиентом" in client_admin_labels
    assert "[ ] Изменения профиля партнёра" in partner_admin_labels
