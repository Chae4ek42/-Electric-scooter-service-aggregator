"""Write-back to Google Sheets via Service Account (gspread + google-auth)."""

from __future__ import annotations

import logging
import re
from typing import Callable

from client_bot.core.config import (
    GOOGLE_SA_PATH,
    GOOGLE_SHEET_ID,
    SHEETS_COLUMNS,
    SHEETS_TAB_BANK_DETAILS,
    SHEETS_TAB_CLIENTS,
    SHEETS_TAB_ORDERS,
    SHEETS_TAB_SERVICE_METRICS,
    SHEETS_TAB_SERVICES,
)
from client_bot.domain.models import Service
from client_bot.services.city_search import is_moscow_city

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = {
    "-",
    "—",
    "?",
    "(не заполнено)",
    "не заполнено",
    "none",
    "null",
    "n/a",
    "na",
}
_TEXT_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_TYPE_MAP_REV = {"repair": "Ремонт", "upgrade": "Апгрейд", "complex": "Комплекс"}

_FIELD_GETTERS: dict[str, Callable[[Service], str]] = {
    "id": lambda s: str(s.id),
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

_BANK_HEADERS = [
    "ID",
    "Форма",
    "Налогообложение",
    "Расч. счёт",
    "Банк",
    "БИК",
    "Корр. счёт",
    "Организация",
    "ИНН",
]

_ORDER_HEADERS = [
    "ID",
    "Код заявки",
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

_SERVICE_METRICS_HEADERS = [
    "ID сервиса",
    "Сервис",
    "Тип",
    "Всего заявок",
    "Оплачено",
    "Дошли до работы",
    "Завершено",
    "Негативные исходы",
    "Конверсия в работу (%)",
    "Конверсия в завершение (%)",
    "Выручка completed (₽)",
    "Средний чек completed (₽)",
    "Медианный чек completed (₽)",
    "Средний цикл completed (ч)",
]


def _is_enabled() -> bool:
    return bool(GOOGLE_SA_PATH and GOOGLE_SHEET_ID)


def _get_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(GOOGLE_SA_PATH, scopes=_SCOPES)
    return gspread.authorize(creds)


def _service_to_row(svc: Service) -> list[str]:
    row: list[str] = []
    for col in SHEETS_COLUMNS:
        getter = _FIELD_GETTERS.get(col.strip().lower())
        row.append(getter(svc) if getter else "")
    return row


def _text_filled(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    if not text:
        return False
    if text.lower() in _EMPTY_MARKERS:
        return False
    return bool(_TEXT_TOKEN_RE.search(text))


def _service_ready_for_export(svc: Service) -> bool:
    if (svc.partnership_status or "").strip().lower() == "активный":
        # Active services were already moderated; keep them in sheets
        # even if legacy optional fields are incomplete.
        return True

    city = (svc.city or "").strip()

    required_text = [
        svc.name,
        svc.service_type,
        city,
        svc.address,
        svc.phone,
        svc.open_time,
        svc.close_time,
        svc.working_days,
    ]
    if not all(_text_filled(v) for v in required_text):
        return False

    if svc.service_type in ("repair", "complex") and svc.category_id is None:
        return False
    if svc.service_type in ("upgrade", "complex") and not _text_filled(
        svc.upgrade_categories
    ):
        return False

    if is_moscow_city(city) and not _text_filled(svc.nearest_metro):
        return False

    if svc.has_hydroisolation and not _text_filled(svc.hydroisolation_price):
        return False

    if svc.diagnostics_price is None:
        return False

    return True


def _ensure_worksheet(sh, name: str, headers: list[str]):
    try:
        ws = sh.worksheet(name)
    except Exception:
        ws = sh.add_worksheet(title=name, rows=100, cols=max(10, len(headers)))
        ws.update("A1", [headers], value_input_option="USER_ENTERED")
        logger.info("SHEETS | created worksheet '%s'", name)
        return ws

    existing = ws.row_values(1)
    if existing != headers:
        ws.update("A1", [headers], value_input_option="USER_ENTERED")
        logger.info(
            "SHEETS | updated headers for '%s': %d→%d cols",
            name,
            len(existing),
            len(headers),
        )
    return ws


def _row_range(row: int, width: int) -> str:
    from gspread.utils import rowcol_to_a1

    return f"{rowcol_to_a1(row, 1)}:{rowcol_to_a1(row, width)}"


def _find_row_by_service_id(ws, service_id: int) -> int | None:
    values = ws.col_values(1)
    lookup = str(service_id)
    # row 1 is header
    for row_index, value in enumerate(values[1:], start=2):
        if value.strip() == lookup:
            return row_index
    return None


def add_service_row(svc: Service) -> bool:
    # Keep public API but ensure id-based upsert semantics.
    return update_service_row(svc)


def update_service_row(svc: Service) -> bool:
    if not _is_enabled():
        return False
    if not svc.registration_complete or not _service_ready_for_export(svc):
        logger.info(
            "SHEETS_WRITE_SKIP | op=update | service_id=%s | reason=incomplete_service",
            svc.id,
        )
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)

        row_data = _service_to_row(svc)
        row_number = _find_row_by_service_id(ws, svc.id)
        if row_number is None:
            ws.append_row(row_data, value_input_option="USER_ENTERED", table_range="A1")
            logger.info(
                "SHEETS_WRITE | op=add | service_id=%s | service=%s",
                svc.id,
                svc.name,
            )
            return True

        ws.update(
            _row_range(row_number, len(row_data)),
            [row_data],
            value_input_option="USER_ENTERED",
        )
        logger.info(
            "SHEETS_WRITE | op=update | service_id=%s | row=%d",
            svc.id,
            row_number,
        )
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=update | service_id=%s", svc.id)
        return False


def sync_all_services_to_sheet() -> bool:
    """Rewrite the "Сервисы" worksheet with all services from DB."""
    if not _is_enabled():
        return False
    try:
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import Session, selectinload

        from client_bot.core.database import sync_engine

        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)

        with Session(sync_engine) as session:
            services = (
                session.execute(
                    sa_select(Service)
                    .options(selectinload(Service.category_rel))
                    .order_by(Service.id)
                )
                .scalars()
                .all()
            )
            rows = [
                _service_to_row(svc)
                for svc in services
                if svc.registration_complete and _service_ready_for_export(svc)
            ]

        ws.clear()
        ws.update(
            range_name="A1",
            values=[SHEETS_COLUMNS] + rows,
            value_input_option="USER_ENTERED",
        )
        logger.info("SHEETS_WRITE | op=sync_services | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_services")
        return False


def set_service_available(service_id: int, available: bool) -> bool:
    if not _is_enabled():
        return False
    try:
        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_SERVICES, SHEETS_COLUMNS)

        row_number = _find_row_by_service_id(ws, service_id)
        if row_number is None:
            logger.warning("Sheets: row not found for service_id=%s", service_id)
            return False

        headers = ws.row_values(1)
        col_idx = None
        for i, h in enumerate(headers, start=1):
            if h.strip().lower() == "доступен":
                col_idx = i
                break
        if col_idx is None:
            logger.warning("Sheets: column 'Доступен' not found")
            return False

        ws.update_cell(row_number, col_idx, "Да" if available else "Нет")
        return True
    except Exception:
        logger.exception(
            "Sheets write failed (available) for service_id=%s", service_id
        )
        return False


