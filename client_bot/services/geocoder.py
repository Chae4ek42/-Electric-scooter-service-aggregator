from __future__ import annotations

import logging

import aiohttp

from client_bot.core.config import YANDEX_GEOCODER_API_KEY
from client_bot.services.city_search import is_moscow_city

logger = logging.getLogger(__name__)

_GEOCODER_URL = "https://geocode-maps.yandex.ru/1.x/"
_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}


def build_geocode_query(city: str, address: str, metro: str | None = None) -> str:
    parts: list[str] = [city.strip(), address.strip()]
    if metro and is_moscow_city(city):
        parts.append(f"метро {metro.strip()}")
    return ", ".join(part for part in parts if part)


async def geocode_address(query: str) -> tuple[float, float] | None:
    clean_query = query.strip()
    if not clean_query:
        return None
    if clean_query in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean_query]

    api_key = (YANDEX_GEOCODER_API_KEY or "").strip()
    if not api_key:
        logger.warning("YANDEX_GEOCODER_API_KEY is not set")
        _GEOCODE_CACHE[clean_query] = None
        return None

    params = {
        "apikey": api_key,
        "geocode": clean_query,
        "format": "json",
        "results": 1,
        "lang": "ru_RU",
    }

    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_GEOCODER_URL, params=params) as response:
                if response.status != 200:
                    logger.warning(
                        "Geocoder request failed: status=%s query=%r",
                        response.status,
                        clean_query,
                    )
                    _GEOCODE_CACHE[clean_query] = None
                    return None
                payload = await response.json(content_type=None)
    except Exception:
        logger.exception("Geocoder request error for query=%r", clean_query)
        _GEOCODE_CACHE[clean_query] = None
        return None

    try:
        members = payload["response"]["GeoObjectCollection"]["featureMember"]
        if not members:
            _GEOCODE_CACHE[clean_query] = None
            return None
        pos_raw = members[0]["GeoObject"]["Point"]["pos"]
        lon_text, lat_text = pos_raw.split(" ")
        result = (float(lat_text), float(lon_text))
    except Exception:
        logger.warning("Failed to parse geocoder response for query=%r", clean_query)
        _GEOCODE_CACHE[clean_query] = None
        return None

    _GEOCODE_CACHE[clean_query] = result
    return result
