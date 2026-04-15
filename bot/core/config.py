from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


# ── Pydantic models for config.yaml ──────────────────────────


class SheetsConfig(BaseModel):
    sync_interval: int = Field(300, ge=10, description="Интервал синхронизации (сек)")
    tab_services: str = "Сервисы"
    tab_orders: str = "Заявки"
    tab_clients: str = "Клиенты"
    columns: List[str] = [
        "Название",
        "Рейтинг Я.Карты",
        "Телефон",
        "Telegram",
        "Адрес",
        "Метро ближ.",
        "Специализация",
        "Основной бренд самокатов",
        "Статус",
        "Доступен",
        "Категория",
        "Открытие",
        "Закрытие",
        "Гидроизоляция",
        "Цена гидроизоляции",
        "Диагностика",
        "Входит в стоимость",
    ]


class CalendarConfig(BaseModel):
    days: int = Field(14, ge=1, le=60)
    work_hour_start: int = Field(8, ge=0, le=23)
    work_hour_end: int = Field(22, ge=1, le=24)
    time_slot_minutes: int = Field(60, ge=15, le=240)

    @field_validator("work_hour_end")
    @classmethod
    def _end_after_start(cls, v, info):
        start = info.data.get("work_hour_start", 0)
        if v <= start:
            raise ValueError("work_hour_end must be greater than work_hour_start")
        return v


class FSMReminderConfig(BaseModel):
    timeout: int = Field(1800, ge=60, description="Секунды до напоминания")
    check_interval: int = Field(60, ge=10, description="Интервал проверки (сек)")


class AppConfig(BaseModel):
    support_user: str = "@i_jusp"
    cooperation_user: str = "@i_jusp"
    admin_usernames: List[str] = []
    database_url: str = "sqlite+aiosqlite:///esas.db"
    google_sa_path: str = "service-account-key.json"
    sheets: SheetsConfig = SheetsConfig()
    calendar: CalendarConfig = CalendarConfig()
    throttle_rate: float = Field(0.2, ge=0.0, le=10.0)
    fsm_reminder: FSMReminderConfig = FSMReminderConfig()


def _load_app_config() -> AppConfig:
    config_path = BASE_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return AppConfig.model_validate(raw)
    return AppConfig()


app_config = _load_app_config()


# ── Secrets from .env ─────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
PARTNER_BOT_TOKEN: str = os.environ.get("PARTNER_BOT_TOKEN", "")
GOOGLE_SHEET_ID: str = os.environ.get("GOOGLE_SHEET_ID", "")
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


# ── Convenience aliases (backward-compatible) ────────────────

SUPPORT_USER: str = app_config.support_user
COOPERATION_USER: str = app_config.cooperation_user
ADMIN_USERNAMES: set[str] = {
    x.strip().lstrip("@").lower() for x in app_config.admin_usernames if x.strip()
}
DATABASE_URL: str = app_config.database_url
GOOGLE_SA_PATH: str = app_config.google_sa_path
SHEETS_SYNC_INTERVAL: int = app_config.sheets.sync_interval
SHEETS_COLUMNS: list[str] = app_config.sheets.columns
SHEETS_TAB_SERVICES: str = app_config.sheets.tab_services
SHEETS_TAB_ORDERS: str = app_config.sheets.tab_orders
SHEETS_TAB_CLIENTS: str = app_config.sheets.tab_clients
THROTTLE_RATE: float = app_config.throttle_rate
CALENDAR_DAYS: int = app_config.calendar.days
WORK_HOUR_START: int = app_config.calendar.work_hour_start
WORK_HOUR_END: int = app_config.calendar.work_hour_end
TIME_SLOT_MINUTES: int = app_config.calendar.time_slot_minutes
