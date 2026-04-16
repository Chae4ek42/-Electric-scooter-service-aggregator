# ESAS — Обзор проекта для разработчика

## Описание

ESAS — Telegram-бот для приёма заявок на ремонт и апгрейд электросамокатов. Написан на Python 3.12 с использованием aiogram 3.x (async FSM), SQLAlchemy 2.0 + aiosqlite (SQLite), Pydantic v2, Redis (хранение FSM-состояний и throttling). Интеграция с Google Sheets через публичный CSV-экспорт без учётных данных.

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
│   ├── config.py        # .env (секреты) + config.yaml (настройки, Pydantic-валидация)
│   ├── database.py      # async engine + sessionmaker
│   └── middlewares.py   # ActionLogger, Throttling, ErrorMiddleware
│
├── domain/              # Доменный слой (данные и состояния)
│   ├── models.py        # SQLAlchemy ORM: Brand, Model, Service,
│   │                #   ServiceCategory, MetroStation, User, Order, UserAction
│   ├── states.py        # OrderFSM (14), RegistrationFSM (19), PartnerProfileFSM, PartnerOrderFSM, ClientOrderFSM
│   └── schemas.py       # Pydantic: MetroTextInput, ModelNameInput, ProblemDescription,
│                    #   BrandNameInput, ServiceNameInput, AddressInput, PhoneInput,
│                    #   TelegramHandleInput, WorkHoursInput, DiagnosticsPriceInput,
│                    #   RejectReasonInput, BankAccountInput, BankNameInput, BikInput,
│                    #   CorrAccountInput, OrgNameInput, InnInput
│
├── services/            # Бизнес-логика и интеграции
│   ├── seed.py          # init_db() — создание таблиц + seed данных
│   ├── metro_search.py  # Fuzzy-match по 266 станциям метро
│   ├── metro_graph.py   # BFS-граф пересадок: metro_transfer_distance(a, b)
│   ├── sheets_sync.py   # Синхронизация сервисов из Google Sheets (CSV)│   ├── sheets_writer.py # Запись данных обратно в Google Sheets
│   ├── fsm_reminder.py  # 30-мин напоминание о незавершённой форме│   └── ranking.py       # Ранжирование сервисов под запрос пользователя
│
├── ui/
│   └── keyboards.py     # Все Reply и Inline клавиатуры; логика московского времени
│
└── handlers/            # aiogram роутеры
    ├── common.py        # /start (видео + приветствие), кнопка «Поддержка»
    ├── order.py         # Полный FSM-flow заявки + «Мои заявки» + pay-хендлеры
    └── admin.py         # Панель администратора (IsAdmin фильтр)

tests/
├── test_smoke.py        # 18 авто-проверок без Telegram API
└── test_partner.py     # 126 unit-тестов схем, клавиатур, хендлеров, FSM-состояний
```

---

## Ключевые модули

### `bot/core/config.py`
Конфигурация разделена на два уровня:
- **`.env`** — секреты (токены, ключи, админы, URL БД/Redis)
- **`config.yaml`** (корень проекта) — операционные настройки с Pydantic-валидацией (модель `AppConfig`)

#### Секреты (`.env`)

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен клиентского бота (**обязательно**) |
| `PARTNER_BOT_TOKEN` | Токен партнёрского бота |
| `GOOGLE_SHEET_ID` | ID Google Таблицы |
| `REDIS_URL` | URL Redis для FSM storage и throttling (default: `redis://localhost:6379/0`) |

#### Настройки (`config.yaml`)

| Раздел | Параметр | По умолчанию | Описание |
|---|---|---|---|
| — | `support_user` | `@i_jusp` | Контакт техподдержки |
| — | `cooperation_user` | `@i_jusp` | Контакт для сотрудничества |
| — | `admin_usernames` | `[]` | Список username админов (без @) |
| — | `database_url` | `sqlite+aiosqlite:///esas.db` | URL подключения к БД |
| — | `google_sa_path` | `service-account-key.json` | Путь к JSON-ключу Service Account |
| `sheets` | `sync_interval` | `300` | Интервал синхронизации Google Sheets (сек) |
| `sheets` | `tab_services` | `Сервисы` | Название листа сервисов |
| `sheets` | `tab_orders` | `Заявки` | Название листа заявок |
| `sheets` | `tab_clients` | `Клиенты` | Название листа клиентов |
| `sheets` | `columns` | 17 колонок | Порядок столбцов листа «Сервисы» |
| `calendar` | `days` | `14` | Дней вперёд в календаре |
| `calendar` | `work_hour_start` | `8` | Начало рабочего дня (час, МСК) |
| `calendar` | `work_hour_end` | `22` | Конец рабочего дня (час, МСК) |
| `calendar` | `time_slot_minutes` | `60` | Шаг тайм-слота (мин) |
| — | `throttle_rate` | `0.2` | Мин. интервал между запросами (сек) |
| `fsm_reminder` | `timeout` | `1800` | Секунды до напоминания |
| `fsm_reminder` | `check_interval` | `60` | Интервал проверки (сек) |

