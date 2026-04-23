from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from client_bot.domain.models import Order, OrderStatusHistory
from client_bot.domain.order_rules import ensure_order_transition

logger = logging.getLogger("esas.business.order_lifecycle")

ACTOR_CLIENT = "client"
ACTOR_PARTNER = "partner"
ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def _normalize_actor(actor: str | None) -> str:
    value = (actor or "").strip()
    return value or ACTOR_SYSTEM


async def transition_order_status(
    session: AsyncSession,
    order: Order,
    new_status: str,
    *,
    actor: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotent: bool = True,
) -> bool:
    current_status = order.status
    if current_status == new_status and idempotent:
        return False

    ensure_order_transition(current_status, new_status)
    order.status = new_status
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            from_status=current_status,
            to_status=new_status,
            actor=_normalize_actor(actor),
            reason=reason,
            metadata_json=_serialize_metadata(metadata),
        )
    )
    logger.info(
        "ORDER_STATUS_TRANSITION | order=%s | from=%s | to=%s | actor=%s",
        order.id,
        current_status,
        new_status,
        actor,
    )
    return True


async def transition_order_status_by_id(
    session: AsyncSession,
    order_id: int,
    new_status: str,
    *,
    actor: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotent: bool = True,
) -> bool:
    order = (
        await session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        return False
    return await transition_order_status(
        session,
        order,
        new_status,
        actor=actor,
        reason=reason,
        metadata=metadata,
        idempotent=idempotent,
    )


async def cancel_expired_awaiting_payment(
    session: AsyncSession,
    cutoff: datetime.datetime,
    *,
    actor: str = f"{ACTOR_SYSTEM}:payment_expire",
) -> int:
    rows = (
        (
            await session.execute(
                select(Order)
                .where(Order.status == "awaiting_payment")
                .where(Order.created_at < cutoff)
            )
        )
        .scalars()
        .all()
    )

    changed = 0
    for order in rows:
        transitioned = await transition_order_status(
            session,
            order,
            "cancelled",
            actor=actor,
            reason="payment_timeout",
            metadata={"cutoff": cutoff.isoformat()},
            idempotent=True,
        )
        if transitioned:
            changed += 1
    return changed
