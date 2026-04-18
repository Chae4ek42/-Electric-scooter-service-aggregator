"""Seed database with initial catalogues + Moscow metro stations."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select

from bot.core.database import async_session, engine
from bot.domain.models import Base, Brand, MetroStation, Model, ServiceCategory

logger = logging.getLogger(__name__)

# ── Каталог брендов и моделей ────────────────────────────────
# «Другое» добавляется автоматически в конец каждого бренда при сидировании.

BRANDS_MODELS: dict[str, list[str]] = {
    "Kugoo": [
        "Kugoo S1",
        "Kugoo S2 Pro",
        "Kugoo S3",
        "Kugoo S3 Pro",
        "Kugoo S4",
        "Kugoo S4 Pro",
        "Kugoo G1",
        "Kugoo G2 Pro",
        "Kugoo G55",
        "Kugoo M2 Pro",
        "Kugoo M4",
        "Kugoo M4 Pro",
        "Kugoo Max Speed",
        "Kugoo Max Speed Pro",
        "Kugoo G-Booster",
        "Kugoo X3",
    ],
    "Xiaomi": [
        "Mi Electric Scooter 1S",
        "Mi Electric Scooter 3",
        "Mi Electric Scooter Pro 2",
        "Mi Electric Scooter 4",
        "Mi Electric Scooter 4 Pro",
        "Mi Electric Scooter 4 Ultra",
        "Mi Electric Scooter Lite",
    ],
    "Ninebot (Segway)": [
        "Ninebot KickScooter E22",
        "Ninebot KickScooter E22E",
        "Ninebot KickScooter E25",
        "Ninebot KickScooter E25E",
        "Ninebot KickScooter F40",
        "Ninebot KickScooter F65",
        "Ninebot KickScooter G30",
        "Ninebot KickScooter G30D",
        "Ninebot KickScooter G30LP",
        "Ninebot KickScooter G65",
        "Ninebot KickScooter Max Plus",
        "Ninebot KickScooter Air T15",
    ],
    "Aovo": [
        "Aovo Bogist C1 Pro",
        "Aovo Bogist M5 Pro",
        "Aovo Pro M365S",
        "Aovo EX10",
        "Aovo EX80",
    ],
    "Dualtron": [
        "Dualtron Mini",
        "Dualtron Mini Lite",
        "Dualtron Victor",
        "Dualtron Victor Luxury",
        "Dualtron Thunder",
        "Dualtron Thunder II",
        "Dualtron Storm",
        "Dualtron Eagle Pro",
        "Dualtron Spider",
        "Dualtron Spider Pro",
        "Dualtron Ultra 2",
        "Dualtron Dream",
    ],
    "Zero Scooters": [
        "Zero 8",
        "Zero 8X",
        "Zero 9",
        "Zero 10",
        "Zero 10X",
        "Zero 11X",
    ],
    "Kaabo": [
        "Kaabo Mantis 8",
        "Kaabo Mantis 10",
        "Kaabo Mantis Pro",
        "Kaabo Wolf Warrior 11",
        "Kaabo Wolf Warrior X",
        "Kaabo Skywalker 8S",
        "Kaabo Skywalker 10H",
    ],
    "VSETT": [
        "VSETT 8",
        "VSETT 8+",
        "VSETT 10+",
        "VSETT 50+",
    ],
    "Speedway (Minimotors)": [
        "Speedway 4",
        "Speedway 5",
        "Speedway Leger",
        "Speedway Leger Pro",
    ],
    "Tribe": [
        "Tribe Himba",
        "Tribe Ostrich",
        "Tribe Falcon",
        "Tribe Ranger",
    ],
    "Yokamura": [
        "Yokamura X7",
        "Yokamura X10",
        "Yokamura X15",
        "Yokamura RX-1",
    ],
    "Hoverbot": [
        "Hoverbot A10",
        "Hoverbot A10 Elite",
        "Hoverbot A11",
        "Hoverbot A15",
    ],
    "E-twow": [
        "E-twow S2 Booster",
        "E-twow Booster Lite",
        "E-twow Booster Plus",
        "E-twow GT SE",
    ],
    "Joyor": [
        "Joyor F3",
        "Joyor F5",
        "Joyor G5",
        "Joyor S5+",
        "Joyor X5",
    ],
    "Whoosh": [
        "Whoosh W1",
        "Whoosh W2",
    ],
}

# ── Станции метро Москвы ─────────────────────────────────────

MOSCOW_METRO: list[tuple[str, str]] = [
    # Сокольническая (1)
    ("Бульвар Рокоссовского", "Сокольническая"),
    ("Черкизовская", "Сокольническая"),
    ("Преображенская площадь", "Сокольническая"),
    ("Сокольники", "Сокольническая"),
    ("Красносельская", "Сокольническая"),
    ("Комсомольская", "Сокольническая"),
    ("Красные Ворота", "Сокольническая"),
    ("Чистые пруды", "Сокольническая"),
    ("Лубянка", "Сокольническая"),
    ("Охотный Ряд", "Сокольническая"),
    ("Библиотека имени Ленина", "Сокольническая"),
    ("Кропоткинская", "Сокольническая"),
    ("Парк культуры", "Сокольническая"),
    ("Фрунзенская", "Сокольническая"),
    ("Спортивная", "Сокольническая"),
    ("Воробьёвы горы", "Сокольническая"),
    ("Университет", "Сокольническая"),
    ("Проспект Вернадского", "Сокольническая"),
    ("Юго-Западная", "Сокольническая"),
    ("Тропарёво", "Сокольническая"),
    ("Румянцево", "Сокольническая"),
    ("Саларьево", "Сокольническая"),
    ("Филатов Луг", "Сокольническая"),
    ("Прокшино", "Сокольническая"),
    ("Ольховая", "Сокольническая"),
    ("Коммунарка", "Сокольническая"),
    # Замоскворецкая (2)
    ("Ховрино", "Замоскворецкая"),
    ("Беломорская", "Замоскворецкая"),
    ("Речной вокзал", "Замоскворецкая"),
    ("Водный стадион", "Замоскворецкая"),
    ("Войковская", "Замоскворецкая"),
    ("Сокол", "Замоскворецкая"),
    ("Аэропорт", "Замоскворецкая"),
    ("Динамо", "Замоскворецкая"),
    ("Белорусская", "Замоскворецкая"),
    ("Маяковская", "Замоскворецкая"),
    ("Тверская", "Замоскворецкая"),
    ("Театральная", "Замоскворецкая"),
    ("Новокузнецкая", "Замоскворецкая"),
    ("Павелецкая", "Замоскворецкая"),
    ("Автозаводская", "Замоскворецкая"),
    ("Технопарк", "Замоскворецкая"),
    ("Коломенская", "Замоскворецкая"),
    ("Каширская", "Замоскворецкая"),
    ("Кантемировская", "Замоскворецкая"),
    ("Царицыно", "Замоскворецкая"),
    ("Орехово", "Замоскворецкая"),
    ("Домодедовская", "Замоскворецкая"),
    ("Красногвардейская", "Замоскворецкая"),
    ("Алма-Атинская", "Замоскворецкая"),
    # Арбатско-Покровская (3)
    ("Щёлковская", "Арбатско-Покровская"),
    ("Первомайская", "Арбатско-Покровская"),
    ("Измайловская", "Арбатско-Покровская"),
    ("Партизанская", "Арбатско-Покровская"),
    ("Семёновская", "Арбатско-Покровская"),
    ("Электрозаводская", "Арбатско-Покровская"),
    ("Бауманская", "Арбатско-Покровская"),
    ("Курская", "Арбатско-Покровская"),
    ("Площадь Революции", "Арбатско-Покровская"),
    ("Арбатская", "Арбатско-Покровская"),
    ("Смоленская", "Арбатско-Покровская"),
    ("Киевская", "Арбатско-Покровская"),
    ("Парк Победы", "Арбатско-Покровская"),
    ("Славянский бульвар", "Арбатско-Покровская"),
    ("Кунцевская", "Арбатско-Покровская"),
    ("Молодёжная", "Арбатско-Покровская"),
    ("Крылатское", "Арбатско-Покровская"),
    ("Строгино", "Арбатско-Покровская"),
    ("Мякинино", "Арбатско-Покровская"),
    ("Волоколамская", "Арбатско-Покровская"),
    ("Митино", "Арбатско-Покровская"),
    ("Пятницкое шоссе", "Арбатско-Покровская"),
    # Филёвская (4)
    ("Кунцевская", "Филёвская"),
    ("Пионерская", "Филёвская"),
    ("Филёвский парк", "Филёвская"),
    ("Багратионовская", "Филёвская"),
    ("Фили", "Филёвская"),
    ("Кутузовская", "Филёвская"),
    ("Студенческая", "Филёвская"),
    ("Киевская", "Филёвская"),
    ("Смоленская", "Филёвская"),
    ("Арбатская", "Филёвская"),
    ("Александровский сад", "Филёвская"),
    ("Выставочная", "Филёвская"),
    ("Международная", "Филёвская"),
    # Кольцевая (5)
    ("Комсомольская", "Кольцевая"),
    ("Курская", "Кольцевая"),
    ("Таганская", "Кольцевая"),
    ("Павелецкая", "Кольцевая"),
    ("Добрынинская", "Кольцевая"),
    ("Октябрьская", "Кольцевая"),
    ("Парк культуры", "Кольцевая"),
    ("Киевская", "Кольцевая"),
    ("Краснопресненская", "Кольцевая"),
    ("Белорусская", "Кольцевая"),
    ("Новослободская", "Кольцевая"),
    ("Проспект Мира", "Кольцевая"),
    # Калужско-Рижская (6)
    ("Медведково", "Калужско-Рижская"),
    ("Бабушкинская", "Калужско-Рижская"),
    ("Свиблово", "Калужско-Рижская"),
    ("Ботанический сад", "Калужско-Рижская"),
    ("ВДНХ", "Калужско-Рижская"),
    ("Алексеевская", "Калужско-Рижская"),
    ("Рижская", "Калужско-Рижская"),
    ("Проспект Мира", "Калужско-Рижская"),
    ("Сухаревская", "Калужско-Рижская"),
    ("Тургеневская", "Калужско-Рижская"),
    ("Китай-город", "Калужско-Рижская"),
    ("Третьяковская", "Калужско-Рижская"),
    ("Октябрьская", "Калужско-Рижская"),
    ("Шаболовская", "Калужско-Рижская"),
    ("Ленинский проспект", "Калужско-Рижская"),
    ("Академическая", "Калужско-Рижская"),
    ("Профсоюзная", "Калужско-Рижская"),
    ("Новые Черёмушки", "Калужско-Рижская"),
    ("Калужская", "Калужско-Рижская"),
    ("Беляево", "Калужско-Рижская"),
    ("Коньково", "Калужско-Рижская"),
    ("Тёплый Стан", "Калужско-Рижская"),
    ("Ясенево", "Калужско-Рижская"),
    ("Новоясеневская", "Калужско-Рижская"),
    # Таганско-Краснопресненская (7)
    ("Котельники", "Таганско-Краснопресненская"),
    ("Жулебино", "Таганско-Краснопресненская"),
    ("Лермонтовский проспект", "Таганско-Краснопресненская"),
    ("Выхино", "Таганско-Краснопресненская"),
    ("Рязанский проспект", "Таганско-Краснопресненская"),
    ("Кузьминки", "Таганско-Краснопресненская"),
    ("Текстильщики", "Таганско-Краснопресненская"),
    ("Волгоградский проспект", "Таганско-Краснопресненская"),
    ("Пролетарская", "Таганско-Краснопресненская"),
    ("Таганская", "Таганско-Краснопресненская"),
    ("Китай-город", "Таганско-Краснопресненская"),
    ("Кузнецкий Мост", "Таганско-Краснопресненская"),
    ("Пушкинская", "Таганско-Краснопресненская"),
    ("Баррикадная", "Таганско-Краснопресненская"),
    ("Улица 1905 года", "Таганско-Краснопресненская"),
    ("Беговая", "Таганско-Краснопресненская"),
    ("Полежаевская", "Таганско-Краснопресненская"),
    ("Октябрьское Поле", "Таганско-Краснопресненская"),
    ("Щукинская", "Таганско-Краснопресненская"),
    ("Тушинская", "Таганско-Краснопресненская"),
    ("Спартак", "Таганско-Краснопресненская"),
    ("Планерная", "Таганско-Краснопресненская"),
    ("Сходненская", "Таганско-Краснопресненская"),
    # Калининская (8)
    ("Новокосино", "Калининская"),
    ("Новогиреево", "Калининская"),
    ("Перово", "Калининская"),
    ("Шоссе Энтузиастов", "Калининская"),
    ("Авиамоторная", "Калининская"),
    ("Площадь Ильича", "Калининская"),
    ("Марксистская", "Калининская"),
    ("Третьяковская", "Калининская"),
    # Серпуховско-Тимирязевская (9)
    ("Алтуфьево", "Серпуховско-Тимирязевская"),
    ("Бибирево", "Серпуховско-Тимирязевская"),
    ("Отрадное", "Серпуховско-Тимирязевская"),
    ("Владыкино", "Серпуховско-Тимирязевская"),
    ("Петровско-Разумовская", "Серпуховско-Тимирязевская"),
    ("Тимирязевская", "Серпуховско-Тимирязевская"),
    ("Дмитровская", "Серпуховско-Тимирязевская"),
    ("Савёловская", "Серпуховско-Тимирязевская"),
    ("Менделеевская", "Серпуховско-Тимирязевская"),
    ("Цветной бульвар", "Серпуховско-Тимирязевская"),
    ("Чеховская", "Серпуховско-Тимирязевская"),
    ("Боровицкая", "Серпуховско-Тимирязевская"),
    ("Полянка", "Серпуховско-Тимирязевская"),
    ("Серпуховская", "Серпуховско-Тимирязевская"),
    ("Тульская", "Серпуховско-Тимирязевская"),
    ("Нагатинская", "Серпуховско-Тимирязевская"),
    ("Нагорная", "Серпуховско-Тимирязевская"),
    ("Нахимовский проспект", "Серпуховско-Тимирязевская"),
    ("Севастопольская", "Серпуховско-Тимирязевская"),
    ("Чертановская", "Серпуховско-Тимирязевская"),
    ("Южная", "Серпуховско-Тимирязевская"),
    ("Пражская", "Серпуховско-Тимирязевская"),
    ("Улица Академика Янгеля", "Серпуховско-Тимирязевская"),
    ("Аннино", "Серпуховско-Тимирязевская"),
    ("Бульвар Дмитрия Донского", "Серпуховско-Тимирязевская"),
    # Люблинско-Дмитровская (10)
    ("Физтех", "Люблинско-Дмитровская"),
    ("Лианозово", "Люблинско-Дмитровская"),
    ("Верхние Лихоборы", "Люблинско-Дмитровская"),
    ("Окружная", "Люблинско-Дмитровская"),
    ("Селигерская", "Люблинско-Дмитровская"),
    ("Фонвизинская", "Люблинско-Дмитровская"),
    ("Бутырская", "Люблинско-Дмитровская"),
    ("Марьина Роща", "Люблинско-Дмитровская"),
    ("Достоевская", "Люблинско-Дмитровская"),
    ("Трубная", "Люблинско-Дмитровская"),
    ("Сретенский бульвар", "Люблинско-Дмитровская"),
    ("Чкаловская", "Люблинско-Дмитровская"),
    ("Римская", "Люблинско-Дмитровская"),
    ("Крестьянская застава", "Люблинско-Дмитровская"),
    ("Дубровка", "Люблинско-Дмитровская"),
    ("Кожуховская", "Люблинско-Дмитровская"),
    ("Печатники", "Люблинско-Дмитровская"),
    ("Волжская", "Люблинско-Дмитровская"),
    ("Люблино", "Люблинско-Дмитровская"),
    ("Братиславская", "Люблинско-Дмитровская"),
    ("Марьино", "Люблинско-Дмитровская"),
    ("Борисово", "Люблинско-Дмитровская"),
    ("Шипиловская", "Люблинско-Дмитровская"),
    ("Зябликово", "Люблинско-Дмитровская"),
    # Большая кольцевая (11)
    ("Савёловская", "Большая кольцевая"),
    ("Марьина Роща", "Большая кольцевая"),
    ("Рижская", "Большая кольцевая"),
    ("Сокольники", "Большая кольцевая"),
    ("Электрозаводская", "Большая кольцевая"),
    ("Лефортово", "Большая кольцевая"),
    ("Авиамоторная", "Большая кольцевая"),
    ("Нижегородская", "Большая кольцевая"),
    ("Текстильщики", "Большая кольцевая"),
    ("Печатники", "Большая кольцевая"),
    ("Нагатинский Затон", "Большая кольцевая"),
    ("Кленовый бульвар", "Большая кольцевая"),
    ("Каширская", "Большая кольцевая"),
    ("Варшавская", "Большая кольцевая"),
    ("Каховская", "Большая кольцевая"),
    ("Зюзино", "Большая кольцевая"),
    ("Воронцовская", "Большая кольцевая"),
    ("Новаторская", "Большая кольцевая"),
    ("Проспект Вернадского", "Большая кольцевая"),
    ("Мичуринский проспект", "Большая кольцевая"),
    ("Аминьевская", "Большая кольцевая"),
    ("Давыдково", "Большая кольцевая"),
    ("Кунцевская", "Большая кольцевая"),
    ("Терехово", "Большая кольцевая"),
    ("Мнёвники", "Большая кольцевая"),
    ("Народное Ополчение", "Большая кольцевая"),
    ("Хорошёвская", "Большая кольцевая"),
    ("ЦСКА", "Большая кольцевая"),
    ("Шелепиха", "Большая кольцевая"),
    ("Деловой центр", "Большая кольцевая"),
    # Бутовская (12)
    ("Битцевский парк", "Бутовская"),
    ("Лесопарковая", "Бутовская"),
    ("Улица Старокачаловская", "Бутовская"),
    ("Улица Скобелевская", "Бутовская"),
    ("Бульвар Адмирала Ушакова", "Бутовская"),
    ("Улица Горчакова", "Бутовская"),
    ("Бунинская аллея", "Бутовская"),
    # Солнцевская (8А)
    ("Деловой центр", "Солнцевская"),
    ("Парк Победы", "Солнцевская"),
    ("Минская", "Солнцевская"),
    ("Ломоносовский проспект", "Солнцевская"),
    ("Раменки", "Солнцевская"),
    ("Мичуринский проспект", "Солнцевская"),
    ("Озёрная", "Солнцевская"),
    ("Говорово", "Солнцевская"),
    ("Солнцево", "Солнцевская"),
    ("Боровское шоссе", "Солнцевская"),
    ("Новопеределкино", "Солнцевская"),
    ("Рассказовка", "Солнцевская"),
    ("Пыхтино", "Солнцевская"),
    ("Аэропорт Внуково", "Солнцевская"),
    # Некрасовская (15)
    ("Нижегородская", "Некрасовская"),
    ("Стахановская", "Некрасовская"),
    ("Окская", "Некрасовская"),
    ("Юго-Восточная", "Некрасовская"),
    ("Косино", "Некрасовская"),
    ("Улица Дмитриевского", "Некрасовская"),
    ("Лухмановская", "Некрасовская"),
    ("Некрасовка", "Некрасовская"),
    # Троицкая (линия строится, частично открыта)
    ("Новаторская", "Троицкая"),
    ("Университет Дружбы Народов", "Троицкая"),
    ("Генерала Тюленева", "Троицкая"),
    ("Тютчевская", "Троицкая"),
    ("Корниловская", "Троицкая"),
    ("Коммунарка", "Троицкая"),
]


async def seed_database() -> None:
    """Populate tables if empty."""
    async with async_session() as session:
        # Brands & Models (including «Другое» per brand)
        existing = (
            await session.execute(select(func.count()).select_from(Brand))
        ).scalar()
        if not existing:
            try:
                for brand_name, model_names in BRANDS_MODELS.items():
                    brand = Brand(name=brand_name)
                    session.add(brand)
                    await session.flush()
                    for mn in model_names:
                        session.add(Model(brand_id=brand.id, name=mn))
                    # Кнопка «Другое» — всегда последняя
                    session.add(Model(brand_id=brand.id, name="Другое"))
                logger.info("Seeded brands & models")
            except Exception:
                await session.rollback()
                logger.info("Seed brands skipped — already inserted by another process")

        # Сервисные категории (справочник: Механика / Электрика)
        existing_cat = (
            await session.execute(select(func.count()).select_from(ServiceCategory))
        ).scalar()
        if not existing_cat:
            try:
                for cat_name in ("Механика", "Электрика"):
                    session.add(ServiceCategory(name=cat_name))
                await session.flush()
                logger.info("Seeded service categories")
            except Exception:
                await session.rollback()
                logger.info(
                    "Seed categories skipped — already inserted by another process"
                )

        # Станции метро
        existing_metro = (
            await session.execute(select(func.count()).select_from(MetroStation))
        ).scalar()
        if not existing_metro:
            try:
                for station_name, line_name in MOSCOW_METRO:
                    session.add(MetroStation(name=station_name, line=line_name))
                await session.flush()
                logger.info("Seeded %d Moscow metro stations", len(MOSCOW_METRO))
            except Exception:
                await session.rollback()
                logger.info("Seed metro skipped — already inserted by another process")

        await session.commit()


async def _run_migrations(conn: Any) -> None:
    """Apply incremental schema changes that SQLAlchemy create_all doesn't handle."""
    # model_custom_name в orders
    rows = await conn.execute("PRAGMA table_info('orders')")
    order_cols = {row[1] for row in await rows.fetchall()}
    if "model_custom_name" not in order_cols:
        await conn.execute("ALTER TABLE orders ADD COLUMN model_custom_name TEXT")
        logger.info("Migration: added orders.model_custom_name")
    # category_id может быть NULL (SQLite не требует явного ALTER)


