# Модуль: Ранжирование сервисов (`bot/services/ranking.py`)

## Зачем это нужно

Пользователь заполняет заявку: указывает тип работ (ремонт / апгрейд), категорию неисправности (Механика / Электрика) и ближайшую к нему станцию метро (или отправляет геолокацию). На основе этого бот должен предложить подходящие сервис-центры из базы данных, отсортированные по релевантности.

---

## Архитектура

```
rank_services(ctx, session, proximity=..., limit=10)
        │
        ├── 1. Фильтрация в БД
        │       по service_type (repair / upgrade / complex)
        │       по ServiceCategory (Механика / Электрика)
        │
        ├── 2. Скоринг каждого результата
        │       ProximityStrategy.score()   → proximity_score (0..1)
        │       yandex_rating / 5.0         → rating_score    (0..1)
        │
        ├── 3. Итоговый скор
        │       score = WEIGHT_PROXIMITY * proximity + WEIGHT_RATING * rating
        │
        └── 4. Сортировка по убыванию скора → первые `limit` результатов
```

### Входные данные: `RankingContext`

| Поле | Тип | Описание |
|------|-----|----------|
| `service_type` | `str` | `'repair'` / `'upgrade'` / `'complex'` |
| `malfunction_category` | `str \| None` | `'Механика'` / `'Электрика'` / `None` |
| `user_metro` | `str \| None` | Название станции метро пользователя |
| `user_lat` / `user_lon` | `float \| None` | GPS (опционально, для будущей геостратегии) |

**Пример формирования из FSM-данных в хендлере:**

```python
from bot.services.ranking import RankingContext, rank_services

data = await state.get_data()
ctx = RankingContext(
    service_type=data["service_type"],
    malfunction_category=data.get("malfunction_category"),
    user_metro=data.get("metro_station"),
    user_lat=data.get("user_lat"),
    user_lon=data.get("user_lon"),
)
async with async_session() as session:
    matches = await rank_services(ctx, session, limit=5)
```

### Выходные данные: `list[ServiceMatch]`

```python
@dataclass
class ServiceMatch:
    service: Service        # ORM-объект сервиса
    score: float            # итоговый скор 0.0 – 1.0
    proximity_score: float  # вклад близости
    rating_score: float     # вклад рейтинга
```

---

## Логика фильтрации по типу

| Запрос пользователя | Допустимые `service_type` в БД |
|---------------------|-------------------------------|
| `repair`            | `repair`, `complex`           |
| `upgrade`           | `upgrade`, `complex`          |
| `complex`           | `repair`, `upgrade`, `complex`|

Сервисы со специализацией `complex` подходят и для ремонта, и для апгрейда.

---

## Стратегии близости (`ProximityStrategy`)

Стратегия — объект, реализующий два метода:

```python
async def setup(self, session: AsyncSession) -> None: ...
def score(self, service: Service, ctx: RankingContext) -> float: ...
```

Это [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol) — не нужно наследоваться, достаточно реализовать методы.

### Текущая: `MetroProximityStrategy` (без внешних API)

Загружает все станции метро из таблицы `metro_stations` и строит словарь `{название станции → множество линий}`.

| Случай | Скор |
|--------|------|
| `nearest_metro` сервиса == `user_metro` пользователя | **1.0** |
| Разные станции, но одна линия | **0.7** |
| Разные линии | **0.3** |
| Нет данных о метро сервиса | **0.5** (нейтрально) |

Константы (`SCORE_METRO_EXACT`, `SCORE_METRO_LINE` и т.д.) настраиваются в начале файла.

### Будущая: `GeocodingProximityStrategy`

Заготовка уже есть в коде. Алгоритм, когда будет реализована:

1. `setup()` — загрузить адреса всех сервисов, геокодировать через Yandex Maps API, сохранить координаты в кэш (или в таблицу `service_coords`)
2. `score()` — если у пользователя есть `user_lat`/`user_lon` (он отправил геолокацию), вычислить [haversine-расстояние](https://en.wikipedia.org/wiki/Haversine_formula) и нормализовать в `0..1` (например, `<1 км → 1.0`, `>10 км → 0.1`)

**Как подключить:**

```python
matches = await rank_services(
    ctx, session,
    proximity=GeocodingProximityStrategy(api_key="ВАШ_КЛЮЧ"),
)
```

Основная логика ранжирования **не меняется**.

---

## Настройка весов

В файле `ranking.py` в самом начале:

```python
WEIGHT_PROXIMITY: float = 0.6   # 60% — близость к пользователю
WEIGHT_RATING:    float = 0.4   # 40% — рейтинг Яндекс.Карт
```

Сумма весов должна равняться 1.0. Веса можно сделать настраиваемыми через `.env` или Google Sheets «Настройки», если потребуется A/B-тестирование.

---

## Данные о сервисах в БД

Эти поля таблицы `services` используются при ранжировании:

| Поле | Откуда | Назначение |
|------|--------|------------|
| `service_type` | Sheets «Специализация» | Фильтрация по типу |
| `category_id` | Sheets «Категория» | Фильтрация по группе неисправностей |
| `nearest_metro` | Sheets «Метро ближ.» | Metro-близость |
| `yandex_rating` | Sheets «Рейтинг Я.Карт» | Rating-скор |
| `address` | Sheets «Адрес» | Отображается пользователю в карточке |

---

## Как добавить новый критерий ранжирования

1. Добавить поле в модель `Service` (`domain/models.py`) и синхронизацию в `sheets_sync.py`
2. Добавить миграцию в `seed.py` (`init_db`)
3. Расширить `score()` в нужной стратегии **или** создать новый `WEIGHT_*` / компонент скора
4. Ничего в хендлерах менять не нужно
