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
    """Параметры запроса пользователя."""

    service_type: str
    """'repair' | 'upgrade' | 'complex'"""

    malfunction_category: str | None = None
    """'Механика' | 'Электрика' | None — для ремонта"""

    upgrade_category: str | None = None
    """'Гидроизоляция' | 'Окраска' | 'Прошивка' | 'Изменение конструкции' | None"""

    user_metro: str | None = None
    """Название ближайшей к пользователю станции метро"""

    scheduled_time: str | None = None
    """Выбранное пользователем время (HH:MM) — для фильтрации по часам работы"""

    user_lat: float | None = None
    user_lon: float | None = None


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


@dataclass
class RankingResult:
    """Результат ранжирования с информацией о fallback."""

    matches: list[ServiceMatch]
    time_fallback: bool = False
    suggested_time: str | None = None


def _svc_covers_time(svc: Service, time_str: str | None) -> bool:
    """Проверяет, работает ли сервис в указанное время."""
    if not time_str or not svc.open_time or not svc.close_time:
        return True
    try:
        t = int(time_str.split(":")[0]) * 60 + int(time_str.split(":")[1])
        o = int(svc.open_time.split(":")[0]) * 60 + int(svc.open_time.split(":")[1])
        c = int(svc.close_time.split(":")[0]) * 60 + int(svc.close_time.split(":")[1])
        return o <= t < c
    except (ValueError, IndexError):
        return True


def _find_nearest_valid_time(svc: Service, original_time: str) -> str | None:
    """Найти ближайший к original_time слот внутри часов работы сервиса."""
    if not svc.open_time or not svc.close_time:
        return None
    try:
        orig_mins = int(original_time.split(":")[0]) * 60 + int(
            original_time.split(":")[1]
        )
        open_mins = int(svc.open_time.split(":")[0]) * 60 + int(
            svc.open_time.split(":")[1]
        )
        close_mins = int(svc.close_time.split(":")[0]) * 60 + int(
            svc.close_time.split(":")[1]
        )
        if orig_mins < open_mins:
            best = open_mins
        elif orig_mins >= close_mins:
            best = close_mins - 60
        else:
            return None
        if best < open_mins:
            return None
        return f"{best // 60:02d}:{best % 60:02d}"
    except (ValueError, IndexError):
        return None


async def rank_services(
    ctx: RankingContext,
    session: AsyncSession,
    *,
    proximity: ProximityStrategy | None = None,
    limit: int = 10,
) -> RankingResult:
    """
    Отфильтровать и отранжировать сервисы.

    Возвращает RankingResult с matches и информацией о time_fallback.
    """
    if proximity is None:
        proximity = MetroProximityStrategy()
    await proximity.setup(session)

    # Гидроизоляция — особый случай: ищем по has_hydroisolation
    if ctx.upgrade_category == "Гидроизоляция":
        stmt = (
            select(Service)
            .where(Service.is_available.is_(True))
            .where(Service.has_hydroisolation.is_(True))
        )
    else:
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

        if ctx.malfunction_category:
            stmt = stmt.join(Service.category_rel).where(
                ServiceCategory.name == ctx.malfunction_category
            )

    services = (await session.execute(stmt)).scalars().all()

    if not services:
        return RankingResult(matches=[])

    # Фильтрация по времени работы
    time_compatible = [s for s in services if _svc_covers_time(s, ctx.scheduled_time)]

    time_fallback = False
    suggested_time: str | None = None

    if time_compatible:
        target_services = time_compatible
    else:
        # Все сервисы не подходят по времени — fallback
        target_services = services
        time_fallback = True
        # Найти ближайшее подходящее время у лучшего сервиса
        if ctx.scheduled_time:
            for svc in sorted(
                services,
                key=lambda s: s.yandex_rating or 0,
                reverse=True,
            ):
                t = _find_nearest_valid_time(svc, ctx.scheduled_time)
                if t:
                    suggested_time = t
                    break

    # Ранжирование
    results: list[ServiceMatch] = []
    for svc in target_services:
        prox = proximity.score(svc, ctx)
        if svc.yandex_rating is not None:
            rating = min(svc.yandex_rating, MAX_YANDEX_RATING) / MAX_YANDEX_RATING
        else:
            rating = 0.5

        total = WEIGHT_PROXIMITY * prox + WEIGHT_RATING * rating
        results.append(
            ServiceMatch(
                service=svc,
                score=round(total, 4),
                proximity_score=prox,
                rating_score=rating,
            )
        )

    # Сортировка: при равном скоре — по рейтингу Яндекс Карт
    results.sort(key=lambda m: (m.score, m.service.yandex_rating or 0), reverse=True)
    return RankingResult(
        matches=results[:limit],
        time_fallback=time_fallback,
        suggested_time=suggested_time,
    )