> Если `config.yaml` отсутствует, используются значения по умолчанию. При невалидном YAML бот не стартует (Pydantic `ValidationError`).

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
| `hydroisolation_price` | `str?` | Цена гидроизоляции (фиксированная или диапазон) |
| `diagnostics_price` | `float?` | Стоимость диагностики |
| `diagnostics_included` | `bool` | Входит ли в стоимость |
| `upgrade_categories` | `str?` | Категории апгрейда через запятую (Окраска, Прошивка и т.д.) |
| `working_days` | `str?` | Рабочие дни через запятую (Пн,Вт,...) |
| `partnership_status` | `str?` | Статус партнёрства |
| `main_brand_scooter` | `str?` | Основной бренд самокатов |
| `category_id` | `int?` FK | Связь с `ServiceCategory` (Механика/Электрика) |

`Order.model_id` — nullable (поддерживает кнопку «Другое»). `Order.model_custom_name` — свободный ввод модели. `Order.total_cost` — итоговая стоимость, устанавливаемая партнёром.

Поля жизненного цикла:
- `estimate_cost` (Float) — стоимость в смете
- `estimate_items` (Text) — перечень работ
- `estimate_deadline` (String) — срок выполнения
- `estimate_description` (Text) — описание работ (опц.)
- `client_visited` (Boolean) — был ли клиент в сервисе
- `client_confirmed_estimate` (Boolean) — подтвердил ли клиент смету
- `dispute_reason` (Text) — причина оспаривания
- `refusal_reason` (Text) — причина отказа клиента

Статусы Order: `awaiting_payment`, `accepted`, `in_progress`, `ready_for_pickup`, `completed`, `cancelled`, `interrupted`, `rejected_by_partner`, `client_refused`, `disputed`.

### `bot/domain/states.py`
`OrderFSM` — 14 состояний:

```
service_type → brand [→ brand_custom] → model [→ model_custom]
  → malfunction_type (only repair) / upgrade_category (only upgrade)
    → problem_description (кроме гидроизоляции)
      → location_method → metro_search → metro_confirm
        → calendar_date → calendar_time
          → confirm
```

`RegistrationFSM` — 19 состояний (партнёрский бот):

```
reg_name → reg_service_type → reg_category (если ремонт)
  → reg_upgrade_categories (если апгрейд)
  → reg_hydroisolation → reg_hydro_price (если да) → reg_address → reg_metro_search → reg_metro_confirm
    → reg_phone → reg_telegram → reg_working_days → reg_hours
      → reg_diagnostics → reg_diag_included → reg_legal_form → reg_tax_system
        → reg_bank_details (6 полей подряд) → reg_confirm
```

При типе «ремонт» запрашивается категория: Электроника / Механика / Комплекс.
При изменении типа услуг несовместимые поля (draft_category / draft_upgrade_categories) очищаются автоматически.

Состояния `specific_problem` и `payment` удалены из потока. Сервис-центр подбирается автоматически после выбора времени (перед экраном подтверждения). Оплата вызывается заглушкой сразу после создания заявки.

Константы (не из .env):
- `SHEETS_COLUMNS` — порядок столбцов листа «Сервисы» (hardcoded в config.py)
- `SHEETS_TAB_SERVICES` / `SHEETS_TAB_ORDERS` / `SHEETS_TAB_CLIENTS` — названия листов Google Sheets

**Auto-payment:** При создании заявки и при финальной оплате платёж автоматически подтверждается через 10 секунд (mock-режим).

**Тестовые данные:** При первом запуске `seed_test_data()` создаёт 6 тестовых сервисов, 3 клиентов и 4 заявки с префиксом `test_`. Пропускается, если тестовые данные уже есть.