def update_service_bank_row(service_id: int) -> bool:
    if not _is_enabled():
        return False

    try:
        from client_bot.core.database import sync_engine
        from client_bot.domain.models import ServiceBankDetails
        from sqlalchemy.orm import Session

        with Session(sync_engine) as session:
            bank = (
                session.query(ServiceBankDetails)
                .filter(ServiceBankDetails.service_id == service_id)
                .one_or_none()
            )

        if bank is None:
            logger.warning(
                "SHEETS_BANK | no bank details for service_id=%s", service_id
            )
            return False

        row_data = [
            str(service_id),
            bank.legal_form or "",
            bank.tax_system or "",
            bank.bank_account or "",
            bank.bank_name or "",
            bank.bik or "",
            bank.corr_account or "",
            bank.org_name or "",
            bank.inn or "",
        ]

        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(sh, SHEETS_TAB_BANK_DETAILS, _BANK_HEADERS)

        row_number = _find_row_by_service_id(ws, service_id)
        if row_number is None:
            ws.append_row(row_data, value_input_option="USER_ENTERED", table_range="A1")
            logger.info("SHEETS_WRITE | op=add_bank | service_id=%s", service_id)
            return True

        ws.update(
            _row_range(row_number, len(row_data)),
            [row_data],
            value_input_option="USER_ENTERED",
        )
        logger.info(
            "SHEETS_WRITE | op=update_bank | service_id=%s | row=%d",
            service_id,
            row_number,
        )
        return True
    except Exception:
        logger.exception(
            "SHEETS_WRITE_ERR | op=update_bank | service_id=%s", service_id
        )
        return False


