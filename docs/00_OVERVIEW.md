# ESAS — Обзор проекта для разработчика

## Описание

ESAS — Telegram-бот для приёма заявок на ремонт и апгрейд электросамокатов. Написан на Python 3.12 с использованием aiogram 3.x (async FSM), SQLAlchemy 2.0 + aiosqlite (SQLite), Pydantic v2. Интеграция с Google Sheets через публичный CSV-экспорт без учётных данных.

---

## Быстрый старт

```bash
# 1. Создать .env в корне проекта
BOT_TOKEN=<token>
ADMIN_USERNAMES=username1,username2   # без @
SUPPORT_USER=@support_handle
GOOGLE_SHEET_ID=<sheet_id>            # опционально

# 2. Установить зависимости
pip install -e .

# 3. Запустить бота
python -m bot

# 4. Запустить тесты
pytest tests/test_smoke.py -v
```

---

## Архитектура пакета `bot/`

```
bot/
├── __init__.py
├── __main__.py          # Точка входа: init_db → sheets sync → polling
│
├── core/                # Инфраструктурный слой
│   ├── config.py        # Переменные окружения (.env)
│   ├── database.py      # async engine + sessionmaker
│   └── middlewares.py   # ActionLogger, Throttling, ErrorMiddleware
│
├── domain/              # Доменный слой (данные и состояния)
│   ├── models.py        # SQLAlchemy ORM: Brand, Model, Service,
│   │                #   ServiceCategory, MetroStation, User, Order, UserAction
│   ├── states.py        # OrderFSM — 14 состояний FSM
│   └── schemas.py       # Pydantic: MetroTextInput, ModelNameInput, ProblemDescription,
│                    #   BrandNameInput
│
├── services/            # Бизнес-логика и интеграции
│   ├── seed.py          # init_db() — создание таблиц + seed данных
│   ├── metro_search.py  # Fuzzy-match по 266 станциям метро
│   ├── metro_graph.py   # BFS-граф пересадок: metro_transfer_distance(a, b)
│   ├── sheets_sync.py   # Синхронизация сервисов из Google Sheets (CSV)
│   └── ranking.py       # Ранжирование сервисов под запрос пользователя
│
├── ui/
│   └── keyboards.py     # Все Reply и Inline клавиатуры; логика московского времени
│
└── handlers/            # aiogram роутеры
    ├── common.py        # /start, кнопка «Техподдержка»
    ├── order.py         # Полный FSM-flow заявки + «Мои заявки» + pay-хендлеры
    └── admin.py         # Панель администратора (IsAdmin фильтр)

tests/
└── test_smoke.py        # 12 авто-проверок без Telegram API
```

---

## Ключевые модули

### `bot/core/config.py`
Все настройки — из `os.environ` (через `.env`). Нет pydantic-settings.

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | **обязательно** | Токен Telegram Bot API |
| `DATABASE_URL` | `sqlite+aiosqlite:///esas.db` | URL подключения к БД |
| `ADMIN_USERNAMES` | `""` | Username-ы администраторов через запятую (без @) |
| `SUPPORT_USER` | `@i_jusp` | Контакт техподдержки |
| `GOOGLE_SHEET_ID` | `""` | ID публичной Google Таблицы |
| `SHEETS_SYNC_INTERVAL` | `300` | Интервал синхронизации, сек |
| `THROTTLE_RATE` | `0.2` | Мин. интервал между запросами, сек |
| `CALENDAR_DAYS` | `14` | Дней вперёд в календаре |
| `WORK_HOUR_START` | `8` | Начало рабочего дня (час, моск. время) |
| `WORK_HOUR_END` | `22` | Конец рабочего дня (час, моск. время) |
| `TIME_SLOT_MINUTES` | `60` | Шаг тайм-слота, мин |

### `bot/domain/models.py`
ORM-модели. Текущий набор полей `Service`:

| Поле | Тип | Описание |
|---|---|---|
| `name` | `str` | Название сервис-центра |
| `service_type` | `str` | `'repair'` / `'upgrade'` / `'complex'` |
| `is_available` | `bool` | Доступен ли сервис (из кол. «Доступен» в таблице) |
| `address` | `str?` | Адрес |
| `yandex_rating` | `float?` | Рейтинг Яндекс.Карт |
| `nearest_metro` | `str?` | Ближайшая станция метро |
| `phone` | `str?` | Телефон |
| `telegram_handle` | `str?` | Telegram-ссылка |
| `open_time` | `str?` | Время открытия (HH:MM) |
| `close_time` | `str?` | Время закрытия (HH:MM) |
| `has_hydroisolation` | `bool` | Делают ли гидроизоляцию |
| `diagnostics_price` | `float?` | Стоимость диагностики |
| `diagnostics_included` | `bool` | Входит ли в стоимость |
| `partnership_status` | `str?` | Статус партнёрства |
| `category_id` | `int?` FK | Связь с `ServiceCategory` (Механика/Электрика) |

