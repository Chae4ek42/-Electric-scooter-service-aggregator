from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
PARTNER_BOT_TOKEN: str = os.environ.get("PARTNER_BOT_TOKEN", "")
SUPPORT_USER: str = os.environ.get("SUPPORT_USER", "@i_jusp")
COOPERATION_USER: str = os.environ.get("COOPERATION_USER", "@i_jusp")
PARTNER_BOT_NAME: str = os.environ.get("PARTNER_BOT_NAME", "ESAS Partner")
GOOGLE_SHEET_ID: str = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SA_PATH: str = os.environ.get("GOOGLE_SA_PATH", "")
SHEETS_SYNC_INTERVAL: int = int(os.environ.get("SHEETS_SYNC_INTERVAL", "300"))

# Порядок столбцов Google Sheets (лист «Сервисы»).
# Значение по умолчанию совпадает с текущей таблицей.
_DEFAULT_COLUMNS = (
    "Название,Рейтинг Я.Карты,Телефон,Telegram,Адрес,Метро ближ.,"
    "Специализация,Основной бренд самокатов,Статус,Доступен,Категория,"
    "Открытие,Закрытие,Гидроизоляция,Диагностика,Входит в стоимость"
)
SHEETS_COLUMNS: list[str] = [
    c.strip()
    for c in os.environ.get("SHEETS_COLUMNS", _DEFAULT_COLUMNS).split(",")
    if c.strip()
]
_admin_raw: str = os.environ.get("ADMIN_USERNAMES", "")
ADMIN_USERNAMES: set[str] = {
    x.strip().lstrip("@").lower()
    for x in _admin_raw.split(",")
    if x.strip().lstrip("@")
}

DATABASE_URL: str = "sqlite+aiosqlite:///esas.db"
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
THROTTLE_RATE: float = 0.2
CALENDAR_DAYS: int = 14
WORK_HOUR_START: int = 8
WORK_HOUR_END: int = 22
TIME_SLOT_MINUTES: int = 60
