# Модуль: Данные и Инфраструктура

## БД — модели (`bot/domain/models.py`)

### Каталог (справочники)

| Таблица | Ключевые поля |
|---|---|
| `brands` | `id`, `name` |
| `models` | `id`, `brand_id` (FK), `name` |
| `service_categories` | `id`, `name` (Механика / Электрика) |
| `services` | `id`, `category_id` (FK, nullable), `name`, `service_type`, `is_available`, `address`, `yandex_rating`, `nearest_metro`, `phone`, `telegram_handle`, `partnership_status` |
| `metro_stations` | `id`, `name`, `line`, `lat` (nullable), `lon` (nullable) |

**`service_type`**: `'repair'` — только ремонт, `'upgrade'` — только апгрейд, `'complex'` — оба типа.

**`is_available`**: `Boolean`, `default=True`. Синхронизируется из колонки «Доступен» в Google Sheets. Все запросы к списку сервисов фильтруют `is_available.is_(True)`.

> Удалённые поля: `price`, `top_service`, `main_brand`, `diagnostics_price`. Данные о 6 сервисах в seed больше не вносятся — всё из Google Sheets.

### Пользователи и заявки

| Таблица | Ключевые поля |
|---|---|
| `users` | `id` (TG BigInteger), `username`, `full_name`, `created_at` |
| `orders` | `id`, `user_id` (FK), `service_id` (FK), `model_id` (FK, **nullable**), `model_custom_name` (nullable), `metro_station`, `scheduled_date`, `scheduled_time`, `status`, `created_at` |

**Статусы заявки:**

```
awaiting_payment → accepted / cancelled / unpaid_diagnostics / interrupted / completed
```

`model_id` nullable — поддерживает кнопку «Другое»: в этом случае имя хранится в `model_custom_name`, `model_id` — на строку-плейсхолдер «Другое» (или NULL).

### Логирование

`user_actions`: `user_id`, `state`, `action_type`, `payload`, `status`, `error_context`, `timestamp`

---

## Инициализация БД (`bot/services/seed.py`)

`init_db()` выполняет три шага:
1. `Base.metadata.create_all` — создаёт все таблицы
2. Inline-миграция — `ALTER TABLE ... ADD COLUMN` через `aiosqlite` (если столбцы отсутствуют)
3. `seed_database()` — наполняет справочники, если пустые

**Текущие данные seed:**

| Сущность | Количество |
|---|---|
| Бренды | 15 |
| Модели | ~111 (по 6–13 на бренд + «Другое») |
| Категории сервисов | 2 (Механика, Электрика) |
| Станции метро | 266 |
| Сервисные центры | **Только из Google Sheets**, в seed не вносятся |

---

## Валидация данных (`bot/domain/schemas.py`)

| Схема | Поле | Правило |
|---|---|---|
| `MetroTextInput` | `text` | 2–100 символов |
| `ProblemDescription` | `text` | 3–1000 символов |
| `ModelNameInput` | `text` | 2–150 символов, хотя бы одна буква |

При ошибке пользователь получает человеческое сообщение и остаётся в том же FSM-состоянии.

---

## Google Sheets интеграция (`bot/services/sheets_sync.py`)

Публичный CSV-экспорт, учётные данные не нужны.

```
https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Сервисы
```

**Лист «Сервисы» — читаемые столбцы:**

| Столбец в таблице | Поле модели | Примечание |
|---|---|---|
| Название | `name` | Ключ upsert |
| Рейтинг Я.Карты | `yandex_rating` | `float`, запятая → точка |
| Телефон | `phone` | |
| Telegram | `telegram_handle` | |
| Адрес | `address` | |
| Метро ближ. | `nearest_metro` | |
| Специализация | `service_type` | ремонт/апгрейд/комплекс; пусто → `complex` |
| Статус | `partnership_status` | |
| Доступен | `is_available` | да/yes/1/true → `True` |
| Категория | `category_id` | FK на `service_categories` |

**Upsert-логика:**

- Поиск по `name` внутри сессии.
- Если запись существует — обновляются все поля (включая `is_available`). `service_type` обновляется только если получен из таблицы.
- Если запись новая и специализация пуста — создаётся с `service_type="complex"`.
- Дополнительные столбцы игнорируются. Регистр заголовков не важен (`_col()` делает `strip().lower()`).

**Поведение при ошибках:**

| Тип ошибки | Поведение |
|---|---|
| `TimeoutError`, `aiohttp.ClientError`, `OSError` | WARNING в лог, бот запускается с данными из БД |
| Таблица доступна, но 0 записей | `ValueError` — бот не запускается |
| `GOOGLE_SHEET_ID` пуст | Синхронизация молча пропускается |

Фоновая синхронизация — `_sheets_sync_loop()` в `__main__.py`, интервал `SHEETS_SYNC_INTERVAL` сек (300 по умолчанию). Любая ошибка в фоновом цикле логируется и не останавливает бот.

---

## Ранжирование сервисов (`bot/services/ranking.py`)

```python
from bot.services.ranking import RankingContext, rank_services, ServiceMatch

ctx = RankingContext(
    service_type="repair",              # 'repair' | 'upgrade' | 'complex'
    malfunction_category="Механика",   # None для upgrade
    user_metro="Курская",
)
results: list[ServiceMatch] = await rank_services(ctx, session, limit=1)
```

**Фильтрация:**
- `service_type`: `'repair'` → `IN ('repair', 'complex')`, `'upgrade'` → `IN ('upgrade', 'complex')`
- `is_available IS TRUE`
- если `malfunction_category ≠ None` — `JOIN service_categories WHERE name = ...`

**Скор = 0.6 × proximity + 0.4 × rating:**