`Order.model_id` — nullable (поддерживает кнопку «Другое»). `Order.model_custom_name` — свободный ввод модели.

### `bot/domain/states.py`
`OrderFSM` — 11 состояний:

```
service_type → brand → model [→ model_custom]
  → malfunction_type (only repair)
    → location_method → metro_search → metro_confirm
      → calendar_date → calendar_time
        → confirm
```

Состояния `specific_problem` и `payment` удалены из потока. Сервис-центр подбирается автоматически после выбора времени (перед экраном подтверждения). Оплата вызывается заглушкой сразу после создания заявки.

### `bot/services/ranking.py`
Ранжирование сервис-центров под запрос пользователя. Принимает `RankingContext` (тип работы, категория, метро пользователя) и возвращает `list[ServiceMatch]`, отсортированный по убыванию скора. Фильтрыет только `is_available=True`. Стратегия близости — `MetroProximityStrategy` (через `metro_graph.py`, BFS).

Веса: `WEIGHT_PROXIMITY=0.6`, `WEIGHT_RATING=0.4`.

### `bot/services/metro_graph.py`
BFS-поиск по графу пересадок. Станции с одинаковым названием на разных линиях — автоматически в одном узле (distance=0).

```python
dist = metro_transfer_distance("Курская", "Чкаловская")  # → 1
dist = metro_transfer_distance("Тверская", "Пушкинская")  # → 1
dist = metro_transfer_distance("Ленинский проспект", "Охотный Ряд")  # → None
```

Скоры по dist: 0→`1.00`, 1→`0.85`, 2→`0.65`, 3→`0.45`, ≥4/None→`0.25`.

### `bot/services/sheets_sync.py`
Синхронизация сервис-центров через CSV. Читаемые столбцы листа «Сервисы»:

| Столбец | Поле модели | Примечание |
|---|---|---|
| Название | `name` | A при пустом — пропуск. |
| Рейтинг Я.Карты | `yandex_rating` | Флоат (`,` → `.`) |
| Телефон | `phone` | |
| Telegram | `telegram_handle` | |
| Адрес | `address` | |
| Метро ближ. | `nearest_metro` | |
| Специализация | `service_type` | Новый без типа → `complex` |
| Статус | `partnership_status` | |
| Доступен | `is_available` | да/yes/1/true → `True` |
| Категория | `category_id` | FK на ServiceCategory |

**Upsert-логика:** поиск по `name`. Если запись существует — обновляется все. Новая запись без типа — создаётся с `service_type="complex"`.

**При ошибке сети** (таймаут, `aiohttp.ClientError`) — бот запускается с предупреждением в лог. Если таблица доступна, но 0 записей — `ValueError`.

### `bot/services/seed.py`
`init_db()` — создаёт таблицы через `Base.metadata.create_all`, применяет inline-миграции (ALTER TABLE), вызывает `seed_database()`.

Сервисные центры в seed больше не вносятся — все данные приходят из Google Sheets. Seed содержит: 15 брендов, ~111 моделей (+«Другое» на каждый), 2 категории сервисов, 266 станций метро.

### `bot/ui/keyboards.py`
Все клавиатуры. Ключевая логика:

- **`calendar_kb()`** — генерация дат в московском времени (`zoneinfo.ZoneInfo("Europe/Moscow")`). Сегодня включается, если `now.hour+1 < WORK_HOUR_END`. Тотал: от 14 до 15 кнопок + 1 назад.
- **`time_slots_kb(date_str)`** — для сегодняшней даты `min_hour = now.hour+1` (next full hour). Для будущих — полный диапазон. Слоты: `max(WORK_HOUR_START, min_hour)..WORK_HOUR_END`. Если слотов нет — заглушка + `noop`.
- **`payment_kb(order_id)`** — стаб ("Pereyti k oplate" / "Отменить заявку").

### `bot/handlers/order.py`
Самый большой модуль. Обрабатывает весь пользовательский flow.

