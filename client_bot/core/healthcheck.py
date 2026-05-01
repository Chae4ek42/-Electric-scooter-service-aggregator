from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy import text

from client_bot.core.config import GOOGLE_SA_PATH, GOOGLE_SHEET_ID, REDIS_URL
from client_bot.core.database import async_session
from client_bot.core.resilience import register_runtime_error


@dataclass(slots=True)
class ProbeResult:
    name: str
    status: str  # ok | degraded | skipped
    latency_ms: int | None
    details: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _latency_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


async def _probe_db() -> ProbeResult:
    started = time.perf_counter()
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return ProbeResult(
            name="database",
            status="ok",
            latency_ms=_latency_ms(started),
            details="SELECT 1",
        )
    except Exception as exc:
        return ProbeResult(
            name="database",
            status="degraded",
            latency_ms=_latency_ms(started),
            details=str(exc),
        )


async def _probe_redis() -> ProbeResult:
    if not REDIS_URL:
        return ProbeResult(
            name="redis",
            status="skipped",
            latency_ms=None,
            details="REDIS_URL not set",
        )

    started = time.perf_counter()
    redis = None
    try:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis.ping()
        return ProbeResult(
            name="redis",
            status="ok",
            latency_ms=_latency_ms(started),
            details="PING",
        )
    except Exception as exc:
        return ProbeResult(
            name="redis",
            status="degraded",
            latency_ms=_latency_ms(started),
            details=str(exc),
        )
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass


async def _probe_sheets() -> ProbeResult:
    if not GOOGLE_SA_PATH or not GOOGLE_SHEET_ID:
        return ProbeResult(
            name="sheets",
            status="skipped",
            latency_ms=None,
            details="GOOGLE_SA_PATH or GOOGLE_SHEET_ID not set",
        )

    started = time.perf_counter()
    try:
        await asyncio.to_thread(_probe_sheets_sync)
        return ProbeResult(
            name="sheets",
            status="ok",
            latency_ms=_latency_ms(started),
            details="open_by_key",
        )
    except Exception as exc:
        return ProbeResult(
            name="sheets",
            status="degraded",
            latency_ms=_latency_ms(started),
            details=str(exc),
        )


def _probe_sheets_sync() -> None:
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_SA_PATH, scopes=scopes)
    client = gspread.authorize(creds)
    # Open only checks auth + spreadsheet access, no write side-effects.
    client.open_by_key(GOOGLE_SHEET_ID)


def _status_icon(status: str) -> str:
    if status == "ok":
        return "OK"
    if status == "degraded":
        return "DEGRADED"
    return "SKIPPED"


def _format_probe_line(result: ProbeResult) -> str:
    latency = f" {result.latency_ms}ms" if result.latency_ms is not None else ""
    return f"{_status_icon(result.status)} {result.name}{latency} - {result.details}"


async def run_self_check(*, source: str, user_id: int | None) -> tuple[bool, str]:
    """Run lightweight health probes and return (healthy, formatted_report)."""
    probes = [
        await _probe_db(),
        await _probe_redis(),
        await _probe_sheets(),
    ]

    degraded = [p for p in probes if p.status == "degraded"]
    healthy = len(degraded) == 0
    overall = "HEALTHY" if healthy else "DEGRADED"

    lines = [f"Self-check ({source})", f"Overall: {overall}", ""]
    lines.extend(_format_probe_line(p) for p in probes)
    report = "\n".join(lines)

    if not healthy:
        details = "; ".join(f"{p.name}:{p.details[:180]}" for p in degraded)
        await register_runtime_error(
            action_type="healthcheck_degraded",
            status="warning",
            user_id=user_id,
            payload=f"source={source}; {details}",
            error=report,
        )

    return healthy, report
