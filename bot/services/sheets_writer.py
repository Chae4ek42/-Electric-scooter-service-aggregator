"""Write-back to Google Sheets via Service Account (gspread + google-auth).

Порядок столбцов берётся из ``SHEETS_COLUMNS`` (config.py / .env).
Маппинг «заголовок → значение поля» описан в ``_FIELD_GETTERS``.
"""

from __future__ import annotations

import logging
from typing import Callable

from bot.core.config import GOOGLE_SA_PATH, GOOGLE_SHEET_ID, SHEETS_COLUMNS
from bot.domain.models import Service

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_TYPE_MAP_REV = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплекс"}

# Маппинг: заголовок (нижний регистр) → функция, возвращающая строковое значение
_FIELD_GETTERS: dict[str, Callable[[Service], str]] = {
    "название": lambda s: s.name or "",
    "рейтинг я.карты": lambda s: str(s.yandex_rating or ""),
    "телефон": lambda s: s.phone or "",
    "telegram": lambda s: s.telegram_handle or "",
    "адрес": lambda s: s.address or "",
    "метро ближ.": lambda s: s.nearest_metro or "",
    "специализация": lambda s: _TYPE_MAP_REV.get(s.service_type, s.service_type),
    "основной бренд самокатов": lambda s: s.main_brand_scooter or "",
    "статус": lambda s: s.partnership_status or "",
    "доступен": lambda s: "Да" if s.is_available else "Нет",
    "категория": lambda s: (s.category_rel.name if s.category_rel else ""),
    "открытие": lambda s: s.open_time or "",
    "закрытие": lambda s: s.close_time or "",
    "гидроизоляция": lambda s: "Да" if s.has_hydroisolation else "Нет",
    "диагностика": lambda s: (
        str(int(s.diagnostics_price)) if s.diagnostics_price else ""
    ),
    "входит в стоимость": lambda s: "Да" if s.diagnostics_included else "Нет",
    "категории апгрейда": lambda s: s.upgrade_categories or "",
    "рабочие дни": lambda s: (s.working_days or "").replace(",", ", "),
}


def _is_enabled() -> bool:
    return bool(GOOGLE_SA_PATH and GOOGLE_SHEET_ID)


def _get_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(GOOGLE_SA_PATH, scopes=_SCOPES)
    return gspread.authorize(creds)


def _service_to_row(svc: Service) -> list[str]:
    """Формирует строку в порядке SHEETS_COLUMNS."""
    row: list[str] = []
    for col in SHEETS_COLUMNS:
        getter = _FIELD_GETTERS.get(col.strip().lower())
        row.append(getter(svc) if getter else "")
    return row


def add_service_row(svc: Service) -> bool:
    if not _is_enabled():
        logger.debug("Sheets write disabled")
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.worksheet("Сервисы")
        ws.append_row(_service_to_row(svc), value_input_option="USER_ENTERED")
        logger.info(
            "SHEETS_WRITE | op=add | service=%s | columns=%d",
            svc.name,
            len(SHEETS_COLUMNS),
        )
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=add | service=%s", svc.name)
        return False


def update_service_row(svc: Service) -> bool:
    if not _is_enabled():
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.worksheet("Сервисы")
        cell = ws.find(svc.name, in_column=1)
        if cell is None:
            return add_service_row(svc)
        row_data = _service_to_row(svc)
        end_col = chr(ord("A") + len(row_data) - 1)
        ws.update(
            f"A{cell.row}:{end_col}{cell.row}",
            [row_data],
            value_input_option="USER_ENTERED",
        )
        logger.info(
            "SHEETS_WRITE | op=update | service=%s | row=%d", svc.name, cell.row
        )
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=update | service=%s", svc.name)
        return False


def set_service_available(service_name: str, available: bool) -> bool:
    if not _is_enabled():
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.worksheet("Сервисы")
        cell = ws.find(service_name, in_column=1)
        if cell is None:
            logger.warning("Sheets: row not found for %s", service_name)
            return False
        headers = ws.row_values(1)
        col_idx = None
        for i, h in enumerate(headers):
            if h.strip().lower() == "доступен":
                col_idx = i + 1
                break
        if col_idx is None:
            logger.warning("Sheets: column 'Доступен' not found")
            return False
        ws.update_cell(cell.row, col_idx, "Да" if available else "Нет")
        return True
    except Exception:
        logger.exception("Sheets write failed (available) for %s", service_name)
        return False
