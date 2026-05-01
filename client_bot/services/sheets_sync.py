"""Strict synchronization from Google Sheets to DB (Service Account only)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from client_bot.core.database import async_session
from client_bot.domain.models import (
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
)

logger = logging.getLogger(__name__)
business_logger = logging.getLogger("esas.business.sheets_sync")

_TYPE_MAP: dict[str, str] = {
    "ремонт": "repair",
    "апгрейд": "upgrade",
    "комплекс": "complex",
}

_BOOL_TRUE = {"да", "yes", "1", "true"}
_BOOL_FALSE = {"нет", "no", "0", "false"}

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


class SheetValidationError(ValueError):
    pass


def _is_available() -> bool:
    from client_bot.core.config import GOOGLE_SHEET_ID

    return bool(GOOGLE_SHEET_ID)


def _col(row: dict[str, str], key: str) -> str | None:
    key_lower = key.strip().lower()
    for k, v in row.items():
        if k.strip().lower() == key_lower:
            value = str(v).strip()
            return value or None
    return None


def _required_str(row: dict[str, str], key: str, row_num: int) -> str:
    value = _col(row, key)
    if value is None:
        raise SheetValidationError(
            f"Строка {row_num}: отсутствует обязательный столбец '{key}'"
        )
    return value


def _parse_int(
    row: dict[str, str], key: str, row_num: int, required: bool = False
) -> int | None:
    value = _required_str(row, key, row_num) if required else _col(row, key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SheetValidationError(
            f"Строка {row_num}: столбец '{key}' должен быть целым числом, получено '{value}'"
        ) from exc
    if parsed <= 0:
        raise SheetValidationError(
            f"Строка {row_num}: столбец '{key}' должен быть > 0, получено '{value}'"
        )
    return parsed


def _parse_float(row: dict[str, str], key: str, row_num: int) -> float | None:
    value = _col(row, key)
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError as exc:
        raise SheetValidationError(
            f"Строка {row_num}: столбец '{key}' должен быть числом, получено '{value}'"
        ) from exc


def _parse_bool(
    row: dict[str, str],
    key: str,
    row_num: int,
    required: bool = False,
) -> bool | None:
    value = _required_str(row, key, row_num) if required else _col(row, key)
    if value is None:
        return None
    raw = value.strip().lower()
    if raw in _BOOL_TRUE:
        return True
    if raw in _BOOL_FALSE:
        return False
    raise SheetValidationError(
        f"Строка {row_num}: столбец '{key}' должен быть Да/Нет, получено '{value}'"
    )


def _parse_service_type(row: dict[str, str], row_num: int) -> str:
    raw_type = _required_str(row, "Специализация", row_num).strip().lower()
    mapped = _TYPE_MAP.get(raw_type)
    if mapped is None:
        raise SheetValidationError(
            f"Строка {row_num}: неизвестная специализация '{raw_type}'"
        )
    return mapped


def _parse_time(row: dict[str, str], key: str, row_num: int) -> str | None:
    value = _col(row, key)
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise SheetValidationError(
            f"Строка {row_num}: столбец '{key}' должен быть в формате HH:MM, получено '{value}'"
        )
    hh = int(parts[0])
    mm = int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise SheetValidationError(
            f"Строка {row_num}: столбец '{key}' вне диапазона времени, получено '{value}'"
        )
    return f"{hh:02d}:{mm:02d}"


def _fetch_via_sa(
    sheet_id: str,
    sheet_name: str,
    *,
    headers: list[str] | None = None,
) -> list[dict[str, Any]]:
    from pathlib import Path

    import gspread
    from google.oauth2.service_account import Credentials

    from client_bot.core.config import GOOGLE_SA_PATH, SHEETS_COLUMNS

    if not GOOGLE_SA_PATH:
        raise RuntimeError("GOOGLE_SA_PATH не задан: CSV fallback отключён")

    sa_path = str(Path(GOOGLE_SA_PATH).resolve())
    creds = Credentials.from_service_account_file(
        sa_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    try:
        ws = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        header_row = headers or SHEETS_COLUMNS
        logger.warning(
            "SYNC_SHEET_MISSING | sheet=%s | action=creating with %d columns",
            sheet_name,
            len(header_row),
        )
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=max(10, len(header_row)))
        ws.append_row(header_row, value_input_option="USER_ENTERED")
        return []

    values = ws.get_all_values()
    if not values:
        return []

    headers = values[0]
    deduped_headers: list[str] = []
    seen: set[str] = set()
    for h in headers:
        if h in seen:
            deduped_headers.append(f"{h}_dup")
        else:
            deduped_headers.append(h)
            seen.add(h)

    rows: list[dict[str, Any]] = []
    for row in values[1:]:
        padded = row + [""] * (len(deduped_headers) - len(row))
        rows.append(dict(zip(deduped_headers, padded)))
    return rows


async def sync_services_from_sheet(
    *, first_run: bool = False, dry_run: bool = False
) -> int:
    if not _is_available():
        logger.debug("Sheets sync пропущен (GOOGLE_SHEET_ID не задан)")
        return 0

    from client_bot.core.config import (
        GOOGLE_SHEET_ID,
        SHEETS_COLUMNS,
        SHEETS_TAB_SERVICES,
    )

    if first_run:
        logger.info(
            "SYNC_START | columns_configured=%d | columns=%s",
            len(SHEETS_COLUMNS),
            ",".join(SHEETS_COLUMNS),
        )

    rows = _fetch_via_sa(GOOGLE_SHEET_ID, SHEETS_TAB_SERVICES)
    if rows:
        headers = {h.strip().lower() for h in rows[0].keys()}
        configured = {c.strip().lower() for c in SHEETS_COLUMNS}
        missing = configured - headers
        if missing:
            logger.warning("SYNC_COLUMNS_MISMATCH | missing_in_sheet=%s", missing)

    added = 0
    updated = 0
    unchanged = 0
    skipped = 0

    seen_ids: set[int] = set()

    async with async_session() as session:
        cats = (await session.execute(select(ServiceCategory))).scalars().all()
        cat_cache: dict[str, int] = {c.name: c.id for c in cats}

        for index, row in enumerate(rows, start=2):
            if all((str(v).strip() == "" for v in row.values())):
                skipped += 1
                continue

            try:
                service_id = _parse_int(row, "ID", index, required=True)
            except SheetValidationError as exc:
                logger.warning("SYNC_ROW_SKIP | row=%d | reason=%s", index, exc)
                skipped += 1
                continue

            if service_id in seen_ids:
                logger.warning(
                    "SYNC_ROW_SKIP | row=%d | reason=duplicate_id | id=%s",
                    index,
                    service_id,
                )
                skipped += 1
                continue
            seen_ids.add(service_id)

            existing = (
                await session.execute(select(Service).where(Service.id == service_id))
            ).scalar_one_or_none()

            name = _col(row, "Название")
            if not name:
                if existing is not None:
                    name = existing.name
                    logger.warning(
                        "SYNC_ROW_WARN | row=%d | field=Название | action=use_existing",
                        index,
                    )
                else:
                    logger.warning(
                        "SYNC_ROW_SKIP | row=%d | reason=missing_name_for_new_service",
                        index,
                    )
                    skipped += 1
                    continue

            try:
                service_type = _parse_service_type(row, index)
            except SheetValidationError as exc:
                service_type = (
                    existing.service_type if existing is not None else "repair"
                )
                logger.warning(
                    "SYNC_ROW_WARN | row=%d | field=Специализация | reason=%s | action=use_fallback(%s)",
                    index,
                    exc,
                    service_type,
                )

            try:
                is_available = _parse_bool(row, "Доступен", index, required=True)
            except SheetValidationError as exc:
                is_available = existing.is_available if existing is not None else False
                logger.warning(
                    "SYNC_ROW_WARN | row=%d | field=Доступен | reason=%s | action=use_fallback(%s)",
                    index,
                    exc,
                    is_available,
                )

            category_name = _col(row, "Категория")
            category_id = existing.category_id if existing is not None else None
            if category_name and category_name not in ("-", "—"):
                category_id = cat_cache.get(category_name)
                if category_id is None:
                    new_cat = ServiceCategory(name=category_name)
                    session.add(new_cat)
                    await session.flush()
                    category_id = new_cat.id
                    cat_cache[category_name] = category_id
                    logger.info(
                        "SYNC_CATEGORY_CREATE | row=%d | name=%s | id=%s",
                        index,
                        category_name,
                        category_id,
                    )
            elif service_type == "upgrade":
                category_id = None

            def _safe_float(key: str, fallback: float | None) -> float | None:
                try:
                    return _parse_float(row, key, index)
                except SheetValidationError as exc:
                    logger.warning(
                        "SYNC_ROW_WARN | row=%d | field=%s | reason=%s | action=use_existing",
                        index,
                        key,
                        exc,
                    )
                    return fallback

            def _safe_bool(
                key: str,
                fallback: bool | None,
                *,
                required: bool = False,
            ) -> bool | None:
                try:
                    return _parse_bool(row, key, index, required=required)
                except SheetValidationError as exc:
                    logger.warning(
                        "SYNC_ROW_WARN | row=%d | field=%s | reason=%s | action=use_existing",
                        index,
                        key,
                        exc,
                    )
                    return fallback

            def _safe_time(key: str, fallback: str | None) -> str | None:
                try:
                    return _parse_time(row, key, index)
                except SheetValidationError as exc:
                    logger.warning(
                        "SYNC_ROW_WARN | row=%d | field=%s | reason=%s | action=use_existing",
                        index,
                        key,
                        exc,
                    )
                    return fallback

            yandex_rating = _safe_float(
                "Рейтинг Я.Карты",
                existing.yandex_rating if existing is not None else None,
            )
            phone = _col(row, "Телефон")
            if phone and phone.startswith("#"):
                phone = None

            partnership_status = _col(row, "Статус")
            if partnership_status is None and existing is not None:
                partnership_status = existing.partnership_status

            has_hydroisolation = _safe_bool(
                "Гидроизоляция",
                existing.has_hydroisolation if existing is not None else False,
            )
            diagnostics_included = _safe_bool(
                "Входит в стоимость",
                existing.diagnostics_included if existing is not None else False,
            )
            registration_complete = _safe_bool(
                "Завершена",
                existing.registration_complete if existing is not None else False,
            )

            open_time = _safe_time(
                "Открытие",
                existing.open_time if existing is not None else None,
            )
            close_time = _safe_time(
                "Закрытие",
                existing.close_time if existing is not None else None,
            )
            diagnostics_price = _safe_float(
                "Диагностика",
                existing.diagnostics_price if existing is not None else None,
            )

            payload = {
                "name": name,
                "service_type": service_type,
                "is_available": is_available,
                "category_id": category_id,
                "address": _col(row, "Адрес"),
                "yandex_rating": yandex_rating,
                "nearest_metro": _col(row, "Метро ближ."),
                "phone": phone,
                "telegram_handle": _col(row, "Telegram"),
                "partnership_status": partnership_status,
                "main_brand_scooter": _col(row, "Основной бренд самокатов"),
                "open_time": open_time,
                "close_time": close_time,
                "hydroisolation_price": _col(row, "Цена гидроизоляции"),
                "diagnostics_price": diagnostics_price,
                "upgrade_categories": _col(row, "Категории апгрейда"),
                "working_days": _col(row, "Рабочие дни"),
            }
            if has_hydroisolation is not None:
                payload["has_hydroisolation"] = has_hydroisolation
            if diagnostics_included is not None:
                payload["diagnostics_included"] = diagnostics_included
            if registration_complete is not None:
                payload["registration_complete"] = registration_complete

            if existing:
                changed = False
                for attr, value in payload.items():
                    if getattr(existing, attr) != value:
                        setattr(existing, attr, value)
                        changed = True
                if changed:
                    updated += 1
                else:
                    unchanged += 1
            else:
                session.add(Service(id=service_id, **payload))
                added += 1

            draft_owner = (
                await session.execute(
                    select(ServiceDraft).where(ServiceDraft.service_id == service_id)
                )
            ).scalar_one_or_none()
            if draft_owner is not None:
                draft_owner.draft_name = payload.get("name")
                draft_owner.draft_service_type = payload.get("service_type")
                draft_owner.draft_category = (
                    category_name
                    if (category_id is not None and service_type != "upgrade")
                    else None
                )
                draft_owner.draft_address = payload.get("address")
                draft_owner.draft_metro = payload.get("nearest_metro")
                draft_owner.draft_phone = payload.get("phone")
                draft_owner.draft_telegram = payload.get("telegram_handle")
                draft_owner.draft_open_time = payload.get("open_time")
                draft_owner.draft_close_time = payload.get("close_time")
                draft_owner.draft_hydroisolation = bool(
                    payload.get(
                        "has_hydroisolation",
                        draft_owner.draft_hydroisolation,
                    )
                )
                draft_owner.draft_hydro_price = (
                    payload.get("hydroisolation_price")
                    if draft_owner.draft_hydroisolation
                    else None
                )
                draft_owner.draft_diagnostics_price = payload.get("diagnostics_price")
                draft_owner.draft_diag_included = bool(
                    payload.get(
                        "diagnostics_included",
                        draft_owner.draft_diag_included,
                    )
                )
                draft_owner.draft_upgrade_categories = payload.get("upgrade_categories")
                draft_owner.draft_working_days = payload.get("working_days")
                if registration_complete is not None:
                    draft_owner.registration_complete = registration_complete
                if partnership_status in {
                    "ожидает",
                    "активный",
                    "приостановлен",
                    "отклонён",
                }:
                    draft_owner.status = partnership_status

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    logger.info(
        "SYNC_RESULT | services_added=%d | services_updated=%d | services_unchanged=%d | services_skipped=%d",
        added,
        updated,
        unchanged,
        skipped,
    )
    if dry_run:
        business_logger.info(
            "SYNC_DRY_RUN_RESULT | services_added=%d | services_updated=%d",
            added,
            updated,
        )

    return added + updated


async def sync_bank_details_from_sheet(*, dry_run: bool = False) -> int:
    if not _is_available():
        return 0

    from client_bot.core.config import GOOGLE_SHEET_ID, SHEETS_TAB_BANK_DETAILS

    rows = _fetch_via_sa(
        GOOGLE_SHEET_ID,
        SHEETS_TAB_BANK_DETAILS,
        headers=_BANK_HEADERS,
    )

    added = 0
    updated = 0
    unchanged = 0
    skipped = 0

    seen_ids: set[int] = set()

    async with async_session() as session:
        for index, row in enumerate(rows, start=2):
            if all((str(v).strip() == "" for v in row.values())):
                continue

            try:
                service_id = _parse_int(row, "ID", index, required=True)
            except SheetValidationError as exc:
                logger.warning("SYNC_BANK_ROW_SKIP | row=%d | reason=%s", index, exc)
                skipped += 1
                continue

            if service_id in seen_ids:
                logger.warning(
                    "SYNC_BANK_ROW_SKIP | row=%d | reason=duplicate_id | id=%s",
                    index,
                    service_id,
                )
                skipped += 1
                continue
            seen_ids.add(service_id)

            service_exists = (
                await session.execute(
                    select(Service.id).where(Service.id == service_id)
                )
            ).scalar_one_or_none()
            if service_exists is None:
                logger.warning(
                    "SYNC_BANK_ROW_SKIP | row=%d | reason=service_not_found | id=%s",
                    index,
                    service_id,
                )
                skipped += 1
                continue

            payload = {
                "legal_form": _col(row, "Форма"),
                "tax_system": _col(row, "Налогообложение"),
                "bank_account": _col(row, "Расч. счёт"),
                "bank_name": _col(row, "Банк"),
                "bik": _col(row, "БИК"),
                "corr_account": _col(row, "Корр. счёт"),
                "org_name": _col(row, "Организация"),
                "inn": _col(row, "ИНН"),
            }

            bank = (
                await session.execute(
                    select(ServiceBankDetails).where(
                        ServiceBankDetails.service_id == service_id
                    )
                )
            ).scalar_one_or_none()

            if bank is None:
                bank = ServiceBankDetails(service_id=service_id, **payload)
                session.add(bank)
                added += 1
            else:
                changed = False
                for attr, value in payload.items():
                    if getattr(bank, attr) != value:
                        setattr(bank, attr, value)
                        changed = True
                if changed:
                    updated += 1
                else:
                    unchanged += 1

            draft_owner = (
                await session.execute(
                    select(ServiceDraft).where(ServiceDraft.service_id == service_id)
                )
            ).scalar_one_or_none()
            if draft_owner is not None:
                draft_owner.draft_legal_form = payload["legal_form"]
                draft_owner.draft_tax_system = payload["tax_system"]
                draft_owner.draft_bank_account = payload["bank_account"]
                draft_owner.draft_bank_name = payload["bank_name"]
                draft_owner.draft_bik = payload["bik"]
                draft_owner.draft_corr_account = payload["corr_account"]
                draft_owner.draft_org_name = payload["org_name"]
                draft_owner.draft_inn = payload["inn"]

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    logger.info(
        "SYNC_RESULT | bank_added=%d | bank_updated=%d | bank_unchanged=%d | bank_skipped=%d",
        added,
        updated,
        unchanged,
        skipped,
    )
    if dry_run:
        business_logger.info(
            "SYNC_DRY_RUN_RESULT | bank_added=%d | bank_updated=%d",
            added,
            updated,
        )

    return added + updated


async def run_full_sync(*, first_run: bool = False, dry_run: bool = False) -> None:
    import asyncio

    from client_bot.services.sheets_writer import (
        sync_all_clients_to_sheet,
        sync_all_orders_to_sheet,
        sync_all_service_metrics_to_sheet,
    )

    try:
        synced_services = await sync_services_from_sheet(
            first_run=first_run,
            dry_run=dry_run,
        )
        synced_bank = await sync_bank_details_from_sheet(dry_run=dry_run)
    except Exception as exc:
        logger.error("SYNC_ERR | error_type=%s | error=%s", type(exc).__name__, exc)
        if first_run:
            raise
        return

    if dry_run:
        business_logger.info(
            "SYNC_HEALTH_OK | services_touched=%d | bank_touched=%d",
            synced_services,
            synced_bank,
        )
        return

    try:
        await asyncio.to_thread(sync_all_orders_to_sheet)
    except Exception as exc:
        logger.warning("SYNC_ORDERS_ERR | error=%s", exc)

    try:
        await asyncio.to_thread(sync_all_clients_to_sheet)
    except Exception as exc:
        logger.warning("SYNC_CLIENTS_ERR | error=%s", exc)

    try:
        await asyncio.to_thread(sync_all_service_metrics_to_sheet)
    except Exception as exc:
        logger.warning("SYNC_SERVICE_METRICS_ERR | error=%s", exc)
