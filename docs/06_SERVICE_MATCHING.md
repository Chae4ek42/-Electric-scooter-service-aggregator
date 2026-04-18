# Модуль: Логика подбора сервис-центров

Файл: `bot/services/ranking.py`

Точка входа: `rank_services(ctx, session, limit=1)` — вызывается из хендлера `pick_time` после выбора даты и времени.

---

## Входные данные

```python
@dataclass
class RankingContext:
    service_type: str           # 'repair' | 'upgrade'
    malfunction_category: str | None   # 'Механика' | 'Электрика' (repair only)
    upgrade_category: str | None       # 'Гидроизоляция' | ... (upgrade only)
    user_metro: str | None             # станция, выбранная при оформлении
    scheduled_time: str | None         # HH:MM — для фильтрации по часам работы
```

---

## Алгоритм

### Шаг 1: Фильтрация из БД

Все сервисы проходят базовый фильтр `is_available = True` и `(telegram_id IS NULL OR status = 'активный')` (незаконченные анкеты партнёров исключаются).

**Далее — по типу запроса:**

| Запрос | Условие SQL |
|---|---|
| Ремонт | `service_type IN ('repair', 'complex')` |
| Апгрейд | `service_type IN ('upgrade', 'complex')` |
| Апгрейд → Гидроизоляция | `has_hydroisolation = True` (любой `service_type`) |

**Фильтрация по категории** (только для ремонта):
- Если `malfunction_category` задана (Механика / Электрика), применяется JOIN по `ServiceCategory`:
  ```sql
  JOIN service_categories ON services.category_id = service_categories.id
  WHERE service_categories.name = :malfunction_category
  ```

### Шаг 2: Фильтрация по времени работы

Каждый сервис имеет `open_time` и `close_time` (формат `HH:MM`).

```
_svc_covers_time(svc, scheduled_time):
    если open_time ≤ scheduled_time < close_time → OK
    иначе → не подходит по времени
```

- Если хотя бы один сервис подходит по времени → используем только такие сервисы.
- Если **ни один** сервис не подходит по времени → `time_fallback = True`.

### Шаг 3: Time fallback

Когда `time_fallback = True`:

1. Берём все доступные сервисы (без фильтра по времени).
2. Сортируем по `yandex_rating` убыванию.
3. Для лучшего сервиса ищем ближайшее допустимое время: `_find_nearest_valid_time(svc, original_time)`.
4. Возвращаем `suggested_time` пользователю.

**Пользователь видит:**
```
К сожалению, в 15:00 подходящие сервисы не работают.
Ближайшее доступное время: 09:00
```
Кнопки: «Записаться на 09:00» / «Выбрать другую дату».

### Шаг 4: Скоринг

Каждый оставшийся сервис получает итоговый скор:

```
score = 0.6 × proximity_score + 0.4 × rating_score
```

**proximity_score** (по графу пересадок метро, BFS):

| Расстояние (пересадки) | Скор |
|---|---|
| 0 — та же станция | 1.00 |
| 1 — прямой переход | 0.85 |
| 2 — через одну пересадку | 0.65 |
| 3 — через две пересадки | 0.45 |
| ≥4 или нет пути | 0.25 |
| Нет данных о метро | 0.50 |

**rating_score**:
```
rating_score = min(yandex_rating, 5.0) / 5.0
```
Если рейтинг не задан → `0.5` (нейтрально).

### Шаг 5: Сортировка и выбор

- Сортировка по `score` убыванию.
- При одинаковом скоре — по `yandex_rating` убыванию.
- Возвращаем первые `limit` результатов (по умолчанию `limit=1`).

---

## Все сценарии в хендлере `pick_time`

### Сценарий 1: Сервис найден

```
result.matches непусто, time_fallback=False
```

→ Берём лучший сервис (`result.matches[0]`), записываем `service_id`, `diagnostics_price`, `diagnostics_included` в FSM. Переход к подтверждению заявки.

### Сценарий 2: Time fallback с альтернативой

```
result.time_fallback=True, result.suggested_time="09:00"
```

→ Пользователю предлагается:
- «Записаться на 09:00» → повторный вызов `pick_time` с новым временем
- «Выбрать другую дату» → возврат к календарю

### Сценарий 3: Ни одного сервиса

```
result.matches пуст
```

→ FSM сбрасывается. Пользователь получает:
```
К сожалению, подходящих сервис-центров не найдено. Попробуйте позже.
```

### Сценарий 4: Сервис стал недоступен к моменту подтверждения

Проверка в `confirm_order`: если сервис с `service_id` больше не `is_available` — сообщение «Сервис-центр стал недоступен. Начните заново.»

---

## Причины пустого результата

| Причина | Как проявляется |
|---|---|
| БД пустая (нет сервисов) | Синхронизация не отработала или Google Sheet пустой |
| Нет `is_available=True` | Все сервисы отключены или приостановлены |
| Статус не «активный» | Партнёр не одобрен или анкета не завершена |
| Тип не совпадает | Ремонт → нет `repair`/`complex`. Апгрейд → нет `upgrade`/`complex` |
| Гидроизоляция | Ни один сервис не имеет `has_hydroisolation=True` |
| Категория Механика/Электрика | `category_id` сервисов не соответствует запрошенной категории |
| Все вне рабочего времени + нет fallback | Теоретически невозможно (fallback возвращает все) |

---

## Расширение: `ProximityStrategy`

Архитектура поддерживает замену стратегии близости через Protocol:

```python
class ProximityStrategy(Protocol):
    async def setup(self, session: AsyncSession) -> None: ...
    def score(self, service: Service, ctx: RankingContext) -> float: ...
```

**Текущая**: `MetroProximityStrategy` — BFS по графу пересадок (`metro_graph.py`).

**Будущая**: `GeocodingProximityStrategy` — геокодирование через Yandex Maps API, расстояние по формуле Хаверсина.

---

## Константы

| Константа | Значение | Описание |
|---|---|---|
| `WEIGHT_PROXIMITY` | 0.6 | Вес близости метро |
| `WEIGHT_RATING` | 0.4 | Вес рейтинга Я.Карт |
| `MAX_YANDEX_RATING` | 5.0 | Максимальный рейтинг |
| `SCORE_METRO_FAR` | 0.25 | Скор для далёких/недоступных станций |
| `SCORE_METRO_UNKNOWN` | 0.50 | Скор при отсутствии данных |
