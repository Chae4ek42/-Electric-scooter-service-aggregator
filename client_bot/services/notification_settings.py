from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from client_bot.core.config import ADMIN_USERNAMES
from client_bot.domain.models import (
    AdminNotificationSettings,
    ServiceOwnerSettings,
    User,
)

ADMIN_SCOPE_CLIENT = "client"
ADMIN_SCOPE_PARTNER = "partner"

PARTNER_EVENT_TO_ATTR: dict[str, str] = {
    "new_order": "notif_new_order",
    "client_cancel": "notif_cancel",
    "client_comment": "notif_client_comment",
    "estimate": "notif_estimate",
    "dispute": "notif_dispute",
    "completed": "notif_completed",
}

CLIENT_ADMIN_EVENT_TO_ATTR: dict[str, str] = {
    "dispute": "notif_client_dispute",
    "client_cancel": "notif_client_cancel",
    "no_center": "notif_no_center",
    "completed": "notif_order_completed",
}

PARTNER_ADMIN_EVENT_TO_ATTR: dict[str, str] = {
    "partner_application": "notif_partner_application",
    "profile_update": "notif_partner_profile_update",
    "status_change": "notif_partner_status_change",
}


def is_partner_notification_enabled(
    settings: ServiceOwnerSettings | None,
    event_key: str | None,
) -> bool:
    if event_key is None:
        return True
    if settings is None:
        return True
    if not settings.notif_enabled:
        return False
    attr_name = PARTNER_EVENT_TO_ATTR.get(event_key)
    if attr_name is None:
        return True
    return bool(getattr(settings, attr_name, True))


def is_admin_notification_enabled(
    settings: AdminNotificationSettings | None,
    *,
    scope: str,
    event_key: str | None,
) -> bool:
    if event_key is None:
        return True
    if settings is None:
        return True
    if not settings.notif_enabled:
        return False

    if scope == ADMIN_SCOPE_CLIENT:
        attr_name = CLIENT_ADMIN_EVENT_TO_ATTR.get(event_key)
    elif scope == ADMIN_SCOPE_PARTNER:
        attr_name = PARTNER_ADMIN_EVENT_TO_ATTR.get(event_key)
    else:
        return True

    if attr_name is None:
        return True
    return bool(getattr(settings, attr_name, True))


async def get_or_create_owner_settings(
    session: AsyncSession,
    *,
    service_id: int,
    owner_user_id: int,
) -> ServiceOwnerSettings:
    settings = (
        await session.execute(
            select(ServiceOwnerSettings).where(
                ServiceOwnerSettings.service_id == service_id
            )
        )
    ).scalar_one_or_none()

    if settings is None:
        settings = ServiceOwnerSettings(
            service_id=service_id,
            owner_user_id=owner_user_id,
        )
        session.add(settings)
        await session.flush()
        return settings

    if settings.owner_user_id != owner_user_id:
        settings.owner_user_id = owner_user_id

    return settings


async def get_or_create_admin_settings(
    session: AsyncSession,
    *,
    admin_user_id: int,
    scope: str,
) -> AdminNotificationSettings:
    settings = (
        await session.execute(
            select(AdminNotificationSettings)
            .where(AdminNotificationSettings.admin_user_id == admin_user_id)
            .where(AdminNotificationSettings.scope == scope)
        )
    ).scalar_one_or_none()

    if settings is None:
        settings = AdminNotificationSettings(
            admin_user_id=admin_user_id,
            scope=scope,
        )
        session.add(settings)
        await session.flush()
        return settings

    return settings


async def get_admin_recipients_for_event(
    session: AsyncSession,
    *,
    scope: str,
    event_key: str,
) -> list[User]:
    admin_usernames = [name.strip().lower() for name in ADMIN_USERNAMES if name.strip()]
    if not admin_usernames:
        return []

    admins = (
        (
            await session.execute(
                select(User).where(func.lower(User.username).in_(admin_usernames))
            )
        )
        .scalars()
        .all()
    )
    if not admins:
        return []

    admin_ids = [admin.id for admin in admins]
    settings_rows = (
        (
            await session.execute(
                select(AdminNotificationSettings)
                .where(AdminNotificationSettings.scope == scope)
                .where(AdminNotificationSettings.admin_user_id.in_(admin_ids))
            )
        )
        .scalars()
        .all()
    )
    settings_by_admin = {row.admin_user_id: row for row in settings_rows}

    return [
        admin
        for admin in admins
        if is_admin_notification_enabled(
            settings_by_admin.get(admin.id),
            scope=scope,
            event_key=event_key,
        )
    ]
