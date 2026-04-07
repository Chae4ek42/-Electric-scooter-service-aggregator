from __future__ import annotations

import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
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

    category_rel: Mapped["ServiceCategory | None"] = relationship(
        back_populates="services", lazy="selectin"
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True)
    metro_station: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    scheduled_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Статусы:
    #   awaiting_payment   — создана, ожидает предоплаты
    #   accepted           — принята (предоплата внесена)
    #   interrupted        — прервана после диагностики
    #   completed          — завершена успешно
    #   cancelled          — отменена
    status: Mapped[str] = mapped_column(String(30), default="awaiting_payment")
    payment_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_custom_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    problem_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="orders", lazy="selectin")
    service: Mapped["Service"] = relationship(lazy="selectin")
    model: Mapped["Model"] = relationship(lazy="selectin")


class UserAction(Base):
    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="success")
    error_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
