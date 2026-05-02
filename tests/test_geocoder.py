from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.services import geocoder


def test_build_geocode_queries_for_moscow_include_without_metro() -> None:
    queries = geocoder.build_geocode_queries("Москва", "Тверская, 7", "Тверская")

    assert queries
    assert queries[0] == "Москва, Тверская, 7, метро Тверская"
    assert "Москва, Тверская, 7" in queries


@pytest.mark.asyncio
async def test_geocode_with_fallback_uses_next_query_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _fake_geocode(query: str) -> tuple[float, float] | None:
        calls.append(query)
        if query.startswith("Россия,"):
            return (55.75, 37.62)
        return None

    monkeypatch.setattr(geocoder, "geocode_address", _fake_geocode)

    coords = await geocoder.geocode_with_fallback(
        city="Казань",
        address="Баумана, 12",
    )

    assert coords == (55.75, 37.62)
    assert calls[:2] == [
        "Казань, Баумана, 12",
        "Россия, Казань, Баумана, 12",
    ]


@pytest.mark.asyncio
async def test_geocode_address_without_api_key_is_not_negative_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "Самара, Ленинградская, 1"
    geocoder._GEOCODE_CACHE.clear()
    monkeypatch.setattr(geocoder, "YANDEX_GEOCODER_API_KEY", "")

    coords = await geocoder.geocode_address(query)

    assert coords is None
    assert query not in geocoder._GEOCODE_CACHE
