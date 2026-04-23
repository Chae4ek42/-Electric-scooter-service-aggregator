from __future__ import annotations

import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    models: Mapped[list["Model"]] = relationship(
        back_populates="brand", lazy="selectin"
    )


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    brand: Mapped["Brand"] = relationship(back_populates="models", lazy="selectin")


class ServiceCategory(Base):
    __tablename__ = "service_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    services: Mapped[list["Service"]] = relationship(
        back_populates="category_rel", lazy="selectin"
    )


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_categories.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    # значения: 'repair' | 'upgrade' | 'complex'
    service_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    yandex_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_metro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    telegram_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    partnership_status: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Время работы (формат "HH:MM")
    open_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    close_time: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # Гидроизоляция
    has_hydroisolation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    hydroisolation_price: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Диагностика
    diagnostics_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnostics_included: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    main_brand_scooter: Mapped[str | None] = mapped_column(String(200), nullable=True)
    upgrade_categories: Mapped[str | None] = mapped_column(String(500), nullable=True)
    working_days: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pause_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Анкета завершена (True для импортированных из Sheets и одобренных партнёров)
    registration_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    category_rel: Mapped["ServiceCategory | None"] = relationship(
        back_populates="services", lazy="selectin"
    )
    bank_details: Mapped["ServiceBankDetails | None"] = relationship(
        back_populates="service",
        uselist=False,
        lazy="selectin",
    )
    owner_settings: Mapped["ServiceOwnerSettings | None"] = relationship(
        back_populates="service",
        uselist=False,
        lazy="selectin",
    )
    draft_links: Mapped[list["ServiceDraft"]] = relationship(
        back_populates="service",
        lazy="selectin",
    )


class ServiceDraft(Base):
    __tablename__ = "service_drafts"
    __table_args__ = (
        Index(
            "ix_service_drafts_status_registration_complete",
            "status",
            "registration_complete",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="ожидает", nullable=False)
    registered_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Draft fields
    draft_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    draft_service_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    draft_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    draft_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    draft_metro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    draft_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_telegram: Mapped[str | None] = mapped_column(String(200), nullable=True)
    draft_open_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    draft_close_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    draft_hydroisolation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    draft_hydro_price: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_diagnostics_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    draft_diag_included: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    draft_upgrade_categories: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    draft_working_days: Mapped[str | None] = mapped_column(String(100), nullable=True)
    draft_legal_form: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_tax_system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    draft_bank_account: Mapped[str | None] = mapped_column(String(30), nullable=True)
    draft_bank_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    draft_bik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    draft_corr_account: Mapped[str | None] = mapped_column(String(30), nullable=True)
    draft_org_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    draft_inn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    registration_complete: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    service: Mapped["Service | None"] = relationship(
        back_populates="draft_links",
        lazy="selectin",
    )


class ServiceBankDetails(Base):
    __tablename__ = "service_bank_details"

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id"),
        primary_key=True,
    )
    legal_form: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(30), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    corr_account: Mapped[str | None] = mapped_column(String(30), nullable=True)
    org_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    service: Mapped["Service"] = relationship(
        back_populates="bank_details",
        lazy="selectin",
    )


class MetroStation(Base):
    __tablename__ = "metro_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    line: Mapped[str] = mapped_column(String(200), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    full_name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="user", lazy="selectin")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index(
            "ix_orders_service_status_created_at",
            "service_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_orders_user_status_created_at",
            "user_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id"), nullable=True
    )
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True)
    metro_station: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    scheduled_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    #   awaiting_payment   — создана, ожидает предоплаты
    #   accepted           — принята партнёром
    #   in_progress        — в работе (заполнена смета)
    #   ready_for_pickup   — готов к выдаче
    #   completed          — завершена успешно
    #   cancelled          — отменена
    #   rejected_by_partner — отклонена сервисом
    #   interrupted        — клиент не пришёл
    #   client_refused     — клиент отказался от ремонта
    #   disputed           — оспорена клиентом
    order_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_payment")
    payment_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_custom_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    brand_custom_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    problem_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    upgrade_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    diagnostics_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    partner_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Смета
    estimate_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimate_items: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimate_deadline: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimate_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Обратная связь клиента
    client_visited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    client_confirmed_estimate: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    dispute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="orders", lazy="selectin")
    service: Mapped["Service"] = relationship(lazy="selectin")
    model: Mapped["Model"] = relationship(lazy="selectin")
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    order: Mapped["Order"] = relationship(
        back_populates="status_history", lazy="selectin"
    )


class UserAction(Base):
    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="success")
    error_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_response_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ServiceOwnerSettings(Base):
    __tablename__ = "service_owner_settings"

    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    notif_new_order: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notif_cancel: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    service: Mapped["Service"] = relationship(
        back_populates="owner_settings",
        lazy="selectin",
    )


class SheetsRetryQueue(Base):
    __tablename__ = "sheets_retry_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
