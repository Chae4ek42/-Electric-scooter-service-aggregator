from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from client_bot.core.config import DATABASE_URL

_parsed_url = make_url(DATABASE_URL)
_is_sqlite = _parsed_url.get_backend_name() == "sqlite"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Синхронный движок для gspread-функций (write-only sheets)
_sync_url = DATABASE_URL
if "+aiosqlite" in _sync_url:
    _sync_url = _sync_url.replace("+aiosqlite", "")
elif "+asyncpg" in _sync_url:
    _sync_url = _sync_url.replace("+asyncpg", "+psycopg")

sync_engine = create_engine(
    _sync_url,
    echo=False,
    pool_pre_ping=not _is_sqlite,
)
