"""
Синхронизация данных из Google Таблицы.

Читает через Service Account (gspread) или CSV-экспорт:
  https://docs.google.com/spreadsheets/d/{ID}/gviz/tq?tqx=out:csv&sheet={ЛИСТ}

Порядок столбцов задаётся через переменную окружения ``SHEETS_COLUMNS``
(см. ``bot/core/config.py``).  Маппинг «заголовок → поле модели» описан
в ``_HEADER_TO_FIELD``.  Столбцы, не указанные в ``SHEETS_COLUMNS``,
при чтении игнорируются; при записи — не включаются в строку.

Регистр заголовков и значений не важен.

Если GOOGLE_SHEET_ID задан, но синхронизация завершилась с ошибкой
или загрузила 0 записей — поднимается исключение и бот не запускается.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import aiohttp
from sqlalchemy import select

from bot.core.database import async_session
from bot.domain.models import Service, ServiceCategory

logger = logging.getLogger(__name__)

_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/{sheet_id}"
    "/gviz/tq?tqx=out:csv&sheet={sheet_name}"
)

_TYPE_MAP: dict[str, str] = {
    "ремонт": "repair",
    "апгрейд": "upgrade",
    "комплекс": "complex",
}

# Маппинг «заголовок таблицы» → «внутренний ключ» (нижний регистр).
# Используется в _col() для поиска значений в строке.
_HEADER_TO_FIELD: dict[str, str] = {
    "название": "name",
    "рейтинг я.карты": "yandex_rating",
    "телефон": "phone",
    "telegram": "telegram_handle",
    "адрес": "address",
    "метро ближ.": "nearest_metro",
    "специализация": "service_type",
    "основной бренд самокатов": "main_brand_scooter",
    "статус": "partnership_status",
    "доступен": "is_available",
    "категория": "category",
    "открытие": "open_time",
    "закрытие": "close_time",
    "гидроизоляция": "has_hydroisolation",
    "цена гидроизоляции": "hydroisolation_price",
    "диагностика": "diagnostics_price",
    "входит в стоимость": "diagnostics_included",
    "категории апгрейда": "upgrade_categories",
    "рабочие дни": "working_days",
}


def _col(row: dict[str, str], key: str, default: str = "") -> str:
    """Регистронезависимый поиск столбца по имени."""
    key_lower = key.strip().lower()
    for k, v in row.items():
        if k.strip().lower() == key_lower:
            return str(v).strip()
    return default


def _is_available() -> bool:
    from bot.core.config import GOOGLE_SHEET_ID

    return bool(GOOGLE_SHEET_ID)


def _fetch_via_sa(sheet_id: str, sheet_name: str) -> list[dict[str, Any]] | None:
    """Try reading via Service Account. Returns None if SA not configured."""
    from bot.core.config import GOOGLE_SA_PATH

    if not GOOGLE_SA_PATH:
        return None
    try:
        from pathlib import Path

        import gspread
        from google.oauth2.service_account import Credentials

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
        ws = sh.worksheet(sheet_name)
        # get_all_values() устойчив к дублирующимся заголовкам в таблице
        all_values = ws.get_all_values()
        if not all_values:
            return []
        headers = all_values[0]
        # При дублях заголовков оставляем первое вхождение
        seen: set[str] = set()
        deduped_headers: list[str] = []
        for h in headers:
            if h in seen:
                deduped_headers.append(f"_{h}_dup")
            else:
                deduped_headers.append(h)
                seen.add(h)
        rows = []
        for row in all_values[1:]:
            padded = row + [""] * (len(deduped_headers) - len(row))
            rows.append(dict(zip(deduped_headers, padded)))
        return rows
    except Exception as exc:
        logger.warning("SA read failed: %s: %s", type(exc).__name__, exc)
        return None


async def _fetch_csv(sheet_id: str, sheet_name: str) -> list[dict[str, Any]]:
    # Try Service Account first (works with private sheets)
    sa_data = _fetch_via_sa(sheet_id, sheet_name)
    if sa_data is not None:
        return sa_data
    # Fallback: public CSV export
    url = _CSV_URL.format(sheet_id=sheet_id, sheet_name=sheet_name)
    async with aiohttp.ClientSession() as http:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"HTTP {resp.status} при загрузке листа «{sheet_name}»"
                )
            text = await resp.text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


async def sync_services_from_sheet(*, first_run: bool = False) -> int:
    """
    Читает лист «Сервисы» и делает upsert по названию сервиса.
    Неизвестные столбцы таблицы игнорируются.
    Возвращает количество обновлённых / добавленных строк.

    Raises:
        RuntimeError: если HTTP-запрос к таблице завершился ошибкой.
        ValueError:   если в таблице есть строки, но ни одна не загружена.
    """
    if not _is_available():
        logger.debug("Sheets sync пропущен (GOOGLE_SHEET_ID не задан)")
        return 0

    from bot.core.config import GOOGLE_SHEET_ID, SHEETS_COLUMNS, SHEETS_TAB_SERVICES

    if first_run:
        logger.info(
            "SYNC_START | columns_configured=%d | columns=%s",
            len(SHEETS_COLUMNS),
            ",".join(SHEETS_COLUMNS),
        )

    # Ошибка сети / HTTP — логируем с типом и пробрасываем (бот не стартует)
    try:
        rows = await _fetch_csv(GOOGLE_SHEET_ID, SHEETS_TAB_SERVICES)
    except Exception as exc:
        logger.error(
            "SYNC_FETCH_ERR | error_type=%s | error=%s",
            type(exc).__name__,
            exc,
        )
        raise

    if rows:
        sheet_headers = {k.strip().lower() for k in rows[0].keys()}
        configured = {c.strip().lower() for c in SHEETS_COLUMNS}
        missing = configured - sheet_headers
        extra = sheet_headers - configured
        if missing:
            logger.warning("SYNC_COLUMNS_MISMATCH | missing_in_sheet=%s", missing)
        if extra:
            logger.debug("SYNC_COLUMNS_EXTRA | extra_in_sheet=%s", extra)
        logger.info(
            "SYNC_FETCHED | rows=%d | sheet_headers=%d", len(rows), len(sheet_headers)
        )

    added = 0
    updated = 0
    skipped = 0  # rows with empty «Название» (skipped)
    unchanged = 0  # rows found in DB but no fields changed
    async with async_session() as session:
        cats = (await session.execute(select(ServiceCategory))).scalars().all()
        cat_cache: dict[str, int] = {c.name: c.id for c in cats}

        for row in rows:
            # Читаем только ожидаемые столбцы; остальные — игнорируются
            name = _col(row, "Название")
            if not name:
                skipped += 1
                continue

            raw_type = _col(row, "Специализация").lower()
            stype = _TYPE_MAP.get(raw_type)
            if not stype and raw_type:
                logger.warning(
                    "Лист «Сервисы»: неизвестная специализация «%s» у «%s»",
                    raw_type,
                    name,
                )

            raw_cat = _col(row, "Категория")
            cat_id: int | None = (
                cat_cache.get(raw_cat) if raw_cat not in ("-", "") else None
            )

            available = _col(row, "Доступен", "да").lower() in (
                "да",
                "yes",
                "1",
                "true",
            )

            address = _col(row, "Адрес") or None

            raw_rating = _col(row, "Рейтинг Я.Карты")
            yandex_rating: float | None = None
            if raw_rating:
                try:
                    yandex_rating = float(raw_rating.replace(",", "."))
                except ValueError:
                    logger.warning(
                        "Лист «Сервисы»: неверный рейтинг «%s» у «%s»", raw_rating, name
                    )

            nearest_metro = _col(row, "Метро ближ.") or None
            phone = _col(row, "Телефон") or None
            if phone and phone.startswith("#"):
                phone = None  # filter Sheets formula errors (#ERROR!, #REF!, etc.)
            telegram_handle = _col(row, "Telegram") or None
            raw_status = (_col(row, "Статус") or "").strip().lower()
            _PARTNER_STATUS_MAP: dict[str, str] = {
                "partner": "активный",
                "партнёр": "активный",
                "партнер": "активный",
                "активный": "активный",
                "active": "активный",
                "приостановлен": "приостановлен",
                "suspended": "приостановлен",
                "отклонён": "отклонён",
                "отклонен": "отклонён",
                "rejected": "отклонён",
                "ожидает": "ожидает",
                "pending": "ожидает",
            }
            partnership_status = _PARTNER_STATUS_MAP.get(raw_status, raw_status) or None
            main_brand_scooter = _col(row, "Основной бренд самокатов") or None

            open_time = _col(row, "Открытие") or None
            close_time = _col(row, "Закрытие") or None

            has_hydro = _col(row, "Гидроизоляция", "нет").lower() in (
                "да",
                "yes",
                "1",
                "true",
            )

            raw_diag = _col(row, "Диагностика")
            diagnostics_price: float | None = None
            if raw_diag:
                try:
                    diagnostics_price = float(raw_diag.replace(",", "."))
                except ValueError:
                    logger.warning(
                        "Лист «Сервисы»: неверная стоимость диагностики «%s» у «%s»",
                        raw_diag,
                        name,
                    )

            diag_included = _col(row, "Входит в стоимость", "нет").lower() in (
                "да",
                "yes",
                "1",
                "true",
            )

            hydro_price = _col(row, "Цена гидроизоляции") or None

            existing = (
                await session.execute(select(Service).where(Service.name == name))
            ).scalar_one_or_none()

            if existing:
                # Обновляем только если реально изменилось
                changed = False
                if stype and existing.service_type != stype:
                    existing.service_type = stype
                    changed = True
                for attr, val in [
                    ("is_available", available),
                    ("category_id", cat_id),
                    ("address", address),
                    ("yandex_rating", yandex_rating),
                    ("nearest_metro", nearest_metro),
                    ("phone", phone),
                    ("telegram_handle", telegram_handle),
                    ("partnership_status", partnership_status),
                    ("main_brand_scooter", main_brand_scooter),
                    ("open_time", open_time),
                    ("close_time", close_time),
                    ("has_hydroisolation", has_hydro),
                    ("hydroisolation_price", hydro_price),
                    ("diagnostics_price", diagnostics_price),
                    ("diagnostics_included", diag_included),
                ]:
                    if getattr(existing, attr) != val:
                        setattr(existing, attr, val)
                        changed = True
                if changed:
                    updated += 1
                else:
                    unchanged += 1
            else:
                # Для новых записей без специализации используем тип «комплекс»
                if not stype:
                    stype = "complex"
                session.add(
                    Service(
                        name=name,
                        service_type=stype,
                        is_available=available,
                        category_id=cat_id,
                        address=address,
                        yandex_rating=yandex_rating,
                        nearest_metro=nearest_metro,
                        phone=phone,
                        telegram_handle=telegram_handle,
                        partnership_status=partnership_status,
                        main_brand_scooter=main_brand_scooter,
                        open_time=open_time,
                        close_time=close_time,
                        has_hydroisolation=has_hydro,
                        hydroisolation_price=hydro_price,
                        diagnostics_price=diagnostics_price,
                        diagnostics_included=diag_included,
                    )
                )
                added += 1

        await session.commit()

    logger.info(
        "SYNC_RESULT | added=%d | updated=%d | unchanged=%d | skipped=%d",
        added,
        updated,
        unchanged,
        skipped,
    )

    processed = added + updated + unchanged
    if rows and processed == 0:
        raise ValueError(
            f"Sheets sync: таблица содержит {len(rows)} строк(и), "
            "но ни одна не загружена — проверьте столбцы «Название» и «Специализация»"
        )

    return added + updated


async def run_full_sync(*, first_run: bool = False) -> None:
    import asyncio
    from bot.services.sheets_writer import (
        sync_all_clients_to_sheet,
        sync_all_orders_to_sheet,
    )

    await sync_services_from_sheet(first_run=first_run)
    await asyncio.to_thread(sync_all_orders_to_sheet)
    await asyncio.to_thread(sync_all_clients_to_sheet)