**Google Sheets:** Три листа — Сервисы (чтение + запись), Заявки (write-only debug), Клиенты (write-only debug). Листы создаются автоматически при первой записи.

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
| Основной бренд самокатов | `main_brand_scooter` | Текст |
| Статус | `partnership_status` | |
| Доступен | `is_available` | да/yes/1/true → `True` |
| Категория | `category_id` | FK на ServiceCategory |
| Гидроизоляция | `has_hydroisolation` | да/нет |
| Цена гидроизоляции | `hydroisolation_price` | Фиксированная или диапазон |
| Диагностика | `diagnostics_price` | Стоимость |
| Входит в стоимость | `diagnostics_included` | да/нет |

**Upsert-логика:** поиск по `name`. Если запись существует — обновляются только реально изменившиеся поля (каждое поле сравнивается с текущим значением в БД; счётчик `изменено` инкрементируется только при наличии фактических отличий). Новая запись без типа — создаётся с `service_type="complex"`.

**Лог синхронизации:** `"Sheets sync: добавлено N, изменено M"` — `M` отражает число записей, в которых реально изменилось хотя бы одно поле относительно предыдущего состояния.

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
- Для заказов типа «Гидроизоляция»: пропускается описание проблемы, на экране подтверждения показывается цена гидроизоляции из БД сервиса + предоплата 500 руб.
- Платёжные хендлеры (`pay:proceed:*`, `pay:cancel:*`) — state-agnostic, работают и из FSM-потока, и из «Мои заявки».
- «Мои заявки»: показываются все незакрытые заявки, **включая `no_center`** (сервис не найден). Все пользовательские строки (название модели, название сервиса, метро) экранируются через `_md_escape()` перед Markdown-рендерингом.
- `cancel_order` (confirm:no) — заявка **не** сохраняется в БД (отмена до создания). `payment_cancel` — уже созданная заявка переводится в `cancelled`.

### `bot/handlers/common.py`
`/start` (видеоприветствие + главное меню), кнопка «Поддержка».  
Команды `/admin` и `/client` доступны для быстрого переключения режимов:
- `/admin` — открывает главное меню с кнопкой «Панель администратора» (только для `ADMIN_USERNAMES`).
- `/client` — возвращает в главное меню (для всех пользователей).

### `bot/handlers/admin.py`
Доступ по `IsAdmin` фильтру (username в `ADMIN_USERNAMES`). Позволяет просматривать и фильтровать клиентские заявки, менять статус. FSM: `AdminFSM.orders_list`, `AdminFSM.order_detail`. Управление партнёрами (одобрение, отклонение, приостановка) вынесено в партнёрский бот (`partner_bot/handlers/admin.py`).

---

## Порядок регистрации роутеров

```python
dp.include_router(common_router)   # /start, /admin, /client, Поддержка
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
- Throttling (0.2 сек/запрос), ErrorMiddleware- Гидроизоляция: цена (фиксированная/диапазон) из профиля → отображается клиенту, предоплата 500 руб.
- Итоговая стоимость: партнёр вводит через кнопку «Указать итоговую стоимость», клиент получает уведомление с остатком к оплате
- 30-минутный напоминатель о незавершённой форме (FSMActivityMiddleware + background loop). Срабатывает только для состояний анкет: `OrderFSM` (клиентский бот), `RegistrationFSM` (партнёрский бот). Прочие FSM-состояния (диспуты, редактирование профиля, действия с заявками) **не** вызывают напоминаний. При нажатии «Продолжить» пользователю повторно отправляется сообщение того этапа, на котором он остановился.
- Отмена FSM по нажатию кнопок меню (оба бота)
- Полное редактирование профиля (14 полей), поля без ре-модерации (метро, реквизиты)
- Переключение Открыт/Закрыт через текстовое меню без ре-модерации
- Команды /admin и /client для переключения режимов- Полный жизненный цикл заявки: смета партнёра → подтверждение клиентом → готовность к выдаче → оплата/оспаривание
- FSM-состояния сметы (PartnerOrderFSM: cost → items → deadline → description → confirm)
- Клиентские действия: подтверждение сметы, оплата, оспаривание (ClientOrderFSM)
- Статусы `client_refused`, `ready_for_pickup`, `disputed`
- Условное отображение полей в анкете партнёра (скрытие нерелевантных полей)
## Предстоит реализовать ❌

- Реальные платежи (ЮКасса / Telegram Payments)
- Уведомления клиенту за 2 часа до визита
- Переход на PostgreSQL в prod (меняется только `DATABASE_URL`)


## Описание

ESAS — два Telegram-бота для приёма заявок на ремонт и апгрейд электросамокатов:
- **client-bot** (`bot/`) — клиентский бот для пользователей.
- **partner-bot** (`partner_bot/`) — бот для владельцев сервисных центров (регистрация, управление заявками, профиль).

Написан на Python 3.12 с использованием aiogram 3.x (async FSM), SQLAlchemy 2.0 + aiosqlite (SQLite), Pydantic v2. Интеграция с Google Sheets через Service Account (gspread + google-auth) для чтения и записи.

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
│   ├── config.py        # .env (секреты) + config.yaml (настройки, Pydantic-валидация)
│   ├── database.py      # async engine + sessionmaker
│   └── middlewares.py   # ActionLogger, Throttling, ErrorMiddleware
│
├── domain/              # Доменный слой (данные и состояния)
│   ├── models.py        # SQLAlchemy ORM: Brand, Model, Service,
│   │                    #   MetroStation, User, Order, UserAction
│   ├── states.py        # OrderFSM, RegistrationFSM, PartnerProfileFSM,
│   │                    #   PartnerOrderFSM, ClientOrderFSM
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
    ├── common.py        # /start (видео + приветствие), кнопка «Поддержка»
    ├── order.py         # Полный FSM-flow заявки + «Мои заявки»
    └── admin.py         # Панель администратора (IsAdmin фильтр)

tests/
├── test_smoke.py        # Комплексный smoke-тест без Telegram API
└── test_partner.py      # 144 теста: схемы, модели, состояния, клавиатуры, импорты
```

