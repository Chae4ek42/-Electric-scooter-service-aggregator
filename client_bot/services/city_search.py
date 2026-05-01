from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

_FALLBACK_CITIES: tuple[str, ...] = (
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Казань",
    "Нижний Новгород",
    "Челябинск",
    "Самара",
    "Омск",
    "Ростов-на-Дону",
    "Уфа",
    "Красноярск",
    "Пермь",
    "Воронеж",
    "Волгоград",
    "Краснодар",
    "Саратов",
    "Тюмень",
    "Тольятти",
    "Ижевск",
    "Барнаул",
    "Ульяновск",
    "Иркутск",
    "Хабаровск",
    "Ярославль",
    "Владивосток",
    "Махачкала",
    "Томск",
    "Оренбург",
    "Кемерово",
    "Новокузнецк",
    "Рязань",
    "Астрахань",
    "Пенза",
    "Липецк",
    "Киров",
    "Чебоксары",
    "Калининград",
    "Тула",
    "Курск",
    "Ставрополь",
    "Улан-Удэ",
    "Сочи",
    "Тверь",
    "Иваново",
    "Брянск",
    "Белгород",
    "Сургут",
    "Владимир",
    "Чита",
    "Набережные Челны",
    "Архангельск",
    "Симферополь",
    "Севастополь",
    "Калуга",
    "Смоленск",
    "Курган",
    "Орёл",
    "Вологда",
    "Саранск",
    "Череповец",
    "Владикавказ",
    "Мурманск",
    "Якутск",
    "Грозный",
    "Новороссийск",
    "Йошкар-Ола",
    "Нижневартовск",
    "Петрозаводск",
    "Псков",
    "Сыктывкар",
    "Нальчик",
    "Кострома",
    "Нижнекамск",
    "Благовещенск",
    "Комсомольск-на-Амуре",
    "Таганрог",
    "Миасс",
    "Люберцы",
    "Балашиха",
    "Подольск",
    "Королёв",
    "Химки",
)

_DATA_FILE = Path(__file__).resolve().parent / "data" / "russian_cities.txt"


def _load_base_cities() -> tuple[str, ...]:
    try:
        lines = _DATA_FILE.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return _FALLBACK_CITIES

    cities: list[str] = []
    seen: set[str] = set()
    for line in lines:
        city = line.strip()
        if not city or city in seen:
            continue
        seen.add(city)
        cities.append(city)

    return tuple(cities) if cities else _FALLBACK_CITIES


_BASE_CITIES: tuple[str, ...] = _load_base_cities()

_SYNONYMS: dict[str, str] = {
    "мск": "Москва",
    "москоу": "Москва",
    "moscow": "Москва",
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "saint petersburg": "Санкт-Петербург",
    "екб": "Екатеринбург",
    "ростов": "Ростов-на-Дону",
    "нижний": "Нижний Новгород",
}


def normalize_city_name(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip().lower().replace("ё", "е")
    for prefix in ("г.", "город ", "гор."):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    cleaned = " ".join(cleaned.split())
    return cleaned


def canonical_city_name(value: str) -> str:
    normalized = normalize_city_name(value)
    if not normalized:
        return ""
    if normalized in _SYNONYMS:
        return _SYNONYMS[normalized]
    for city in _BASE_CITIES:
        if normalize_city_name(city) == normalized:
            return city
    return value.strip()


def is_moscow_city(value: str | None) -> bool:
    normalized = normalize_city_name(value)
    return normalized in {"москва", "moscow", "мск", "москоу"}


def city_candidates(extra_cities: Iterable[str] | None = None) -> list[str]:
    merged: dict[str, str] = {}
    for city in _BASE_CITIES:
        merged[normalize_city_name(city)] = city
    if extra_cities:
        for city in extra_cities:
            normalized = normalize_city_name(city)
            if normalized:
                merged.setdefault(normalized, canonical_city_name(city))
    return sorted(merged.values(), key=lambda c: c.lower())


def _score(query: str, candidate: str) -> float:
    q = normalize_city_name(query)
    c = normalize_city_name(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if c.startswith(q) or q in c:
        return 0.95
    return SequenceMatcher(a=q, b=c).ratio()


def top_city_matches(
    query: str,
    cities: Iterable[str] | None = None,
    *,
    limit: int = 5,
    threshold: float = 0.45,
) -> list[tuple[str, float]]:
    source = list(cities) if cities is not None else list(_BASE_CITIES)
    ranked: list[tuple[str, float]] = []
    for city in source:
        similarity = _score(query, city)
        if similarity >= threshold:
            ranked.append((city, similarity))

    ranked.sort(key=lambda item: item[1], reverse=True)

    deduped: list[tuple[str, float]] = []
    seen: set[str] = set()
    for city, score in ranked:
        key = normalize_city_name(city)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((canonical_city_name(city), score))
        if len(deduped) >= limit:
            break
    return deduped


def best_city_match(
    query: str,
    cities: Iterable[str] | None = None,
    *,
    threshold: float = 0.6,
) -> str | None:
    matches = top_city_matches(query, cities, limit=1, threshold=threshold)
    if not matches:
        return None
    return matches[0][0]
