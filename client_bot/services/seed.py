"""Seed database with initial catalogues + Moscow metro stations."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select, text

from client_bot.core.database import async_session, engine
from client_bot.domain.models import Base, Brand, MetroStation, Model, ServiceCategory

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

        # Сервисные категории: гарантируем наличие базового справочника.
        required_categories = ("Механика", "Электрика", "Электрика + механика")
        existing_categories = set(
            (
                await session.execute(
                    select(ServiceCategory.name).where(
                        ServiceCategory.name.in_(required_categories)
                    )
                )
            )
            .scalars()
            .all()
        )
        missing_categories = [
            name for name in required_categories if name not in existing_categories
        ]
        if missing_categories:
            try:
                for cat_name in missing_categories:
                    session.add(ServiceCategory(name=cat_name))
                await session.flush()
                logger.info("Seeded missing service categories: %s", missing_categories)
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


async def _ensure_schema_versions_table(conn: Any) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version VARCHAR(128) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


async def _is_schema_version_applied(conn: Any, version: str) -> bool:
    row = (
        await conn.execute(
            text("SELECT 1 FROM schema_versions WHERE version = :version LIMIT 1"),
            {"version": version},
        )
    ).first()
    return row is not None


async def _mark_schema_version(conn: Any, version: str) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO schema_versions(version)
            VALUES (:version)
            ON CONFLICT(version) DO NOTHING
            """
        ),
        {"version": version},
    )


async def _run_postgres_migrations(conn: Any) -> None:
    """Apply lightweight migrations for PostgreSQL deployments."""
    await conn.execute(
        text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_code VARCHAR(6)")
    )
    await conn.execute(
        text(
            """
            UPDATE orders
            SET order_code = LPAD((id % 1000000)::text, 6, '0')
            WHERE order_code IS NULL OR BTRIM(order_code) = ''
            """
        )
    )

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sheets_retry_queue (
                id SERIAL PRIMARY KEY,
                service_id INTEGER NULL,
                operation VARCHAR(64) NOT NULL,
                payload_json TEXT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMP WITH TIME ZONE NULL,
                next_retry_at TIMESTAMP WITH TIME ZONE NULL,
                last_error TEXT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_sheets_retry_queue_next_retry_at "
            "ON sheets_retry_queue(next_retry_at)"
        )
    )


async def _run_postgres_notifications_migrations(conn: Any) -> None:
    """Add flexible partner/admin notification settings on PostgreSQL."""
    await conn.execute(
        text(
            "ALTER TABLE service_owner_settings "
            "ADD COLUMN IF NOT EXISTS notif_enabled BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE service_owner_settings "
            "ADD COLUMN IF NOT EXISTS notif_client_comment BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE service_owner_settings "
            "ADD COLUMN IF NOT EXISTS notif_estimate BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE service_owner_settings "
            "ADD COLUMN IF NOT EXISTS notif_dispute BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE service_owner_settings "
            "ADD COLUMN IF NOT EXISTS notif_completed BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS admin_notification_settings (
                admin_user_id BIGINT NOT NULL,
                scope VARCHAR(20) NOT NULL,
                notif_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                notif_client_dispute BOOLEAN NOT NULL DEFAULT TRUE,
                notif_client_cancel BOOLEAN NOT NULL DEFAULT TRUE,
                notif_no_center BOOLEAN NOT NULL DEFAULT TRUE,
                notif_order_completed BOOLEAN NOT NULL DEFAULT TRUE,
                notif_partner_application BOOLEAN NOT NULL DEFAULT TRUE,
                notif_partner_profile_update BOOLEAN NOT NULL DEFAULT TRUE,
                notif_partner_status_change BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (admin_user_id, scope)
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_admin_notification_settings_scope "
            "ON admin_notification_settings(scope)"
        )
    )