---

## Ключевые модули

### `bot/core/config.py`
Все настройки — из переменных окружения через `os.environ`. Нет pydantic-settings.
Важные переменные: `BOT_TOKEN`, `ADMIN_USERNAMES` (set[str], нижний регистр без @), `GOOGLE_SHEET_ID`, `SHEETS_SYNC_INTERVAL`, `SHEETS_COLUMNS`.

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
dp.include_router(common_router)   # /start, Поддержка
dp.include_router(order_router)    # FSM + Мои заявки
dp.include_router(admin_router)    # Панель администратора
```

Middlewares применяются к `dp.message` и `dp.callback_query`:
`ErrorMiddleware → ThrottlingMiddleware → ActionLoggerMiddleware` (outer → inner).

---

## Google Sheets интеграция

Синхронизация — каждые `SHEETS_SYNC_INTERVAL` секунд (по умолчанию 300).
Порядок столбцов настраивается через `SHEETS_COLUMNS` в `.env`.
Листы: **«Сервисы»** (upsert в таблицу `services`) и **«Настройки»** (runtime-параметры).

Регистр заголовков и значений в таблице не важен. Неизвестные столбцы игнорируются.
При расхождении столбцов таблицы и `SHEETS_COLUMNS` выводится предупреждение `SYNC_COLUMNS_MISMATCH`.

Если `GOOGLE_SHEET_ID` не задан — синхронизация молча пропускается.

Подробная схема логирования: [LOG_SCHEMA.md](LOG_SCHEMA.md).

---

## Реализовано

- Полный flow заявки (ремонт и апгрейд), кнопка «Другое» для ввода модели вручную
- 266 станций метро, нечёткий поиск (top-5), геолокация с fallback
- Динамический календарь (14 дней), часовые тайм-слоты 08:00–22:00
- Кнопка «Назад» из любого состояния FSM
- Прерывание FSM меню-кнопками («Мои заявки», «Поддержка»)
- Панель администратора с пагинацией и сменой статусов
- ActionLogger — все действия пользователя в таблице `user_actions`
- Throttling (0.2 сек/запрос), ErrorMiddleware
- Полный жизненный цикл заявки после предоплаты (смета, выдача, оплата, оспаривание)
- Команды `/admin` и `/client` в обоих ботах; `set_my_commands` регистрирует их в меню
- Заявки со статусом `no_center` отображаются в «Мои заявки» с меткой «Сервис не найден»
- Markdown-экранирование пользовательских данных в «Мои заявки» (`_md_escape`)
- Часовой пояс контейнеров — `Europe/Moscow` (`TZ=Europe/Moscow` в docker-compose)

## Предстоит реализовать

- Реальные платежи (ЮKassa / Telegram Payments)
- Уведомления клиенту за 2 часа до визита
- Переход на PostgreSQL в prod (меняется только `DATABASE_URL`)

