from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
SUPPORT_USER: str = os.environ.get("SUPPORT_USER", "@i_jusp")
GOOGLE_SHEET_ID: str = os.environ.get("GOOGLE_SHEET_ID", "")
SHEETS_SYNC_INTERVAL: int = int(os.environ.get("SHEETS_SYNC_INTERVAL", "300"))
_admin_raw: str = os.environ.get("ADMIN_USERNAMES", "")
ADMIN_USERNAMES: set[str] = {
    x.strip().lstrip("@").lower()
    for x in _admin_raw.split(",")
    if x.strip().lstrip("@")
}

DATABASE_URL: str = "sqlite+aiosqlite:///esas.db"
THROTTLE_RATE: float = 0.2
CALENDAR_DAYS: int = 14
WORK_HOUR_START: int = 8
WORK_HOUR_END: int = 22
TIME_SLOT_MINUTES: int = 60
