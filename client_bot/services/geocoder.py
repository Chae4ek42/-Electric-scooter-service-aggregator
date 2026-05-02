from __future__ import annotations

import asyncio
import logging
import re

import aiohttp

from client_bot.core.config import YANDEX_GEOCODER_API_KEY
from client_bot.services.city_search import is_moscow_city

logger = logging.getLogger(__name__)

_GEOCODER_URL = "https://geocode-maps.yandex.ru/1.x/"
_GEOCODE_CACHE: dict[str, tuple[float, float]] = {}
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

_ADDRESS_TAIL_RE = re.compile(
    r"(?:,\s*)?(?:кв(?:артира)?|офис|оф\.|подъезд|эт(?:аж)?|помещение)\b.*$",
    re.IGNORECASE,
)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        clean = raw.strip(" ,")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _address_variants(address: str) -> list[str]:
    normalized = _normalize_space(address)
    without_tail = _ADDRESS_TAIL_RE.sub("", normalized).strip(" ,")
    return _dedupe_keep_order([normalized, without_tail])


def build_geocode_queries(
    city: str,
    address: str,
    metro: str | None = None,
) -> list[str]:
    city_clean = _normalize_space(city)
    address_clean = _normalize_space(address)
    metro_clean = _normalize_space(metro) if metro else ""
    if not city_clean or not address_clean:
        return []

    use_metro = metro_clean if metro_clean and is_moscow_city(city_clean) else ""

    queries: list[str] = []
    for addr in _address_variants(address_clean):
        base_parts = [city_clean, addr]
        if use_metro:
            queries.append(", ".join([*base_parts, f"метро {use_metro}"]))
        queries.append(", ".join(base_parts))
        queries.append(", ".join(["Россия", *base_parts]))

    return _dedupe_keep_order(queries)


def build_geocode_query(city: str, address: str, metro: str | None = None) -> str:
    queries = build_geocode_queries(city, address, metro)
    return queries[0] if queries else ""


def _extract_coords(payload: dict) -> tuple[float, float] | None:
    try:
        members = payload["response"]["GeoObjectCollection"]["featureMember"]
        if not members:
            return None
        pos_raw = members[0]["GeoObject"]["Point"]["pos"]
        lon_text, lat_text = pos_raw.split(" ")
        return float(lat_text), float(lon_text)
    except Exception:
        return None


async def geocode_address(query: str) -> tuple[float, float] | None:
    clean_query = query.strip()
    if not clean_query:
        return None
    if clean_query in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean_query]

    api_key = (YANDEX_GEOCODER_API_KEY or "").strip()
    if not api_key:
        logger.warning("YANDEX_GEOCODER_API_KEY is not set")
        return None

    params = {
        "apikey": api_key,
        "geocode": clean_query,
        "format": "json",
        "results": 1,
        "lang": "ru_RU",
    }

    timeout = aiohttp.ClientTimeout(total=8)
    retries = 2

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(retries + 1):
            try:
                async with session.get(_GEOCODER_URL, params=params) as response:
                    if response.status != 200:
                        if response.status in _RETRYABLE_STATUSES and attempt < retries:
                            await asyncio.sleep(0.35 * (attempt + 1))
                            continue
                        logger.warning(
                            "Geocoder request failed: status=%s query=%r",
                            response.status,
                            clean_query,
                        )
                        return None
                    payload = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < retries:
                    logger.warning(
                        "Geocoder transient error: query=%r attempt=%d error=%s",
                        clean_query,
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(0.35 * (attempt + 1))
                    continue
                logger.warning(
                    "Geocoder request error after retries: query=%r error=%s",
                    clean_query,
                    exc,
                )
                return None
            except Exception:
                logger.exception("Geocoder request error for query=%r", clean_query)
                return None

            result = _extract_coords(payload)
            if result is None:
                logger.info(
                    "Geocoder returned no coordinates for query=%r", clean_query
                )
                return None

            _GEOCODE_CACHE[clean_query] = result
            return result

    return None


async def geocode_with_fallback(
    city: str,
    address: str,
    metro: str | None = None,
) -> tuple[float, float] | None:
    queries = build_geocode_queries(city, address, metro)
    for index, query in enumerate(queries, start=1):
        coords = await geocode_address(query)
        if coords is not None:
            if index > 1:
                logger.info("Geocoder fallback succeeded on variant=%d", index)
            return coords

    return None
