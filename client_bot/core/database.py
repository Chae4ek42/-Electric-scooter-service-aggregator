from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from client_bot.core.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Синхронный движок для gspread-функций (write-only sheets)
_sync_url = DATABASE_URL.replace("+aiosqlite", "")
sync_engine = create_engine(_sync_url, echo=False)
