"""
Ранжирование и фильтрация сервис-центров под запрос пользователя.

Точка входа:
    results = await rank_services(ctx, session)

Стратегии близости подключаются через Protocol ProximityStrategy.
По умолчанию используется MetroProximityStrategy (без внешних API).
Для GPS-точности — подключить GeocodingProximityStrategy (см. TODO ниже).

Веса результирующего скора:
    WEIGHT_PROXIMITY  — насколько важна близость метро
    WEIGHT_RATING     — насколько важен рейтинг Я.Карт
    (меняются в одном месте, без правки логики)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.domain.models import MetroStation, Service, ServiceCategory
from bot.services.metro_graph import metro_transfer_distance

logger = logging.getLogger(__name__)

# ── Веса итогового скора ─────────────────────────────────────
WEIGHT_PROXIMITY: float = 0.6
WEIGHT_RATING: float = 0.4
MAX_YANDEX_RATING: float = 5.0

# ── Скоры близости по метро (transfer-graph based) ───────────
# dist=0  → та же станция (или одно название на разных линиях)
# dist=1  → прямой переход в вестибюле, физически рядом
# dist=2  → через одну промежуточную пересадку
# dist=3  → через две промежуточные пересадки
# dist≥4  → далеко; None → нет пути в графе
_DIST_SCORES: dict[int, float] = {
    0: 1.00,
    1: 0.85,
    2: 0.65,
    3: 0.45,
}
SCORE_METRO_FAR: float = 0.25  # dist≥4 или нет пути в графе
SCORE_METRO_UNKNOWN: float = 0.50  # нет данных о метро сервиса/пользователя


# ══════════════════════════════════════════════════════════════
# Утилита: расстояние по формуле Хаверсина
# ══════════════════════════════════════════════════════════════

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Возвращает расстояние в километрах между двумя точками на Земле.
    Использует формулу Хаверсина — точность достаточна для задач поиска метро.
    """
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def find_nearest_metro_by_coords(
    lat: float,
    lon: float,
    stations: Sequence[MetroStation],
) -> MetroStation | None:
    """
    Находит ближайшую к (lat, lon) станцию метро из списка.

    Работает только для станций, у которых заполнены поля lat/lon.
    Если ни одна станция не имеет координат — возвращает None.

    Координаты метро можно заполнить двумя способами:
    - Вручную в seed.py (статичные данные для Москвы).
    - Через Google Sheets «Метро» (отдельный лист с lat/lon).
    """
    best: MetroStation | None = None
    best_dist = float("inf")
    for st in stations:
        if st.lat is None or st.lon is None:
            continue
        dist = haversine_km(lat, lon, st.lat, st.lon)
        if dist < best_dist:
            best_dist = dist
            best = st
    return best


# ══════════════════════════════════════════════════════════════
# Контекст запроса
# ══════════════════════════════════════════════════════════════


@dataclass
class RankingContext:
    """
    Параметры запроса пользователя.

    Формируется из данных FSM-состояния перед показом результатов.
    """

    service_type: str
    """'repair' | 'upgrade' | 'complex'"""

    malfunction_category: str | None = None
    """'Механика' | 'Электрика' | None — для ремонта"""

    user_metro: str | None = None
    """Название ближайшей к пользователю станции метро"""

    user_lat: float | None = None
    user_lon: float | None = None
    """GPS-координаты пользователя (опционально, для будущей геострategии)"""


# ══════════════════════════════════════════════════════════════
# Результат ранжирования
# ══════════════════════════════════════════════════════════════


@dataclass
class ServiceMatch:
    """Сервис-центр с объяснением его скора."""

    service: Service
    score: float  # итоговый скор 0.0 – 1.0
    proximity_score: float
    rating_score: float


# ══════════════════════════════════════════════════════════════
# Protocol: стратегия близости
# ══════════════════════════════════════════════════════════════


@runtime_checkable
class ProximityStrategy(Protocol):
    """
    Интерфейс стратегии близости.

    Реализуйте этот Protocol чтобы подключить любой источник данных
    (метро, геокодинг, 2GIS и т.д.) без изменения основной логики.
    """

    async def setup(self, session: AsyncSession) -> None:
        """Инициализация (загрузка данных из БД / API)."""
        ...

    def score(self, service: Service, ctx: RankingContext) -> float:
        """Вернуть скор близости от 0.0 до 1.0."""
        ...


# ══════════════════════════════════════════════════════════════
# Стратегия 1: По метро (default, без внешних API)
# ══════════════════════════════════════════════════════════════


