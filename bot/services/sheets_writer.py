"""Write-back to Google Sheets via Service Account (gspread + google-auth).

Порядок столбцов берётся из ``SHEETS_COLUMNS`` (config.py / .env).
Маппинг «заголовок → значение поля» описан в ``_FIELD_GETTERS``.
"""

from __future__ import annotations

import logging
from typing import Callable

from bot.core.config import (
    GOOGLE_SA_PATH,
    GOOGLE_SHEET_ID,
    SHEETS_COLUMNS,
    SHEETS_TAB_CLIENTS,
    SHEETS_TAB_ORDERS,
    SHEETS_TAB_SERVICES,
)
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
    "телефон": lambda s: f"'{s.phone}" if s.phone else "",
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
    "цена гидроизоляции": lambda s: s.hydroisolation_price or "",
    "диагностика": lambda s: (
        str(int(s.diagnostics_price)) if s.diagnostics_price is not None else ""
    ),
    "входит в стоимость": lambda s: "Да" if s.diagnostics_included else "Нет",
    "категории апгрейда": lambda s: s.upgrade_categories or "",
    "рабочие дни": lambda s: (s.working_days or "").replace(",", ", "),
    "дни недели": lambda s: (s.working_days or "").replace(",", ", "),
    "завершена": lambda s: "Да" if s.registration_complete else "Нет",
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


def _ensure_worksheet(sh, name: str, headers: list[str]):
    """Return existing worksheet with validated headers, or create new one."""
    try:
        ws = sh.worksheet(name)
        # Verify the first row has correct headers (not duplicated)
        existing = ws.row_values(1)
        if existing and existing == ws.row_values(2):
            # Duplicated header row — remove second copy
            ws.delete_rows(2)
            logger.warning("SHEETS | removed duplicate header row in '%s'", name)
        return ws
    except Exception:
        ws = sh.add_worksheet(title=name, rows=100, cols=len(headers))
        ws.update("A1", [headers], value_input_option="USER_ENTERED")
        logger.info("SHEETS | created worksheet '%s'", name)
        return ws


def add_service_row(svc: Service) -> bool:
    if not _is_enabled():
        logger.debug("Sheets write disabled")
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)
        ws.append_row(
            _service_to_row(svc),
            value_input_option="USER_ENTERED",
            table_range="A1",
        )
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
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)
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
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)
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


# ── Orders & Clients sheets (write-only, for debugging) ──────

_ORDER_HEADERS = [
    "ID",
    "Клиент",
    "Клиент TG",
    "Сервис",
    "Бренд",
    "Модель",
    "Категория",
    "Статус",
    "Создано",
    "Обновлено",
]

_CLIENT_HEADERS = [
    "TG ID",
    "Username",
    "Имя",
    "Заявок",
    "Первый заказ",
    "Последний заказ",
]


def sync_all_orders_to_sheet() -> bool:
    """Перезаписывает лист «Заявки» всеми заказами из БД (синхронный)."""
    if not _is_enabled():
        return False
    try:
        from sqlalchemy import select as sa_select
        from bot.core.database import sync_engine
        from sqlalchemy.orm import Session, joinedload
        from bot.domain.models import Brand, Model as ModelModel, Order, User

        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_ORDERS, _ORDER_HEADERS)

        _STATUS_RU = {
            "awaiting_payment": "Ожидает оплаты",
            "paid": "Оплачено",
            "accepted": "Принята",
            "in_progress": "В работе",
            "ready_for_pickup": "Готов к выдаче",
            "completed": "Завершена",
            "cancelled": "Отменена",
            "rejected_by_partner": "Отклонена",
            "interrupted": "Прервана",
            "client_refused": "Клиент отказался",
            "disputed": "Оспорена",
            "no_center": "Не найден центр",
        }
        with Session(sync_engine) as session:
            orders = (
                session.execute(
                    sa_select(Order).options(
                        joinedload(Order.model).joinedload(ModelModel.brand),
                        joinedload(Order.service),
                    )
                )
                .unique()
                .scalars()
                .all()
            )
            rows = []
            for o in orders:
                user = session.get(User, o.user_id)
                svc = o.service
                if o.brand_custom_name:
                    brand_str = o.brand_custom_name
                    model_str = o.model_custom_name or ""
                elif o.model:
                    brand_str = o.model.brand.name if o.model.brand else ""
                    model_str = o.model_custom_name or o.model.name
                else:
                    brand_str = ""
                    model_str = o.model_custom_name or ""
                category_str = (
                    o.upgrade_category
                    or (svc.category_rel.name if svc and svc.category_rel else "")
                    if svc
                    else (o.upgrade_category or "")
                )
                rows.append(
                    [
                        o.id,
                        user.full_name if user else "",
                        (
                            f"@{user.username}"
                            if user and user.username
                            else str(o.user_id)
                        ),
                        svc.name if svc else "",
                        brand_str,
                        model_str,
                        category_str,
                        _STATUS_RU.get(o.status, o.status) if o.status else "",
                        str(
                            o.created_at.strftime("%d.%m.%Y %H:%M")
                            if o.created_at
                            else ""
                        ),
                        str(
                            o.completed_at.strftime("%d.%m.%Y %H:%M")
                            if o.completed_at
                            else ""
                        ),
                    ]
                )
        all_data = [_ORDER_HEADERS] + rows
        ws.clear()
        ws.update(range_name="A1", values=all_data, value_input_option="USER_ENTERED")
        logger.info("SHEETS_WRITE | op=sync_orders | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_orders")
        return False


def sync_all_clients_to_sheet() -> bool:
    """Перезаписывает лист «Клиенты» всеми пользователями из БД."""
    if not _is_enabled():
        return False
    try:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select
        from bot.core.database import sync_engine
        from sqlalchemy.orm import Session
        from bot.domain.models import Order, User

        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_CLIENTS, _CLIENT_HEADERS)

        with Session(sync_engine) as session:
            users = session.execute(sa_select(User)).scalars().all()
            rows = []
            for u in users:
                order_count = (
                    session.scalar(
                        sa_select(sa_func.count())
                        .select_from(Order)
                        .where(Order.user_id == u.id)
                    )
                    or 0
                )
                first = session.scalar(
                    sa_select(sa_func.min(Order.created_at)).where(
                        Order.user_id == u.id
                    )
                )
                last = session.scalar(
                    sa_select(sa_func.max(Order.created_at)).where(
                        Order.user_id == u.id
                    )
                )
                rows.append(
                    [
                        u.id,
                        f"@{u.username}" if u.username else "",
                        u.full_name or "",
                        order_count,
                        first.strftime("%d.%m.%Y %H:%M") if first else "",
                        last.strftime("%d.%m.%Y %H:%M") if last else "",
                    ]
                )
        all_data = [_CLIENT_HEADERS] + rows
        ws.clear()
        ws.update(range_name="A1", values=all_data, value_input_option="USER_ENTERED")
        logger.info("SHEETS_WRITE | op=sync_clients | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_clients")
        return False