async def init_db() -> None:
    """Create all tables, run migrations, seed initial data."""
    async with engine.begin() as conn:
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception as exc:
            if "already exists" not in str(exc):
                raise
        # Inline migrations — запускаем через raw aiosqlite connection
        raw = await conn.get_raw_connection()
        raw_conn = raw.driver_connection

        cursor = await raw_conn.execute("PRAGMA table_info('orders')")
        order_cols = {row[1] for row in await cursor.fetchall()}
        for col_def in (
            ("model_custom_name", "TEXT"),
            ("brand_custom_name", "TEXT"),
            ("problem_description", "TEXT"),
            ("upgrade_category", "TEXT"),
            ("diagnostics_price", "REAL"),
            ("total_cost", "REAL"),
            ("partner_comment", "TEXT"),
            ("reject_reason", "TEXT"),
            ("accepted_at", "TEXT"),
            ("completed_at", "TEXT"),
            ("estimate_cost", "REAL"),
            ("estimate_items", "TEXT"),
            ("estimate_deadline", "TEXT"),
            ("estimate_description", "TEXT"),
            ("client_visited", "INTEGER"),
            ("client_confirmed_estimate", "INTEGER"),
            ("dispute_reason", "TEXT"),
            ("refusal_reason", "TEXT"),
        ):
            col_name, col_type = col_def
            if col_name not in order_cols:
                await raw_conn.execute(
                    f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added orders.%s", col_name)
        await raw_conn.commit()

        cursor = await raw_conn.execute("PRAGMA table_info('services')")
        svc_cols = {row[1] for row in await cursor.fetchall()}
        for col_def in (
            ("address", "TEXT"),
            ("yandex_rating", "REAL"),
            ("nearest_metro", "TEXT"),
            ("phone", "TEXT"),
            ("telegram_handle", "TEXT"),
            ("partnership_status", "TEXT"),
            ("is_available", "INTEGER NOT NULL DEFAULT 1"),
            ("main_brand_scooter", "TEXT"),
            ("hydroisolation_price", "TEXT"),
            ("pause_until", "TEXT"),
            # Owner fields (merged from service_owners)
            ("telegram_id", "INTEGER"),
            ("status", "TEXT"),
            ("registered_at", "TEXT"),
            ("approved_at", "TEXT"),
            ("approved_by", "TEXT"),
            ("draft_name", "TEXT"),
            ("draft_service_type", "TEXT"),
            ("draft_category", "TEXT"),
            ("draft_address", "TEXT"),
            ("draft_metro", "TEXT"),
            ("draft_phone", "TEXT"),
            ("draft_telegram", "TEXT"),
            ("draft_open_time", "TEXT"),
            ("draft_close_time", "TEXT"),
            ("draft_hydroisolation", "INTEGER NOT NULL DEFAULT 0"),
            ("draft_hydro_price", "TEXT"),
            ("draft_diagnostics_price", "REAL"),
            ("draft_diag_included", "INTEGER NOT NULL DEFAULT 0"),
            ("draft_upgrade_categories", "TEXT"),
            ("draft_working_days", "TEXT"),
            ("draft_legal_form", "TEXT"),
            ("draft_tax_system", "TEXT"),
            ("draft_bank_account", "TEXT"),
            ("draft_bank_name", "TEXT"),
            ("draft_bik", "TEXT"),
            ("draft_corr_account", "TEXT"),
            ("draft_org_name", "TEXT"),
            ("draft_inn", "TEXT"),
            ("registration_complete", "INTEGER NOT NULL DEFAULT 0"),
        ):
            col_name, col_type = col_def
            if col_name not in svc_cols:
                await raw_conn.execute(
                    f"ALTER TABLE services ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added services.%s", col_name)

        # Migrate data from service_owners → services (if old table exists)
        cursor = await raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='service_owners'"
        )
        if await cursor.fetchone():
            cursor = await raw_conn.execute("SELECT * FROM service_owners")
            old_owners = await cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]
            for row in old_owners:
                owner = dict(zip(col_names, row))
                svc_id = owner.get("service_id")
                tg_id = owner.get("telegram_id")
                if svc_id:
                    # Link existing service with owner data
                    await raw_conn.execute(
                        "UPDATE services SET telegram_id=?, status=?, registered_at=?, "
                        "approved_at=?, approved_by=?, draft_name=?, draft_service_type=?, "
                        "draft_category=?, draft_address=?, draft_metro=?, draft_phone=?, "
                        "draft_telegram=?, draft_open_time=?, draft_close_time=?, "
                        "draft_hydroisolation=?, draft_hydro_price=?, "
                        "draft_diagnostics_price=?, draft_diag_included=?, "
                        "draft_upgrade_categories=?, draft_working_days=?, "
                        "draft_legal_form=?, draft_tax_system=?, draft_bank_account=?, "
                        "draft_bank_name=?, draft_bik=?, draft_corr_account=?, "
                        "draft_org_name=?, draft_inn=? WHERE id=?",
                        (
                            tg_id,
                            owner.get("status"),
                            owner.get("registered_at"),
                            owner.get("approved_at"),
                            owner.get("approved_by"),
                            owner.get("draft_name"),
                            owner.get("draft_service_type"),
                            owner.get("draft_category"),
                            owner.get("draft_address"),
                            owner.get("draft_metro"),
                            owner.get("draft_phone"),
                            owner.get("draft_telegram"),
                            owner.get("draft_open_time"),
                            owner.get("draft_close_time"),
                            owner.get("draft_hydroisolation", 0),
                            owner.get("draft_hydro_price"),
                            owner.get("draft_diagnostics_price"),
                            owner.get("draft_diag_included", 0),
                            owner.get("draft_upgrade_categories"),
                            owner.get("draft_working_days"),
                            owner.get("draft_legal_form"),
                            owner.get("draft_tax_system"),
                            owner.get("draft_bank_account"),
                            owner.get("draft_bank_name"),
                            owner.get("draft_bik"),
                            owner.get("draft_corr_account"),
                            owner.get("draft_org_name"),
                            owner.get("draft_inn"),
                            svc_id,
                        ),
                    )
                elif tg_id:
                    # Owner without linked service — create a new service record
                    await raw_conn.execute(
                        "INSERT INTO services (name, service_type, is_available, "
                        "has_hydroisolation, diagnostics_included, "
                        "telegram_id, status, registered_at, approved_at, approved_by, "
                        "draft_name, draft_service_type, draft_category, draft_address, "
                        "draft_metro, draft_phone, draft_telegram, draft_open_time, "
                        "draft_close_time, draft_hydroisolation, draft_hydro_price, "
                        "draft_diagnostics_price, draft_diag_included, "
                        "draft_upgrade_categories, draft_working_days, "
                        "draft_legal_form, draft_tax_system, draft_bank_account, "
                        "draft_bank_name, draft_bik, draft_corr_account, "
                        "draft_org_name, draft_inn) VALUES "
                        "(?,?,0,0,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            owner.get("draft_name") or "(не заполнено)",
                            owner.get("draft_service_type") or "repair",
                            tg_id,
                            owner.get("status"),
                            owner.get("registered_at"),
                            owner.get("approved_at"),
                            owner.get("approved_by"),
                            owner.get("draft_name"),
                            owner.get("draft_service_type"),
                            owner.get("draft_category"),
                            owner.get("draft_address"),
                            owner.get("draft_metro"),
                            owner.get("draft_phone"),
                            owner.get("draft_telegram"),
                            owner.get("draft_open_time"),
                            owner.get("draft_close_time"),
                            owner.get("draft_hydroisolation", 0),
                            owner.get("draft_hydro_price"),
                            owner.get("draft_diagnostics_price"),
                            owner.get("draft_diag_included", 0),
                            owner.get("draft_upgrade_categories"),
                            owner.get("draft_working_days"),
                            owner.get("draft_legal_form"),
                            owner.get("draft_tax_system"),
                            owner.get("draft_bank_account"),
                            owner.get("draft_bank_name"),
                            owner.get("draft_bik"),
                            owner.get("draft_corr_account"),
                            owner.get("draft_org_name"),
                            owner.get("draft_inn"),
                        ),
                    )
            # Update service_owner_settings FK from old owner_id to new service id
            cursor = await raw_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='service_owner_settings'"
            )
            if await cursor.fetchone():
                cursor = await raw_conn.execute(
                    "SELECT owner_id FROM service_owner_settings"
                )
                for (old_owner_id,) in await cursor.fetchall():
                    # Find the service_id the old owner pointed to
                    cursor2 = await raw_conn.execute(
                        "SELECT service_id FROM service_owners WHERE id=?",
                        (old_owner_id,),
                    )
                    row2 = await cursor2.fetchone()
                    if row2 and row2[0]:
                        await raw_conn.execute(
                            "UPDATE service_owner_settings SET owner_id=? WHERE owner_id=?",
                            (row2[0], old_owner_id),
                        )
            logger.info("Migration: merged service_owners data into services")
        await raw_conn.commit()

        # Migrate Service statuses from English to Russian (on services table now)
        _status_migration = {
            "pending": "ожидает",
            "active": "активный",
            "rejected": "отклонён",
            "suspended": "приостановлен",
        }
        cursor = await raw_conn.execute("PRAGMA table_info('services')")
        svc_cols2 = {row[1] for row in await cursor.fetchall()}
        if "status" in svc_cols2:
            for eng, rus in _status_migration.items():
                await raw_conn.execute(
                    "UPDATE services SET status = ? WHERE status = ?",
                    (rus, eng),
                )
            await raw_conn.commit()
            logger.info("Migration: converted services.status to Russian")

        cursor = await raw_conn.execute("PRAGMA table_info('metro_stations')")
        metro_cols = {row[1] for row in await cursor.fetchall()}
        for col_name in ("lat", "lon"):
            if col_name not in metro_cols:
                await raw_conn.execute(
                    f"ALTER TABLE metro_stations ADD COLUMN {col_name} REAL"
                )
                logger.info("Migration: added metro_stations.%s", col_name)

        cursor = await raw_conn.execute("PRAGMA table_info('user_actions')")
        action_cols = {row[1] for row in await cursor.fetchall()}
        for col_def in (
            ("bot_response", "TEXT"),
            ("bot_response_type", "TEXT"),
        ):
            col_name, col_type = col_def
            if col_name not in action_cols:
                await raw_conn.execute(
                    f"ALTER TABLE user_actions ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added user_actions.%s", col_name)

    await seed_database()


if __name__ == "__main__":
    asyncio.run(init_db())