class MetroProximityStrategy:
    """
    Сравнивает nearest_metro сервиса с user_metro из контекста.

    Логика (transfer-graph, BFS):
    - dist=0 (та же станция или одинаковое название на разных линиях) → 1.00
    - dist=1 (прямой переход)                                          → 0.85
    - dist=2 (через один хаб)                                          → 0.65
    - dist=3 (через два хаба)                                          → 0.45
    - dist≥4 или нет пути                                              → 0.25
    - нет данных о метро                                               → 0.50
    """

    def __init__(self) -> None:
        self._stations: list[MetroStation] = []

    async def setup(self, session: AsyncSession) -> None:
        self._stations = (await session.execute(select(MetroStation))).scalars().all()

    def score(self, service: Service, ctx: RankingContext) -> float:
        svc_metro = service.nearest_metro
        user_metro = ctx.user_metro

        # Нет данных — нейтральный скор
        if not svc_metro or not user_metro:
            return SCORE_METRO_UNKNOWN

        dist = metro_transfer_distance(svc_metro, user_metro)
        if dist is None:
            return SCORE_METRO_FAR
        return _DIST_SCORES.get(dist, SCORE_METRO_FAR)


# ══════════════════════════════════════════════════════════════
# Стратегия 2: Заготовка для геокодирования (TODO)
# ══════════════════════════════════════════════════════════════


class GeocodingProximityStrategy:
    """
    TODO: Реализовать геокодирование через Yandex Maps / 2GIS API.

    Алгоритм:
    1. setup(): загрузить все сервисы с адресами, геокодировать через API,
       закешировать координаты в памяти (или в новой таблице service_coords).
    2. score(): если у ctx есть user_lat/user_lon, вычислить haversine-расстояние
       до каждого сервиса и нормализовать скор (например, <1 км → 1.0, >10 км → 0.1).

    При подключении:
        results = await rank_services(ctx, session,
                                      proximity=GeocodingProximityStrategy(api_key="..."))
    """

    async def setup(self, session: AsyncSession) -> None:
        raise NotImplementedError("GeocodingProximityStrategy не реализована")

    def score(self, service: Service, ctx: RankingContext) -> float:
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════
# Основная функция ранжирования
# ══════════════════════════════════════════════════════════════


async def rank_services(
    ctx: RankingContext,
    session: AsyncSession,
    *,
    proximity: ProximityStrategy | None = None,
    limit: int = 10,
) -> list[ServiceMatch]:
    """
    Отфильтровать и отранжировать сервисы под запрос пользователя.

    Args:
        ctx:       Контекст запроса (тип, категория, метро и т.д.)
        session:   Активная SQLAlchemy async session.
        proximity: Стратегия близости. По умолчанию — MetroProximityStrategy.
        limit:     Максимальное число результатов.

    Returns:
        Список ServiceMatch, отсортированный по убыванию скора.
    """
    # Инициализируем стратегию близости
    if proximity is None:
        proximity = MetroProximityStrategy()
    await proximity.setup(session)

    # ── Запрос к БД ───────────────────────────────────────────
    # "complex" подходит и для ремонта, и для апгрейда
    if ctx.service_type == "repair":
        allowed_types = ("repair", "complex")
    elif ctx.service_type == "upgrade":
        allowed_types = ("upgrade", "complex")
    else:
        allowed_types = ("repair", "upgrade", "complex")

    stmt = (
        select(Service)
        .where(Service.service_type.in_(allowed_types))
        .where(Service.is_available.is_(True))
    )

    # Фильтр по категории неисправности (только для ремонта)
    if ctx.malfunction_category:
        stmt = stmt.join(Service.category_rel).where(
            ServiceCategory.name == ctx.malfunction_category
        )

    services = (await session.execute(stmt)).scalars().all()

    if not services:
        logger.debug(
            "rank_services: нет сервисов для type=%s category=%s",
            ctx.service_type,
            ctx.malfunction_category,
        )
        return []

    # ── Ранжирование ──────────────────────────────────────────
    results: list[ServiceMatch] = []
    for svc in services:
        prox = proximity.score(svc, ctx)

        # Нормализуем рейтинг Я.Карт в диапазон 0..1
        if svc.yandex_rating is not None:
            rating = min(svc.yandex_rating, MAX_YANDEX_RATING) / MAX_YANDEX_RATING
        else:
            rating = 0.5  # нейтрально при отсутствии данных

        total = WEIGHT_PROXIMITY * prox + WEIGHT_RATING * rating

        results.append(
            ServiceMatch(
                service=svc,
                score=round(total, 4),
                proximity_score=prox,
                rating_score=rating,
            )
        )

    results.sort(key=lambda m: m.score, reverse=True)
    return results[:limit]
