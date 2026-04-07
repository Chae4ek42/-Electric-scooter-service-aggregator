"""
Синхронизация данных из публичной Google Таблицы (без credentials).

Читает через CSV-экспорт:
  https://docs.google.com/spreadsheets/d/{ID}/gviz/tq?tqx=out:csv&sheet={ЛИСТ}

Структура таблицы
─────────────────────────────────────────────────────
Лист «Сервисы»
  Столбец «Название»          — название сервис-центра
  Столбец «Рейтинг Я.Карты»  — число с плавающей точкой, напр. 4.8
  Столбец «Телефон»           — номер телефона
  Столбец «Telegram»          — ссылка/хэндл Telegram
  Столбец «Адрес»             — строка адреса
  Столбец «Метро ближ.»       — название ближайшей станции метро
  Столбец «Специализация»     — ремонт | апгрейд | комплекс
  Столбец «Статус»            — статус партнёрства (напр. «Заключён договор»)
  Столбец «Доступен»          — Да | Нет
  Столбец «Категория»         — Механика | Электрика | - (пусто = без категории)

  Регистр заголовков и значений не важен.
  Дополнительные (неизвестные) столбцы игнорируются.

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


async def _fetch_csv(sheet_id: str, sheet_name: str) -> list[dict[str, Any]]:
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


async def sync_services_from_sheet() -> int:
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

    from bot.core.config import GOOGLE_SHEET_ID

    # Ошибка сети / HTTP — логируем с типом и пробрасываем (бот не стартует)
    try:
        rows = await _fetch_csv(GOOGLE_SHEET_ID, "Сервисы")
    except Exception as exc:
        logger.error(
            "Sheets sync: не удалось загрузить таблицу — %s: %s",
            type(exc).__name__,
            exc,
        )
        raise

    count = 0
    async with async_session() as session:
        cats = (await session.execute(select(ServiceCategory))).scalars().all()
        cat_cache: dict[str, int] = {c.name: c.id for c in cats}

        for row in rows:
            # Читаем только ожидаемые столбцы; остальные — игнорируются
            name = _col(row, "Название")
            if not name:
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
            telegram_handle = _col(row, "Telegram") or None
            partnership_status = _col(row, "Статус") or None

            existing = (
                await session.execute(select(Service).where(Service.name == name))
            ).scalar_one_or_none()

            if existing:
                # Обновляем service_type только если тип получен из таблицы
                if stype:
                    existing.service_type = stype
                existing.is_available = available
                existing.category_id = cat_id
                existing.address = address
                existing.yandex_rating = yandex_rating
                existing.nearest_metro = nearest_metro
                existing.phone = phone
                existing.telegram_handle = telegram_handle
                existing.partnership_status = partnership_status
                count += 1
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
                    )
                )
                count += 1

        await session.commit()

    logger.info("Sheets sync «Сервисы»: обновлено/добавлено %d", count)

    if rows and count == 0:
        raise ValueError(
            f"Sheets sync: таблица содержит {len(rows)} строк(и), "
            "но ни одна не загружена — проверьте столбцы «Название» и «Специализация»"
        )

    return count


async def run_full_sync() -> None:
    await sync_services_from_sheet()
