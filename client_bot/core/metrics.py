from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    value = (os.getenv("METRICS_ENABLED", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


_METRICS_ENABLED = _is_enabled()

try:
    from prometheus_client import Counter, Gauge, start_http_server

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - optional runtime dependency fallback
    Counter = Gauge = None  # type: ignore[assignment]
    start_http_server = None  # type: ignore[assignment]
    _PROM_AVAILABLE = False


if _PROM_AVAILABLE:
    _RUNTIME_ERRORS_TOTAL = Counter(
        "esas_runtime_errors_total",
        "Runtime errors persisted by resilience hooks",
        ["action_type"],
    )
    _SHEETS_SYNC_TOTAL = Counter(
        "esas_sheets_sync_total",
        "Sheets sync attempts by source/result",
        ["source", "result"],
    )
    _SHEETS_RETRY_ENQUEUED_TOTAL = Counter(
        "esas_sheets_retry_enqueued_total",
        "Queued sheets retry operations",
        ["operation"],
    )
    _SHEETS_RETRY_PROCESSED_TOTAL = Counter(
        "esas_sheets_retry_processed_total",
        "Processed sheets retry operations",
        ["operation", "result"],
    )
    _SHEETS_RETRY_QUEUE_SIZE = Gauge(
        "esas_sheets_retry_queue_size",
        "Current queue length of delayed sheets retries",
    )
else:  # pragma: no cover - no-op metrics in environments without dependency
    _RUNTIME_ERRORS_TOTAL = None
    _SHEETS_SYNC_TOTAL = None
    _SHEETS_RETRY_ENQUEUED_TOTAL = None
    _SHEETS_RETRY_PROCESSED_TOTAL = None
    _SHEETS_RETRY_QUEUE_SIZE = None


def start_metrics_server(*, service_name: str, default_port: int) -> None:
    if not _METRICS_ENABLED:
        logger.info("Metrics disabled | service=%s", service_name)
        return
    if not _PROM_AVAILABLE or start_http_server is None:
        logger.warning(
            "prometheus_client is not available, metrics disabled | service=%s",
            service_name,
        )
        return

    host = os.getenv("METRICS_HOST", "0.0.0.0")
    raw_port = os.getenv("METRICS_PORT", str(default_port))
    try:
        port = int(raw_port)
    except ValueError:
        port = default_port

    start_http_server(port, addr=host)
    logger.info(
        "Metrics exporter started | service=%s | listen=%s:%s",
        service_name,
        host,
        port,
    )


def mark_runtime_error(action_type: str) -> None:
    if _RUNTIME_ERRORS_TOTAL is not None:
        _RUNTIME_ERRORS_TOTAL.labels(action_type=action_type or "unknown").inc()


def mark_sheets_sync(*, source: str, result: str) -> None:
    if _SHEETS_SYNC_TOTAL is not None:
        _SHEETS_SYNC_TOTAL.labels(source=source, result=result).inc()


def mark_sheets_retry_enqueued(operation: str) -> None:
    if _SHEETS_RETRY_ENQUEUED_TOTAL is not None:
        _SHEETS_RETRY_ENQUEUED_TOTAL.labels(operation=operation).inc()


def mark_sheets_retry_processed(*, operation: str, result: str) -> None:
    if _SHEETS_RETRY_PROCESSED_TOTAL is not None:
        _SHEETS_RETRY_PROCESSED_TOTAL.labels(operation=operation, result=result).inc()


def set_sheets_retry_queue_size(size: int) -> None:
    if _SHEETS_RETRY_QUEUE_SIZE is not None:
        _SHEETS_RETRY_QUEUE_SIZE.set(max(size, 0))


def metrics_health() -> dict[str, Any]:
    return {
        "enabled": _METRICS_ENABLED,
        "prometheus_client": _PROM_AVAILABLE,
    }
