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

import datetime
import logging
import math
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from client_bot.domain.models import MetroStation, Service, ServiceCategory
from client_bot.services.metro_graph import metro_transfer_distance

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

    scheduled_date: str | None = None
    """Выбранная пользователем дата (DD.MM.YYYY) — для фильтрации по рабочим дням"""

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
    suggested_date: str | None = None
    suggested_time: str | None = None


_RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_FALLBACK_SEARCH_DAYS = 21


def _parse_minutes(time_str: str | None) -> int | None:
    if not time_str:
        return None
    try:
        hours, minutes = time_str.split(":", 1)
        h = int(hours)
        m = int(minutes)
    except (ValueError, TypeError):
        return None
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return h * 60 + m


def _parse_ru_date(date_str: str | None) -> datetime.date | None:
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        return None


def _fmt_minutes(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _svc_covers_date(svc: Service, date_str: str | None) -> bool:
    """Проверяет, работает ли сервис в указанный день недели."""
    if not date_str or not svc.working_days:
        return True
    day = _parse_ru_date(date_str)
    if day is None:
        return True
    allowed = {token.strip() for token in svc.working_days.split(",") if token.strip()}
    if not allowed:
        return True
    return _RU_WEEKDAYS[day.weekday()] in allowed


def _svc_covers_slot(svc: Service, date_str: str | None, time_str: str | None) -> bool:
    return _svc_covers_date(svc, date_str) and _svc_covers_time(svc, time_str)


def _svc_covers_time(svc: Service, time_str: str | None) -> bool:
    """Проверяет, работает ли сервис в указанное время."""
    if not time_str or not svc.open_time or not svc.close_time:
        return True
    t = _parse_minutes(time_str)
    o = _parse_minutes(svc.open_time)
    c = _parse_minutes(svc.close_time)
    if t is None or o is None or c is None:
        return True
    return o <= t < c


def _find_nearest_valid_time(svc: Service, original_time: str) -> str | None:
    """Найти ближайший к original_time слот внутри часов работы сервиса."""
    if not svc.open_time or not svc.close_time:
        return None
    orig_mins = _parse_minutes(original_time)
    open_mins = _parse_minutes(svc.open_time)
    close_mins = _parse_minutes(svc.close_time)
    if orig_mins is None or open_mins is None or close_mins is None:
        return None
    if orig_mins < open_mins:
        best = open_mins
    elif orig_mins >= close_mins:
        best = close_mins - 60
    else:
        return None
    if best < open_mins:
        return None
    return _fmt_minutes(best)


def _find_nearest_valid_slot(
    svc: Service,
    original_date: str | None,
    original_time: str | None,
) -> tuple[str, str] | None:
    """Найти ближайший доступный слот дата+время, учитывая рабочие дни и часы."""
    base_date = _parse_ru_date(original_date)
    if base_date is None:
        return None

    requested_mins = _parse_minutes(original_time)
    open_mins = _parse_minutes(svc.open_time)
    close_mins = _parse_minutes(svc.close_time)

    for day_offset in range(_FALLBACK_SEARCH_DAYS + 1):
        day = base_date + datetime.timedelta(days=day_offset)
        day_str = day.strftime("%d.%m.%Y")
        if not _svc_covers_date(svc, day_str):
            continue

        if open_mins is None or close_mins is None:
            if requested_mins is None:
                continue
            return day_str, _fmt_minutes(requested_mins)

        latest_start = close_mins - 60
        if latest_start < open_mins:
            continue

        if day_offset == 0 and requested_mins is not None:
            if requested_mins < open_mins:
                candidate = open_mins
            elif requested_mins >= close_mins:
                candidate = latest_start
            else:
                candidate = requested_mins
        else:
            candidate = open_mins

        candidate = max(open_mins, min(candidate, latest_start))
        return day_str, _fmt_minutes(candidate)

    return None


def _slot_distance_minutes(
    base_date: str | None,
    base_time: str | None,
    candidate_date: str,
    candidate_time: str,
) -> float:
    base_d = _parse_ru_date(base_date)
    base_t = _parse_minutes(base_time)
    cand_d = _parse_ru_date(candidate_date)
    cand_t = _parse_minutes(candidate_time)
    if base_d is None or base_t is None or cand_d is None or cand_t is None:
        return float("inf")
    base_dt = datetime.datetime.combine(
        base_d, datetime.time(base_t // 60, base_t % 60)
    )
    cand_dt = datetime.datetime.combine(
        cand_d, datetime.time(cand_t // 60, cand_t % 60)
    )
    return abs((cand_dt - base_dt).total_seconds())


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
            .where(Service.registration_complete.is_(True))
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
            .where(Service.registration_complete.is_(True))
        )

        if ctx.malfunction_category:
            # Сервисы с "Электрика + механика" подходят для любой из двух категорий
            stmt = stmt.join(Service.category_rel).where(
                (ServiceCategory.name == ctx.malfunction_category)
                | (ServiceCategory.name == "Электрика + механика")
            )

    services = (await session.execute(stmt)).scalars().all()

    if not services:
        return RankingResult(matches=[])

    # Фильтрация по рабочему дню и времени
    slot_compatible = [
        s
        for s in services
        if _svc_covers_slot(s, ctx.scheduled_date, ctx.scheduled_time)
    ]

    if not slot_compatible:
        suggested_date: str | None = None
        suggested_time: str | None = None

        if ctx.scheduled_date:
            candidates: list[tuple[float, float, str, str]] = []
            for svc in services:
                slot = _find_nearest_valid_slot(
                    svc,
                    ctx.scheduled_date,
                    ctx.scheduled_time,
                )
                if not slot:
                    continue
                date_part, time_part = slot
                distance = _slot_distance_minutes(
                    ctx.scheduled_date,
                    ctx.scheduled_time,
                    date_part,
                    time_part,
                )
                rating_tiebreak = -(svc.yandex_rating or 0.0)
                candidates.append((distance, rating_tiebreak, date_part, time_part))

            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                suggested_date = candidates[0][2]
                suggested_time = candidates[0][3]
        elif ctx.scheduled_time:
            for svc in sorted(
                services,
                key=lambda s: s.yandex_rating or 0,
                reverse=True,
            ):
                nearest = _find_nearest_valid_time(svc, ctx.scheduled_time)
                if nearest:
                    suggested_time = nearest
                    break

        return RankingResult(
            matches=[],
            time_fallback=True,
            suggested_date=suggested_date,
            suggested_time=suggested_time,
        )

    target_services = slot_compatible

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
    return RankingResult(matches=results[:limit])