async def init_db() -> None:
    """Create all tables, run migrations, seed initial data."""
    if engine.url.get_backend_name() != "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_schema_versions_table(conn)
            migrations = [
                ("2026_01_postgres_baseline", _run_postgres_migrations),
                (
                    "2026_02_postgres_notifications_flex",
                    _run_postgres_notifications_migrations,
                ),
            ]
            for version, migration in migrations:
                if not await _is_schema_version_applied(conn, version):
                    await migration(conn)
                    await _mark_schema_version(conn, version)
                    logger.info("Migration: applied %s", version)

        await seed_database()
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_schema_versions_table(conn)

        # Inline migrations for legacy SQLite databases.
        raw = await conn.get_raw_connection()
        raw_conn = raw.driver_connection

        cursor = await raw_conn.execute("PRAGMA table_info('orders')")
        order_cols = {row[1] for row in await cursor.fetchall()}
        for col_name, col_type in (
            ("city", "TEXT"),
            ("client_address", "TEXT"),
            ("client_latitude", "REAL"),
            ("client_longitude", "REAL"),
            ("order_code", "TEXT"),
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
            ("price_change_reason", "TEXT"),
            ("price_updated_at", "TEXT"),
        ):
            if col_name not in order_cols:
                await raw_conn.execute(
                    f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added orders.%s", col_name)

        await raw_conn.execute(
            """
            UPDATE orders
            SET order_code = substr('000000' || CAST(id % 1000000 AS TEXT), -6, 6)
            WHERE order_code IS NULL OR TRIM(order_code) = ''
            """
        )

        await raw_conn.execute("""
            UPDATE orders
            SET city = CASE WHEN metro_station IS NOT NULL THEN 'Москва' ELSE city END
            WHERE city IS NULL OR TRIM(city) = ''
            """)

        await raw_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_service_status_created_at "
            "ON orders(service_id, status, created_at)"
        )
        await raw_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_user_status_created_at "
            "ON orders(user_id, status, created_at)"
        )

        await raw_conn.execute("""
            CREATE TABLE IF NOT EXISTS order_status_history (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                from_status TEXT NULL,
                to_status TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NULL,
                metadata_json TEXT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            )
            """)
        await raw_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_order_status_history_order_id_created_at "
            "ON order_status_history(order_id, created_at)"
        )

        cursor = await raw_conn.execute("PRAGMA table_info('services')")
        svc_cols = {row[1] for row in await cursor.fetchall()}

        for col_name, col_type in (
            ("city", "TEXT"),
            ("address", "TEXT"),
            ("latitude", "REAL"),
            ("longitude", "REAL"),
            ("yandex_rating", "REAL"),
            ("nearest_metro", "TEXT"),
            ("phone", "TEXT"),
            ("telegram_handle", "TEXT"),
            ("partnership_status", "TEXT"),
            ("is_available", "INTEGER NOT NULL DEFAULT 1"),
            ("main_brand_scooter", "TEXT"),
            ("open_time", "TEXT"),
            ("close_time", "TEXT"),
            ("has_hydroisolation", "INTEGER NOT NULL DEFAULT 0"),
            ("hydroisolation_price", "TEXT"),
            ("diagnostics_price", "REAL"),
            ("diagnostics_included", "INTEGER NOT NULL DEFAULT 0"),
            ("upgrade_categories", "TEXT"),
            ("working_days", "TEXT"),
            ("pause_until", "TEXT"),
            ("registration_complete", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col_name not in svc_cols:
                await raw_conn.execute(
                    f"ALTER TABLE services ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added services.%s", col_name)

        await raw_conn.execute("""
            UPDATE services
            SET city = 'Москва'
            WHERE (city IS NULL OR TRIM(city) = '')
              AND nearest_metro IS NOT NULL
            """)

        cursor = await raw_conn.execute("PRAGMA table_info('services')")
        svc_cols = {row[1] for row in await cursor.fetchall()}

        await raw_conn.execute("""
            UPDATE orders
            SET city = COALESCE(
                city,
                (SELECT s.city FROM services s WHERE s.id = orders.service_id),
                CASE WHEN metro_station IS NOT NULL THEN 'Москва' ELSE city END
            )
            WHERE city IS NULL OR TRIM(city) = ''
            """)

        await raw_conn.execute("""
            CREATE TABLE IF NOT EXISTS service_drafts (
                id INTEGER PRIMARY KEY,
                owner_user_id INTEGER NOT NULL UNIQUE,
                service_id INTEGER NULL,
                status TEXT NOT NULL DEFAULT 'ожидает',
                registered_at TEXT NULL,
                approved_at TEXT NULL,
                approved_by TEXT NULL,
                draft_name TEXT NULL,
                draft_service_type TEXT NULL,
                draft_category TEXT NULL,
                draft_city TEXT NULL,
                draft_address TEXT NULL,
                draft_latitude REAL NULL,
                draft_longitude REAL NULL,
                draft_metro TEXT NULL,
                draft_phone TEXT NULL,
                draft_telegram TEXT NULL,
                draft_open_time TEXT NULL,
                draft_close_time TEXT NULL,
                draft_hydroisolation INTEGER NOT NULL DEFAULT 0,
                draft_hydro_price TEXT NULL,
                draft_diagnostics_price REAL NULL,
                draft_diag_included INTEGER NOT NULL DEFAULT 0,
                draft_upgrade_categories TEXT NULL,
                draft_working_days TEXT NULL,
                draft_legal_form TEXT NULL,
                draft_tax_system TEXT NULL,
                draft_bank_account TEXT NULL,
                draft_bank_name TEXT NULL,
                draft_bik TEXT NULL,
                draft_corr_account TEXT NULL,
                draft_org_name TEXT NULL,
                draft_inn TEXT NULL,
                registration_complete INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(service_id) REFERENCES services(id)
            )
            """)
        await raw_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_service_drafts_status_registration_complete "
            "ON service_drafts(status, registration_complete)"
        )

        cursor = await raw_conn.execute("PRAGMA table_info('service_drafts')")
        draft_cols = {row[1] for row in await cursor.fetchall()}
        for col_name, col_type in (
            ("draft_city", "TEXT"),
            ("draft_latitude", "REAL"),
            ("draft_longitude", "REAL"),
        ):
            if col_name not in draft_cols:
                await raw_conn.execute(
                    f"ALTER TABLE service_drafts ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migration: added service_drafts.%s", col_name)

        await raw_conn.execute("""
            UPDATE service_drafts
            SET draft_city = COALESCE(
                draft_city,
                (SELECT s.city FROM services s WHERE s.id = service_drafts.service_id),
                'Москва'
            )
            WHERE (draft_city IS NULL OR TRIM(draft_city) = '')
              AND (draft_metro IS NOT NULL OR draft_address IS NOT NULL)
            """)

        await raw_conn.execute("""
            CREATE TABLE IF NOT EXISTS service_bank_details (
                service_id INTEGER PRIMARY KEY,
                legal_form TEXT NULL,
                tax_system TEXT NULL,
                bank_account TEXT NULL,
                bank_name TEXT NULL,
                bik TEXT NULL,
                corr_account TEXT NULL,
                org_name TEXT NULL,
                inn TEXT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(service_id) REFERENCES services(id)
            )
            """)

        # Legacy migration: services table used to hold draft/owner fields.
        legacy_cols = {
            "telegram_id",
            "status",
            "registered_at",
            "approved_at",
            "approved_by",
            "draft_name",
            "draft_service_type",
            "draft_address",
            "draft_phone",
            "draft_open_time",
            "draft_close_time",
        }
        if legacy_cols.issubset(svc_cols):
            await raw_conn.execute("""
                INSERT OR IGNORE INTO service_drafts (
                    owner_user_id,
                    service_id,
                    status,
                    registered_at,
                    approved_at,
                    approved_by,
                    draft_name,
                    draft_service_type,
                    draft_category,
                    draft_city,
                    draft_address,
                    draft_latitude,
                    draft_longitude,
                    draft_metro,
                    draft_phone,
                    draft_telegram,
                    draft_open_time,
                    draft_close_time,
                    draft_hydroisolation,
                    draft_hydro_price,
                    draft_diagnostics_price,
                    draft_diag_included,
                    draft_upgrade_categories,
                    draft_working_days,
                    draft_legal_form,
                    draft_tax_system,
                    draft_bank_account,
                    draft_bank_name,
                    draft_bik,
                    draft_corr_account,
                    draft_org_name,
                    draft_inn,
                    registration_complete
                )
                SELECT
                    telegram_id,
                    CASE WHEN COALESCE(registration_complete, 0) = 1 THEN id ELSE NULL END,
                    COALESCE(status, 'ожидает'),
                    registered_at,
                    approved_at,
                    approved_by,
                    COALESCE(draft_name, name),
                    COALESCE(draft_service_type, service_type),
                    draft_category,
                    COALESCE(city, 'Москва'),
                    COALESCE(draft_address, address),
                    latitude,
                    longitude,
                    draft_metro,
                    COALESCE(draft_phone, phone),
                    COALESCE(draft_telegram, telegram_handle),
                    COALESCE(draft_open_time, open_time),
                    COALESCE(draft_close_time, close_time),
                    COALESCE(draft_hydroisolation, has_hydroisolation, 0),
                    COALESCE(draft_hydro_price, hydroisolation_price),
                    COALESCE(draft_diagnostics_price, diagnostics_price),
                    COALESCE(draft_diag_included, diagnostics_included, 0),
                    COALESCE(draft_upgrade_categories, upgrade_categories),
                    COALESCE(draft_working_days, working_days),
                    draft_legal_form,
                    draft_tax_system,
                    draft_bank_account,
                    draft_bank_name,
                    draft_bik,
                    draft_corr_account,
                    draft_org_name,
                    draft_inn,
                    COALESCE(registration_complete, 0)
                FROM services
                WHERE telegram_id IS NOT NULL
                """)

            if {
                "draft_legal_form",
                "draft_tax_system",
                "draft_bank_account",
                "draft_bank_name",
                "draft_bik",
                "draft_corr_account",
                "draft_org_name",
                "draft_inn",
            }.issubset(svc_cols):
                await raw_conn.execute("""
                    INSERT OR IGNORE INTO service_bank_details (
                        service_id,
                        legal_form,
                        tax_system,
                        bank_account,
                        bank_name,
                        bik,
                        corr_account,
                        org_name,
                        inn
                    )
                    SELECT
                        id,
                        draft_legal_form,
                        draft_tax_system,
                        draft_bank_account,
                        draft_bank_name,
                        draft_bik,
                        draft_corr_account,
                        draft_org_name,
                        draft_inn
                    FROM services
                    WHERE
                        COALESCE(draft_bank_account, '') <> '' OR
                        COALESCE(draft_bank_name, '') <> '' OR
                        COALESCE(draft_bik, '') <> '' OR
                        COALESCE(draft_corr_account, '') <> '' OR
                        COALESCE(draft_org_name, '') <> '' OR
                        COALESCE(draft_inn, '') <> ''
                    """)

        # Drop legacy owner/draft columns from services after data migration.
        # Some old schemas had NOT NULL draft columns without defaults and broke inserts.
        legacy_service_cols = {
            "telegram_id",
            "status",
            "registered_at",
            "approved_at",
            "approved_by",
            "draft_name",
            "draft_service_type",
            "draft_category",
            "draft_address",
            "draft_metro",
            "draft_phone",
            "draft_telegram",
            "draft_open_time",
            "draft_close_time",
            "draft_hydroisolation",
            "draft_hydro_price",
            "draft_diagnostics_price",
            "draft_diag_included",
            "draft_upgrade_categories",
            "draft_working_days",
            "draft_legal_form",
            "draft_tax_system",
            "draft_bank_account",
            "draft_bank_name",
            "draft_bik",
            "draft_corr_account",
            "draft_org_name",
            "draft_inn",
        }

        if legacy_service_cols.intersection(svc_cols):

            def _coalesce_sql(*cols: str, default: str = "NULL") -> str:
                present = [c for c in cols if c in svc_cols]
                if not present:
                    return default
                expr = present[0]
                for c in present[1:]:
                    expr = f"COALESCE({expr}, {c})"
                if default != "NULL":
                    expr = f"COALESCE({expr}, {default})"
                return expr

            await raw_conn.execute("PRAGMA foreign_keys=OFF")
            await raw_conn.execute("DROP TABLE IF EXISTS services_new")
            await raw_conn.execute("""
                CREATE TABLE services_new (
                    id INTEGER PRIMARY KEY,
                    category_id INTEGER NULL,
                    name TEXT NOT NULL,
                    service_type TEXT NOT NULL,
                    is_available INTEGER NOT NULL DEFAULT 1,
                    city TEXT NULL,
                    address TEXT NULL,
                    latitude REAL NULL,
                    longitude REAL NULL,
                    yandex_rating REAL NULL,
                    nearest_metro TEXT NULL,
                    phone TEXT NULL,
                    telegram_handle TEXT NULL,
                    partnership_status TEXT NULL,
                    open_time TEXT NULL,
                    close_time TEXT NULL,
                    has_hydroisolation INTEGER NOT NULL DEFAULT 0,
                    hydroisolation_price TEXT NULL,
                    diagnostics_price REAL NULL,
                    diagnostics_included INTEGER NOT NULL DEFAULT 0,
                    main_brand_scooter TEXT NULL,
                    upgrade_categories TEXT NULL,
                    working_days TEXT NULL,
                    pause_until TEXT NULL,
                    registration_complete INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(category_id) REFERENCES service_categories(id)
                )
                """)

            await raw_conn.execute(f"""
                INSERT INTO services_new (
                    id,
                    category_id,
                    name,
                    service_type,
                    is_available,
                    city,
                    address,
                    latitude,
                    longitude,
                    yandex_rating,
                    nearest_metro,
                    phone,
                    telegram_handle,
                    partnership_status,
                    open_time,
                    close_time,
                    has_hydroisolation,
                    hydroisolation_price,
                    diagnostics_price,
                    diagnostics_included,
                    main_brand_scooter,
                    upgrade_categories,
                    working_days,
                    pause_until,
                    registration_complete
                )
                SELECT
                    id,
                    category_id,
                    {_coalesce_sql('name', default="'Без названия'")},
                    {_coalesce_sql('service_type', default="'repair'")},
                    {_coalesce_sql('is_available', default='1')},
                    {_coalesce_sql('city', default="'Москва'")},
                    {_coalesce_sql('address', 'draft_address')},
                    {_coalesce_sql('latitude')},
                    {_coalesce_sql('longitude')},
                    {_coalesce_sql('yandex_rating')},
                    {_coalesce_sql('nearest_metro', 'draft_metro')},
                    {_coalesce_sql('phone', 'draft_phone')},
                    {_coalesce_sql('telegram_handle', 'draft_telegram')},
                    {_coalesce_sql('partnership_status', 'status')},
                    {_coalesce_sql('open_time', 'draft_open_time')},
                    {_coalesce_sql('close_time', 'draft_close_time')},
                    {_coalesce_sql('has_hydroisolation', 'draft_hydroisolation', default='0')},
                    {_coalesce_sql('hydroisolation_price', 'draft_hydro_price')},
                    {_coalesce_sql('diagnostics_price', 'draft_diagnostics_price')},
                    {_coalesce_sql('diagnostics_included', 'draft_diag_included', default='0')},
                    {_coalesce_sql('main_brand_scooter')},
                    {_coalesce_sql('upgrade_categories', 'draft_upgrade_categories')},
                    {_coalesce_sql('working_days', 'draft_working_days')},
                    {_coalesce_sql('pause_until')},
                    {_coalesce_sql('registration_complete', default='0')}
                FROM services
                """)

            await raw_conn.execute("DROP TABLE services")
            await raw_conn.execute("ALTER TABLE services_new RENAME TO services")
            await raw_conn.execute("PRAGMA foreign_keys=ON")
            logger.info(
                "Migration: rebuilt services table without legacy owner/draft columns"
            )

            cursor = await raw_conn.execute("PRAGMA table_info('services')")
            svc_cols = {row[1] for row in await cursor.fetchall()}

        # service_owner_settings schema migration: owner_id -> service_id + owner_user_id.
        cursor = await raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='service_owner_settings'"
        )
        settings_exists = await cursor.fetchone()
        owner_settings_extra_cols = (
            ("notif_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("notif_client_comment", "INTEGER NOT NULL DEFAULT 1"),
            ("notif_estimate", "INTEGER NOT NULL DEFAULT 1"),
            ("notif_dispute", "INTEGER NOT NULL DEFAULT 1"),
            ("notif_completed", "INTEGER NOT NULL DEFAULT 1"),
        )
        if settings_exists:
            cursor = await raw_conn.execute(
                "PRAGMA table_info('service_owner_settings')"
            )
            settings_cols = {row[1] for row in await cursor.fetchall()}

            if "service_id" not in settings_cols and "owner_id" in settings_cols:
                await raw_conn.execute("""
                    CREATE TABLE service_owner_settings_new (
                        service_id INTEGER PRIMARY KEY,
                        owner_user_id INTEGER NOT NULL UNIQUE,
                        notif_enabled INTEGER NOT NULL DEFAULT 1,
                        notif_new_order INTEGER NOT NULL DEFAULT 1,
                        notif_cancel INTEGER NOT NULL DEFAULT 1,
                        notif_client_comment INTEGER NOT NULL DEFAULT 1,
                        notif_estimate INTEGER NOT NULL DEFAULT 1,
                        notif_dispute INTEGER NOT NULL DEFAULT 1,
                        notif_completed INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(service_id) REFERENCES services(id)
                    )
                    """)
                await raw_conn.execute("""
                    INSERT OR IGNORE INTO service_owner_settings_new (
                        service_id,
                        owner_user_id,
                        notif_enabled,
                        notif_new_order,
                        notif_cancel,
                        notif_client_comment,
                        notif_estimate,
                        notif_dispute,
                        notif_completed
                    )
                    SELECT
                        sos.owner_id,
                        COALESCE(sd.owner_user_id, sos.owner_id),
                        1,
                        COALESCE(sos.notif_new_order, 1),
                        COALESCE(sos.notif_cancel, 1),
                        1,
                        1,
                        1,
                        1
                    FROM service_owner_settings sos
                    LEFT JOIN service_drafts sd ON sd.service_id = sos.owner_id
                    WHERE sos.owner_id IS NOT NULL
                    """)
                await raw_conn.execute("DROP TABLE service_owner_settings")
                await raw_conn.execute(
                    "ALTER TABLE service_owner_settings_new RENAME TO service_owner_settings"
                )
            else:
                if "owner_user_id" not in settings_cols:
                    await raw_conn.execute(
                        "ALTER TABLE service_owner_settings ADD COLUMN owner_user_id INTEGER"
                    )
                await raw_conn.execute("""
                    UPDATE service_owner_settings
                    SET owner_user_id = COALESCE(
                        owner_user_id,
                        (
                            SELECT sd.owner_user_id
                            FROM service_drafts sd
                            WHERE sd.service_id = service_owner_settings.service_id
                            LIMIT 1
                        ),
                        service_id
                    )
                    """)

                cursor = await raw_conn.execute(
                    "PRAGMA table_info('service_owner_settings')"
                )
                settings_cols = {row[1] for row in await cursor.fetchall()}
                for col_name, col_type in owner_settings_extra_cols:
                    if col_name not in settings_cols:
                        await raw_conn.execute(
                            f"ALTER TABLE service_owner_settings ADD COLUMN {col_name} {col_type}"
                        )
        else:
            await raw_conn.execute("""
                CREATE TABLE service_owner_settings (
                    service_id INTEGER PRIMARY KEY,
                    owner_user_id INTEGER NOT NULL UNIQUE,
                    notif_enabled INTEGER NOT NULL DEFAULT 1,
                    notif_new_order INTEGER NOT NULL DEFAULT 1,
                    notif_cancel INTEGER NOT NULL DEFAULT 1,
                    notif_client_comment INTEGER NOT NULL DEFAULT 1,
                    notif_estimate INTEGER NOT NULL DEFAULT 1,
                    notif_dispute INTEGER NOT NULL DEFAULT 1,
                    notif_completed INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(service_id) REFERENCES services(id)
                )
                """)

        # Ensure default owner settings for every linked draft.
        await raw_conn.execute("""
            INSERT OR IGNORE INTO service_owner_settings (
                service_id,
                owner_user_id,
                notif_enabled,
                notif_new_order,
                notif_cancel,
                notif_client_comment,
                notif_estimate,
                notif_dispute,
                notif_completed
            )
            SELECT service_id, owner_user_id, 1, 1, 1, 1, 1, 1, 1
            FROM service_drafts
            WHERE service_id IS NOT NULL
            """)

        await raw_conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_notification_settings (
                admin_user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                notif_enabled INTEGER NOT NULL DEFAULT 1,
                notif_client_dispute INTEGER NOT NULL DEFAULT 1,
                notif_client_cancel INTEGER NOT NULL DEFAULT 1,
                notif_no_center INTEGER NOT NULL DEFAULT 1,
                notif_order_completed INTEGER NOT NULL DEFAULT 1,
                notif_partner_application INTEGER NOT NULL DEFAULT 1,
                notif_partner_profile_update INTEGER NOT NULL DEFAULT 1,
                notif_partner_status_change INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (admin_user_id, scope)
            )
            """)
        await raw_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_admin_notification_settings_scope "
            "ON admin_notification_settings(scope)"
        )

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

        # Seed "Электрика + механика" category for existing databases
        cursor = await raw_conn.execute(
            "SELECT COUNT(*) FROM service_categories WHERE name = 'Электрика + механика'"
        )
        row = await cursor.fetchone()
        if row and row[0] == 0:
            await raw_conn.execute(
                "INSERT INTO service_categories (name) VALUES ('Электрика + механика')"
            )
            logger.info("Migration: seeded service category 'Электрика + механика'")

        await raw_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor = await raw_conn.execute(
            "SELECT 1 FROM schema_versions WHERE version = '2026_01_sheets_retry_queue_v2' LIMIT 1"
        )
        queue_v2_applied = await cursor.fetchone()
        if not queue_v2_applied:
            cursor = await raw_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_retry_queue'"
            )
            queue_exists = await cursor.fetchone()
            queue_cols: set[str] = set()
            if queue_exists:
                cursor = await raw_conn.execute("PRAGMA table_info('sheets_retry_queue')")
                queue_cols = {row[1] for row in await cursor.fetchall()}

            await raw_conn.execute("DROP TABLE IF EXISTS sheets_retry_queue_new")
            await raw_conn.execute(
                """
                CREATE TABLE sheets_retry_queue_new (
                    id INTEGER PRIMARY KEY,
                    service_id INTEGER NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT NULL,
                    next_retry_at TEXT NULL,
                    last_error TEXT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            if queue_exists:
                service_expr = "service_id" if "service_id" in queue_cols else "NULL"
                operation_expr = "operation" if "operation" in queue_cols else "'sync_full'"
                payload_expr = (
                    "payload_json" if "payload_json" in queue_cols else "NULL"
                )
                attempts_expr = "attempts" if "attempts" in queue_cols else "0"
                last_attempt_expr = (
                    "last_attempt_at" if "last_attempt_at" in queue_cols else "NULL"
                )
                created_expr = "created_at" if "created_at" in queue_cols else "CURRENT_TIMESTAMP"

                await raw_conn.execute(
                    f"""
                    INSERT INTO sheets_retry_queue_new (
                        id,
                        service_id,
                        operation,
                        payload_json,
                        attempts,
                        last_attempt_at,
                        next_retry_at,
                        last_error,
                        created_at
                    )
                    SELECT
                        id,
                        {service_expr},
                        {operation_expr},
                        {payload_expr},
                        {attempts_expr},
                        {last_attempt_expr},
                        NULL,
                        NULL,
                        {created_expr}
                    FROM sheets_retry_queue
                    """
                )

            await raw_conn.execute("DROP TABLE IF EXISTS sheets_retry_queue")
            await raw_conn.execute(
                "ALTER TABLE sheets_retry_queue_new RENAME TO sheets_retry_queue"
            )
            await raw_conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_sheets_retry_queue_next_retry_at "
                "ON sheets_retry_queue(next_retry_at)"
            )
            await raw_conn.execute(
                "INSERT OR IGNORE INTO schema_versions(version) VALUES ('2026_01_sheets_retry_queue_v2')"
            )

        await raw_conn.execute(
            "INSERT OR IGNORE INTO schema_versions(version) VALUES ('2026_00_sqlite_legacy_bootstrap')"
        )

        await raw_conn.commit()

    await seed_database()


if __name__ == "__main__":
    asyncio.run(init_db())