**Ключевые особенности:**
- Переход без шага выбора сервиса: в `pick_time` вызывается `rank_services()`, сервис-центр подбирается автоматически и показывается на экране подтверждения.
- Заявка создаётся со статусом `awaiting_payment`. Сразу отправляется заглушка предоплаты.
- Платёжные хендлеры (`pay:proceed:*`, `pay:cancel:*`) — state-agnostic, работают и из FSM-потока, и из «Мои заявки».
- «Мои заявки»: каждая заявка — отдельное сообщение (не одним текстовым полотном). Для заявок со статусом `awaiting_payment` — inline-кнопки оплаты.
- `cancel_order` (confirm:no) — заявка **не** сохраняется в БД (отмена до создания). `payment_cancel` — уже созданная заявка переводится в `cancelled`.

### `bot/handlers/admin.py`
Доступ по `IsAdmin` фильтру (username в `ADMIN_USERNAMES`). Позволяет просматривать и фильтровать заявки, менять статус. FSM: `AdminFSM.orders_list`, `AdminFSM.order_detail`.

---

## Порядок регистрации роутеров

```python
dp.include_router(common_router)   # /start, Техподдержка
dp.include_router(order_router)    # FSM + Мои заявки + pay-хендлеры
dp.include_router(admin_router)    # Панель администратора
```

Middlewares применяются к `dp.message` и `dp.callback_query`:
`ErrorMiddleware → ThrottlingMiddleware → ActionLoggerMiddleware` (outer → inner).

---

## Реализовано ✅

- Полный FSM-flow заявки (ремонт и апгрейд), кнопка «Другое» для ввода модели вручную
- Автоподбор сервис-центра через `rank_services()` — по метро + рейтинг
- BFS-граф пересадок метро Москвы
- 266 станций, нечёткий поиск (top-5), фоллбак при множественных совпадениях
- Календарь с сегодней датой (если есть слоты), фильтрация прошедшего времени (МСК)
- Список заявок: каждая отдельным сообщением + inline-кнопки оплаты для `awaiting_payment`
- Синхронизация из Google Sheets (сечас — лист «Сервисы», upsert по названию)
- Панель администратора с пагинацией и сменой статусов
- `ActionLogger` — все действия пользователя в `user_actions`
- Throttling (0.2 сек/запрос), ErrorMiddleware

## Предстоит реализовать ❌

- Реальные платежи (ЮКасса / Telegram Payments)
- Уведомления клиенту за 2 часа до визита
- Переход на PostgreSQL в prod (меняется только `DATABASE_URL`)


## Описание

ESAS — Telegram-бот для приёма заявок на ремонт и апгрейд электросамокатов. Написан на Python 3.12 с использованием aiogram 3.x (async FSM), SQLAlchemy 2.0 + aiosqlite (SQLite), Pydantic v2. Интеграция с Google Sheets через публичный CSV-экспорт без учётных данных.

---

## Быстрый старт

```bash
# 1. Создать .env в корне проекта
BOT_TOKEN=<token>
ADMIN_USERNAMES=username1,username2   # без @
SUPPORT_USER=@support_handle
GOOGLE_SHEET_ID=<sheet_id>            # опционально

# 2. Установить зависимости
pip install -e .

# 3. Запустить бота
python -m bot

# 4. Запустить тесты
pytest tests/test_smoke.py -v
```

---

## Архитектура пакета `bot/`

```
bot/
├── __init__.py
├── __main__.py          # Точка входа: init_db → sheets sync → polling
│
├── core/                # Инфраструктурный слой
│   ├── config.py        # Переменные окружения (.env)
│   ├── database.py      # async engine + sessionmaker
│   └── middlewares.py   # ActionLogger, Throttling, ErrorMiddleware
│
├── domain/              # Доменный слой (данные и состояния)
│   ├── models.py        # SQLAlchemy ORM: Brand, Model, Service,
│   │                    #   MetroStation, User, Order, UserAction
│   ├── states.py        # OrderFSM — 13 состояний FSM
│   └── schemas.py       # Pydantic: MetroTextInput, ModelNameInput, ProblemDescription
│
├── services/            # Бизнес-логика и интеграции
│   ├── seed.py          # init_db() — создание таблиц + seed данных
│   ├── metro_search.py  # Fuzzy-match по 266 станциям метро Москвы
│   ├── sheets_sync.py   # Синхронизация сервисов из Google Sheets (CSV)
│   └── ranking.py       # Ранжирование сервисов под запрос пользователя
│
├── ui/
│   └── keyboards.py     # Все Reply и Inline клавиатуры
│
└── handlers/            # aiogram роутеры
    ├── common.py        # /start, кнопка «Техподдержка»
    ├── order.py         # Полный FSM-flow заявки + «Мои заявки»
    └── admin.py         # Панель администратора (IsAdmin фильтр)

tests/
└── test_smoke.py        # 12 авто-проверок без Telegram API
```

