from __future__ import annotations

import datetime
import os
import sys
import uuid

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import (
    Brand,
    Model,
    Order,
    Service,
    ServiceCategory,
    ServiceDraft,
    User,
)


def uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def uniq_user_id() -> int:
    return int(uuid.uuid4().int % 9_000_000_000_000) + 1_000_000_000_000


async def init_db_once() -> None:
    from client_bot.services.seed import init_db

    await init_db()


async def create_order_bundle(
    *,
    status: str = "awaiting_payment",
    service_type: str = "repair",
    repair_category: str | None = None,
    upgrade_category: str | None = None,
    hydro_price: str | None = None,
    diagnostics_price: float | None = None,
    diagnostics_included: bool = False,
    created_at: datetime.datetime | None = None,
) -> dict[str, int]:
    async with async_session() as session:
        brand = Brand(name=uniq("brand"))
        model = Model(name=uniq("model"), brand=brand)

        category = None
        if repair_category:
            category = (
                await session.execute(
                    select(ServiceCategory).where(
                        ServiceCategory.name == repair_category
                    )
                )
            ).scalar_one_or_none()
            if category is None:
                category = ServiceCategory(name=repair_category)
                session.add(category)
                await session.flush()

        service = Service(
            name=uniq("service"),
            service_type=service_type,
            category_id=category.id if category else None,
            is_available=True,
            registration_complete=True,
            partnership_status="активный",
            address="Москва, тестовый адрес, 1",
            nearest_metro="Арбатская",
            phone="+79990000000",
            telegram_handle="@svc_test",
            open_time="10:00",
            close_time="21:00",
            working_days="Пн,Вт,Ср,Чт,Пт,Сб,Вс",
            has_hydroisolation=bool(hydro_price),
            hydroisolation_price=hydro_price,
            diagnostics_price=diagnostics_price,
            diagnostics_included=diagnostics_included,
            upgrade_categories=(
                "Окраска,Прошивка" if service_type in ("upgrade", "complex") else None
            ),
        )

        user = User(
            id=uniq_user_id(),
            username=uniq("user"),
            full_name="Scenario User",
        )

        order = Order(
            user=user,
            service=service,
            model=model,
            status=status,
            metro_station="Арбатская",
            scheduled_date="15.11.2030",
            scheduled_time="14:00",
            upgrade_category=upgrade_category,
            diagnostics_price=diagnostics_price,
            created_at=created_at or datetime.datetime.now(datetime.timezone.utc),
        )

        session.add_all([brand, model, service, user, order])
        await session.commit()

        return {
            "order_id": order.id,
            "service_id": service.id,
            "user_id": user.id,
            "model_id": model.id,
            "brand_id": brand.id,
        }


async def create_active_owner_bundle(*, service_type: str = "repair") -> dict[str, int]:
    async with async_session() as session:
        category = (
            await session.execute(
                select(ServiceCategory).where(ServiceCategory.name == "Механика")
            )
        ).scalar_one_or_none()
        if category is None:
            category = ServiceCategory(name="Механика")
            session.add(category)
            await session.flush()

        service = Service(
            name=uniq("partner_service"),
            service_type=service_type,
            category_id=category.id if service_type in ("repair", "complex") else None,
            is_available=True,
            registration_complete=True,
            partnership_status="активный",
            address="Москва, Партнерская, 10",
            nearest_metro="Курская",
            phone="+79998887766",
            telegram_handle="@partner_service",
            open_time="09:00",
            close_time="20:00",
            has_hydroisolation=False,
            diagnostics_price=500,
            diagnostics_included=False,
            working_days="Пн,Вт,Ср,Чт,Пт",
        )
        session.add(service)
        await session.flush()

        owner_id = uniq_user_id()
        owner = ServiceDraft(
            owner_user_id=owner_id,
            service_id=service.id,
            status="активный",
            registration_complete=True,
            draft_name=service.name,
            draft_service_type=service.service_type,
            draft_category=(
                "Механика" if service_type in ("repair", "complex") else None
            ),
            draft_address=service.address,
            draft_metro=service.nearest_metro,
            draft_phone=service.phone,
            draft_telegram=service.telegram_handle,
            draft_open_time=service.open_time,
            draft_close_time=service.close_time,
            draft_hydroisolation=False,
            draft_hydro_price=None,
            draft_diagnostics_price=500,
            draft_diag_included=False,
            draft_upgrade_categories=(
                "Окраска" if service_type in ("upgrade", "complex") else None
            ),
            draft_working_days=service.working_days,
        )
        session.add(owner)
        await session.commit()

        return {
            "owner_user_id": owner_id,
            "service_id": service.id,
            "owner_id": owner.id,
        }