| proximity (BFS) | Скор |
|---|---|
| dist=0 (та же станция) | 1.00 |
| dist=1 (прямой переход) | 0.85 |
| dist=2 | 0.65 |
| dist=3 | 0.45 |
| dist≥4 или None | 0.25 |
| нет данных о метро | 0.50 |

rating = `min(yandex_rating, 5.0) / 5.0`. При отсутствии рейтинга = 0.5 (нейтрально).

**`metro_graph.py` — BFS-граф пересадок:** узлы — названия станций (строки) в нижнем регистре. Станции с одинаковым названием на разных линиях — автоматически в одном узле (dist=0).

---

## Конфигурация (`bot/core/config.py`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | **обязательно** | Токен Telegram Bot API |
| `DATABASE_URL` | `sqlite+aiosqlite:///esas.db` | URL подключения к БД |
| `ADMIN_USERNAMES` | `""` | Username-ы администраторов через запятую (без @) |
| `SUPPORT_USER` | `@i_jusp` | Контакт техподдержки |
| `GOOGLE_SHEET_ID` | `""` | ID публичной Google Таблицы |
| `SHEETS_SYNC_INTERVAL` | `300` | Интервал фоновой синхронизации, сек |
| `THROTTLE_RATE` | `0.2` | Мин. интервал между запросами, сек |
| `CALENDAR_DAYS` | `14` | Дней вперёд в календаре |
| `WORK_HOUR_START` | `8` | Начало рабочего дня (моск. вр.) |
| `WORK_HOUR_END` | `22` | Конец рабочего дня (моск. вр.) |
| `TIME_SLOT_MINUTES` | `60` | Шаг тайм-слота, мин |

FSM-хранилище — `MemoryStorage` (in-process; перезапуск сбрасывает состояния). Для prod — Redis-backend.


## БД — модели (`bot/domain/models.py`)

### Каталог (справочники)
| Таблица | Ключевые поля |
|---|---|
| `brands` | `id`, `name` |
| `models` | `id`, `brand_id` (FK), `name` |
| `service_categories` | `id`, `name` (Механика / Электрика) |
| `services` | `id`, `category_id` (FK, nullable), `name`, `price`, `service_type` (repair / upgrade / both) |
| `metro_stations` | `id`, `name`, `line` |

### Пользователи и заявки
| Таблица | Ключевые поля |
|---|---|
| `users` | `id` (TG BigInteger), `username`, `full_name`, `created_at` |
| `orders` | `id`, `user_id` (FK), `service_id` (FK), `model_id` (FK, **nullable**), `model_custom_name` (nullable), `metro_station`, `scheduled_date`, `scheduled_time`, `status`, `payment_id`, `problem_description`, `created_at` |

**Статусы заявки:** `awaiting_payment` → `accepted` / `cancelled` / `unpaid_diagnostics` / `interrupted` / `completed`

`model_id` nullable — поддерживает кнопку «Другое»: в этом случае имя хранится в `model_custom_name`, а `model_id` указывает на строку-плейсхолдер «Другое» (или NULL).

### Логирование
- `user_actions`: `user_id`, `state`, `action_type`, `payload`, `status`, `error_context`, `timestamp`

---

## Инициализация БД (`bot/services/seed.py`)

`init_db()` выполняет три шага:
1. `Base.metadata.create_all` — создаёт все таблицы
2. Inline-миграция через raw aiosqlite (добавляет `model_custom_name` если отсутствует)
3. `seed_database()` — наполняет справочники, если таблицы пустые

Данные seed: 15 брендов, ~100 моделей (+«Другое» на каждый бренд), 33 услуги, 266 станций метро Москвы.

---

## Валидация данных (`bot/domain/schemas.py`)

| Схема | Поле | Правило |
|---|---|---|
| `MetroTextInput` | `text` | 2–100 символов |
| `ProblemDescription` | `text` | 3–1000 символов |
| `ModelNameInput` | `text` | 2–150 символов, хотя бы одна буква |

При ошибке валидации пользователь получает человеческое сообщение и остаётся в том же FSM-состоянии.

---

## Google Sheets интеграция (`bot/services/sheets_sync.py`)

Публичный CSV-экспорт, учётные данные не нужны.

```
https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet={Лист}
```

| Лист | Столбцы | Назначение |
|---|---|---|
| Услуги | Название, Тип (ремонт/апгрейд/оба), Категория, Активна (ДА/НЕТ) | Upsert в `services` |
| Настройки | Ключ, Значение | Runtime-параметры конфига |

Ключи «Настройки»: `WORK_HOUR_START`, `WORK_HOUR_END`, `CALENDAR_DAYS`, `SLOT_STEP_MINUTES`, `SUPPORT_USER`.

Если `GOOGLE_SHEET_ID` пуст — синхронизация молча пропускается.  
Запускается при старте и каждые `SHEETS_SYNC_INTERVAL` секунд (по умолчанию 300).

---

## Конфигурация (`bot/core/config.py`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | **обязательно** | Токен Telegram Bot API |
| `DATABASE_URL` | `sqlite+aiosqlite:///esas.db` | URL подключения к БД |
| `ADMIN_USERNAMES` | `""` | Username-ы администраторов через запятую (без @) |
| `SUPPORT_USER` | `@support` | Контакт техподдержки |
| `GOOGLE_SHEET_ID` | `""` | ID публичной Google Таблицы |
| `SHEETS_SYNC_INTERVAL` | `300` | Интервал синхронизации, сек |

FSM-хранилище — `MemoryStorage` (только in-process, перезапуск сбрасывает состояния).  
Для prod рекомендуется добавить Redis или PostgreSQL-backed storage.
