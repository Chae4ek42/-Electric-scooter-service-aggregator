from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import sys
import uuid
from logging.handlers import RotatingFileHandler
from typing import Any

_LOG_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "log_context",
    default={},
)


class _ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self._service_name
        ctx = _LOG_CONTEXT.get()
        record.request_id = ctx.get("request_id", "-")
        record.user_id = ctx.get("user_id", "-")
        record.chat_id = ctx.get("chat_id", "-")
        return True


class _BusinessLoggerFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("esas.business")


class _NonBusinessLoggerFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("esas.business")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created,
                tz=dt.timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "-"),
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", "-")
        user_id = getattr(record, "user_id", "-")
        chat_id = getattr(record, "chat_id", "-")
        if request_id != "-":
            payload["request_id"] = request_id
        if user_id != "-":
            payload["user_id"] = user_id
        if chat_id != "-":
            payload["chat_id"] = chat_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s | %(levelname)-8s | %(service)s | %(name)s | "
            "rid=%(request_id)s uid=%(user_id)s chat=%(chat_id)s | %(message)s"
        )


def _resolve_log_level(default_level: str) -> int:
    level_name = os.getenv("LOG_LEVEL", default_level).strip().upper()
    return getattr(logging, level_name, logging.INFO)


def setup_logging(service_name: str, *, default_level: str = "INFO") -> None:
    level = _resolve_log_level(default_level)
    log_format = os.getenv("LOG_FORMAT", "text").strip().lower()

    try:
        file_max_mb = max(1, int(os.getenv("LOG_FILE_MAX_MB", "30")))
    except ValueError:
        file_max_mb = 30
    try:
        file_backups = max(1, int(os.getenv("LOG_FILE_BACKUPS", "5")))
    except ValueError:
        file_backups = 5

    log_dir = os.getenv("LOG_DIR", os.path.join("data", "logs"))
    os.makedirs(log_dir, exist_ok=True)

    service_slug = service_name.replace(" ", "-")
    app_log_path = os.path.join(log_dir, f"{service_slug}.app.log")
    business_log_path = os.path.join(log_dir, f"{service_slug}.business.log")

    formatter: logging.Formatter
    if log_format == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _TextFormatter()

    handlers: list[logging.Handler] = []

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(_ContextFilter(service_name=service_name))
    stream_handler.setFormatter(formatter)
    handlers.append(stream_handler)

    app_file_handler = RotatingFileHandler(
        app_log_path,
        maxBytes=file_max_mb * 1024 * 1024,
        backupCount=file_backups,
        encoding="utf-8",
    )
    app_file_handler.addFilter(_ContextFilter(service_name=service_name))
    app_file_handler.addFilter(_NonBusinessLoggerFilter())
    app_file_handler.setFormatter(formatter)
    handlers.append(app_file_handler)

    business_file_handler = RotatingFileHandler(
        business_log_path,
        maxBytes=file_max_mb * 1024 * 1024,
        backupCount=file_backups,
        encoding="utf-8",
    )
    business_file_handler.addFilter(_ContextFilter(service_name=service_name))
    business_file_handler.addFilter(_BusinessLoggerFilter())
    business_file_handler.setFormatter(formatter)
    handlers.append(business_file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def bind_update_log_context(
    *,
    user_id: int | None,
    chat_id: int | None,
    request_id: str | None = None,
) -> contextvars.Token[dict[str, str]]:
    ctx = dict(_LOG_CONTEXT.get())
    ctx["request_id"] = request_id or uuid.uuid4().hex[:12]

    if user_id is None:
        ctx.pop("user_id", None)
    else:
        ctx["user_id"] = str(user_id)

    if chat_id is None:
        ctx.pop("chat_id", None)
    else:
        ctx["chat_id"] = str(chat_id)

    return _LOG_CONTEXT.set(ctx)


def reset_log_context(token: contextvars.Token[dict[str, str]]) -> None:
    _LOG_CONTEXT.reset(token)
