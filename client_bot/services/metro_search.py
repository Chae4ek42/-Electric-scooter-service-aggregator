"""Fuzzy-match utility for Moscow metro station names.

Uses difflib.SequenceMatcher (no extra deps) to find the best match.
"""

from __future__ import annotations

import difflib
from typing import Sequence

from client_bot.domain.models import MetroStation


def best_metro_match(
    query: str,
    stations: Sequence[MetroStation],
    threshold: float = 0.45,
) -> MetroStation | None:
    """Return the station with the highest similarity to *query*, or None."""
    query_lower = query.strip().lower()
    if not query_lower:
        return None

    best_score = 0.0
    best_station: MetroStation | None = None

    for station in stations:
        name_lower = station.name.lower()
        if query_lower in name_lower:
            score = 0.85 + 0.15 * (len(query_lower) / len(name_lower))
        else:
            score = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()

        if score > best_score:
            best_score = score
            best_station = station

    if best_score >= threshold:
        return best_station
    return None


def top_metro_matches(
    query: str,
    stations: Sequence[MetroStation],
    limit: int = 5,
    threshold: float = 0.35,
) -> list[tuple[MetroStation, float]]:
    """Return top *limit* stations sorted by similarity score."""
    query_lower = query.strip().lower()
    if not query_lower:
        return []

    scored: list[tuple[MetroStation, float]] = []
    for station in stations:
        name_lower = station.name.lower()
        if query_lower in name_lower:
            score = 0.85 + 0.15 * (len(query_lower) / len(name_lower))
        else:
            score = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
        if score >= threshold:
            scored.append((station, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