def sync_all_orders_to_sheet() -> bool:
    """Rewrite the "Заявки" worksheet with all orders from DB."""
    if not _is_enabled():
        return False
    try:
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import Session, joinedload

        from client_bot.core.database import sync_engine
        from client_bot.domain.models import Model as ModelModel
        from client_bot.domain.models import Order, User

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
                        (o.order_code or f"{o.id:06d}"),
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
                        o.created_at.strftime("%d.%m.%Y %H:%M") if o.created_at else "",
                        (
                            o.completed_at.strftime("%d.%m.%Y %H:%M")
                            if o.completed_at
                            else ""
                        ),
                    ]
                )

        ws.clear()
        ws.update(
            range_name="A1",
            values=[_ORDER_HEADERS] + rows,
            value_input_option="USER_ENTERED",
        )
        logger.info("SHEETS_WRITE | op=sync_orders | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_orders")
        return False


def sync_all_clients_to_sheet() -> bool:
    """Rewrite the "Клиенты" worksheet with all users from DB."""
    if not _is_enabled():
        return False
    try:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import Session

        from client_bot.core.database import sync_engine
        from client_bot.domain.models import Order, User

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

        ws.clear()
        ws.update(
            range_name="A1",
            values=[_CLIENT_HEADERS] + rows,
            value_input_option="USER_ENTERED",
        )
        logger.info("SHEETS_WRITE | op=sync_clients | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_clients")
        return False


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def sync_all_service_metrics_to_sheet() -> bool:
    """Rewrite the service metrics worksheet with business KPIs per service."""
    if not _is_enabled():
        return False

    try:
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import Session

        from client_bot.core.database import sync_engine
        from client_bot.domain.models import Order

        gc = _get_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = _ensure_worksheet(
            sh,
            SHEETS_TAB_SERVICE_METRICS,
            _SERVICE_METRICS_HEADERS,
        )

        with Session(sync_engine) as session:
            services = (
                session.execute(
                    sa_select(Service)
                    .where(Service.registration_complete.is_(True))
                    .order_by(Service.id)
                )
                .scalars()
                .all()
            )
            services = [svc for svc in services if _service_ready_for_export(svc)]

            rows: list[list[str | int | float]] = []
            for svc in services:
                orders = (
                    session.execute(sa_select(Order).where(Order.service_id == svc.id))
                    .scalars()
                    .all()
                )

                total = len(orders)
                by_status: dict[str, int] = {}
                for order in orders:
                    by_status[order.status] = by_status.get(order.status, 0) + 1

                paid = by_status.get("paid", 0)
                accepted_flow = sum(
                    by_status.get(key, 0)
                    for key in (
                        "accepted",
                        "in_progress",
                        "ready_for_pickup",
                        "completed",
                    )
                )
                completed = by_status.get("completed", 0)
                failed = sum(
                    by_status.get(key, 0)
                    for key in (
                        "cancelled",
                        "rejected_by_partner",
                        "client_refused",
                        "interrupted",
                        "disputed",
                    )
                )

                completed_costs = [
                    float(order.total_cost)
                    for order in orders
                    if order.status == "completed" and order.total_cost is not None
                ]
                revenue = sum(completed_costs)
                avg_check = (revenue / len(completed_costs)) if completed_costs else 0.0
                median_check = _median(completed_costs)

                cycle_hours = [
                    (order.completed_at - order.created_at).total_seconds() / 3600
                    for order in orders
                    if order.status == "completed"
                    and order.completed_at is not None
                    and order.created_at is not None
                ]
                avg_cycle_hours = (
                    sum(cycle_hours) / len(cycle_hours) if cycle_hours else 0.0
                )

                conversion_to_work = (accepted_flow / total * 100) if total else 0.0
                conversion_to_completed = (completed / total * 100) if total else 0.0

                rows.append(
                    [
                        svc.id,
                        svc.name or "",
                        _TYPE_MAP_REV.get(svc.service_type, svc.service_type or ""),
                        total,
                        paid,
                        accepted_flow,
                        completed,
                        failed,
                        round(conversion_to_work, 2),
                        round(conversion_to_completed, 2),
                        round(revenue, 2),
                        round(avg_check, 2),
                        round(median_check, 2),
                        round(avg_cycle_hours, 2),
                    ]
                )

        ws.clear()
        ws.update(
            range_name="A1",
            values=[_SERVICE_METRICS_HEADERS] + rows,
            value_input_option="USER_ENTERED",
        )
        logger.info("SHEETS_WRITE | op=sync_service_metrics | count=%d", len(rows))
        return True
    except Exception:
        logger.exception("SHEETS_WRITE_ERR | op=sync_service_metrics")
        return False