---

## Ключевые модули

### `bot/core/config.py`
Все настройки — из переменных окружения через `os.environ`. Нет pydantic-settings.
Важные переменные: `BOT_TOKEN`, `ADMIN_USERNAMES` (set[str], нижний регистр без @), `GOOGLE_SHEET_ID`, `SHEETS_SYNC_INTERVAL`.

### `bot/domain/models.py`
ORM-модели. `Order.model_id` — nullable (поддерживает кнопку «Другое»). `Order.model_custom_name` — свободный ввод модели.

### `bot/domain/states.py`
`OrderFSM` — 13 состояний: `service_type → brand → model → model_custom → malfunction_type → specific_problem → location_method → metro_search → metro_confirm → calendar_date → calendar_time → confirm → payment`.

### `bot/services/ranking.py`
Ранжирование сервис-центров под запрос пользователя. Принимает `RankingContext` (тип работы, категория неисправности, метро пользователя, опциональные GPS-координаты) и возвращает `list[ServiceMatch]`, отсортированный по убыванию итогового скора.

Стратегия близости подключается через `ProximityStrategy` Protocol — дефолт: `MetroProximityStrategy` (без внешних API). Для геокодирования — подключить `GeocodingProximityStrategy`. Подробнее: [03_SERVICE_RANKING.md](03_SERVICE_RANKING.md).

### `bot/services/seed.py`
`init_db()` — создаёт таблицы через `Base.metadata.create_all`, применяет inline-миграции (ALTER TABLE), вызывает `seed_database()`.

### `bot/handlers/order.py`
Самый большой модуль (~840 строк). Обрабатывает весь пользовательский flow. Содержит универсальный обработчик `back` (if/elif по текущему состоянию), catch-all для неожиданного текста.

### `bot/handlers/admin.py`
Доступ по `IsAdmin` фильтру (username в `ADMIN_USERNAMES`). Позволяет просматривать и фильтровать заявки, менять их статус. FSM: `AdminFSM.orders_list`, `AdminFSM.order_detail`.

---

## Порядок регистрации роутеров

```python
dp.include_router(common_router)   # /start, Техподдержка
dp.include_router(order_router)    # FSM + Мои заявки
dp.include_router(admin_router)    # Панель администратора
```

Middlewares применяются к `dp.message` и `dp.callback_query`:
`ErrorMiddleware → ThrottlingMiddleware → ActionLoggerMiddleware` (outer → inner).

---

## Google Sheets интеграция

Синхронизация — каждые `SHEETS_SYNC_INTERVAL` секунд (по умолчанию 300).
Листы: **«Сервисы»** (upsert в таблицу `services`, поля: Название, Специализация, Категория, Доступен, Адрес, Рейтинг Я.Карт, Метро ближ.) и **«Настройки»** (runtime-параметры `WORK_HOUR_START`, `WORK_HOUR_END`, `CALENDAR_DAYS`, `SLOT_STEP_MINUTES`, `SUPPORT_USER`).

Регистр заголовков и значений в таблице не важен. Неизвестные столбцы игнорируются.

Если `GOOGLE_SHEET_ID` не задан — синхронизация молча пропускается.

---

## Реализовано

- Полный flow заявки (ремонт и апгрейд), кнопка «Другое» для ввода модели вручную
- 266 станций метро, нечёткий поиск (top-5), геолокация с fallback
- Динамический календарь (14 дней), часовые тайм-слоты 08:00–22:00
- Кнопка «Назад» из любого состояния FSM
- Прерывание FSM меню-кнопками («Мои заявки», «Техподдержка»)
- Панель администратора с пагинацией и сменой статусов
- ActionLogger — все действия пользователя в таблице `user_actions`
- Throttling (0.2 сек/запрос), ErrorMiddleware

## Предстоит реализовать

- Реальные платежи (ЮKassa / Telegram Payments)
- Уведомления клиенту за 2 часа до визита
- Переход на PostgreSQL в prod (меняется только `DATABASE_URL`)

