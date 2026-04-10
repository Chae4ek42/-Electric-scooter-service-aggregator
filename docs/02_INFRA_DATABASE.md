# Модуль: Данные и Инфраструктура

## БД — модели (`bot/domain/models.py`)

### Каталог (справочники)

| Таблица | Ключевые поля |
|---|---|
| `brands` | `id`, `name` |
| `models` | `id`, `brand_id` (FK), `name` |
| `service_categories` | `id`, `name` (Механика / Электрика) |
| `services` | `id`, `category_id` (FK, nullable), `name`, `service_type`, `is_available`, `address`, `yandex_rating`, `nearest_metro`, `phone`, `telegram_handle`, `partnership_status`, `main_brand_scooter`, `open_time`, `close_time`, `has_hydroisolation`, `diagnostics_price`, `diagnostics_included`, `upgrade_categories`, `working_days` |
| `metro_stations` | `id`, `name`, `line`, `lat` (nullable), `lon` (nullable) |

**`service_type`**: `'repair'` — только ремонт, `'upgrade'` — только апгрейд, `'complex'` — оба типа.

**`is_available`**: `Boolean`, `default=True`. Синхронизируется из колонки «Доступен» в Google Sheets. Все запросы к списку сервисов фильтруют `is_available.is_(True)`.

> Удалённые поля: `price`, `top_service`. Данные о сервисах в seed не вносятся — всё из Google Sheets.

### Пользователи и заявки

| Таблица | Ключевые поля |
|---|---|
| `users` | `id` (TG BigInteger), `username`, `full_name`, `created_at` |
| `orders` | `id`, `user_id` (FK), `service_id` (FK), `model_id` (FK, **nullable**), `model_custom_name`, `brand_custom_name`, `metro_station`, `scheduled_date`, `scheduled_time`, `problem_description`, `upgrade_category`, `diagnostics_price`, `partner_comment`, `reject_reason`, `accepted_at`, `completed_at`, `status`, `created_at` |
| `service_owners` | `id`, `telegram_id` (BigInteger, unique), `service_id` (FK, nullable), `status`, `registered_at`, `approved_at`, `approved_by`, `draft_*` (22 поля анкеты: name, service_type, category, address, metro, phone, telegram, open_time, close_time, hydroisolation, diagnostics_price, diag_included, upgrade_categories, working_days, legal_form, tax_system, bank_account, bank_name, bik, corr_account, org_name, inn) |
| `service_owner_settings` | `owner_id` (PK, FK), `notif_new_order`, `notif_cancel` |
| `sheets_retry_queue` | `id`, `service_id` (FK), `operation`, `payload_json`, `attempts`, `last_attempt_at`, `created_at` |

**Статусы заявки:**

```
awaiting_payment → accepted → in_progress → completed
                 → rejected_by_partner
                 → cancelled
                 → interrupted
```

`model_id` nullable — поддерживает кнопку «Другое»: в этом случае имя хранится в `model_custom_name`, `model_id` — на строку-плейсхолдер «Другое» (или NULL).

`brand_custom_name` — если пользователь ввёл бренд вручную (кнопка «Другой бренд»).

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
| `BrandNameInput` | `text` | 2–100 символов, хотя бы одна буква |
| `ServiceNameInput` | `text` | 3–200 символов, хотя бы одна буква |
| `AddressInput` | `text` | 10–400 символов |
| `PhoneInput` | `text` | regex `\+?[\d\- ]{7,20}` |
| `TelegramHandleInput` | `text` | `@?[a-zA-Z0-9_]{5,32}`, срезает `@` |
| `WorkHoursInput` | `text` | HH:MM-HH:MM |
| `DiagnosticsPriceInput` | `text` | целое число ≥ 0 |
| `RejectReasonInput` | `text` | 3–500 символов |
| `BankAccountInput` | `text` | ровно 20 цифр |
| `BankNameInput` | `text` | 3–200 символов |
| `BikInput` | `text` | ровно 9 цифр |
| `CorrAccountInput` | `text` | ровно 20 цифр |
| `OrgNameInput` | `text` | 3–300 символов |
| `InnInput` | `text` | 10 или 12 цифр |

При ошибке пользователь получает человеческое сообщение и остаётся в том же FSM-состоянии.

---

## Google Sheets интеграция

### Чтение (`bot/services/sheets_sync.py`)

Приватная таблица через Service Account (gspread). Fallback на публичный CSV если SA не настроен.

```
https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Сервисы
```

### Настройка порядка столбцов

Порядок и набор столбцов задаётся через переменную `SHEETS_COLUMNS` в `.env`:

```env
SHEETS_COLUMNS=Название,Рейтинг Я.Карты,Телефон,Telegram,Адрес,Метро ближ.,Специализация,Основной бренд самокатов,Статус,Доступен,Категория,Открытие,Закрытие,Гидроизоляция,Диагностика,Входит в стоимость
```

Если `SHEETS_COLUMNS` не задан — используется значение по умолчанию из `config.py`.
При записи в таблицу строка формируется в порядке `SHEETS_COLUMNS`.
При чтении столбцы ищутся по заголовкам (порядок не важен), но при расхождении с
реальной таблицей выводится предупреждение `SYNC_COLUMNS_MISMATCH`.

**Лист «Сервисы» — читаемые столбцы:**

| Столбец в таблице | Поле модели | Примечание |
|---|---|---|
| Название | `name` | Ключ upsert |
| Рейтинг Я.Карты | `yandex_rating` | `float`, запятая → точка |
| Телефон | `phone` | |
| Telegram | `telegram_handle` | |
| Адрес | `address` | |
| Метро ближ. | `nearest_metro` | |
| Специализация | `service_type` | ремонт/апгрейд/комплекс|
| Основной бренд самокатов | `main_brand_scooter` | Текст |
| Статус | `partnership_status` | |
| Доступен | `is_available` | да/yes/1/true → `True` |
| Категория | `category_id` | FK на `service_categories` |
| Открытие | `open_time` | Формат HH:MM |
| Закрытие | `close_time` | Формат HH:MM |
| Гидроизоляция | `has_hydroisolation` | Да/Нет → `Boolean` |
| Диагностика | `diagnostics_price` | `float`, стоимость диагностики |
| Входит в стоимость | `diagnostics_included` | Да/Нет → `Boolean` |

**Upsert-логика:**

- Поиск по `name` внутри сессии.
- Если запись существует — каждое поле сравнивается с текущим значением в БД; счётчик `изменено` инкрементируется только при наличии фактических отличий. `service_type` обновляется только если получен из таблицы.
- Если запись новая и специализация пуста — создаётся с `service_type="complex"`.
- Дополнительные столбцы игнорируются. Регистр заголовков не важен (`_col()` делает `strip().lower()`).
- **Лог:** `"Sheets sync: добавлено N, изменено M"` — `M` = число записей, где реально изменилось хотя бы одно поле.

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
from bot.services.ranking import RankingContext, rank_services, RankingResult

ctx = RankingContext(
    service_type="repair",
    malfunction_category="Механика",
    upgrade_category=None,
    user_metro="Курская",
    scheduled_time="14:00",
)
result: RankingResult = await rank_services(ctx, session, limit=1)
# result.matches: list[ServiceMatch]
# result.time_fallback: bool
# result.suggested_time: str | None
```

**Фильтрация:**
- `service_type`: `'repair'` → `IN ('repair', 'complex')`, `'upgrade'` → `IN ('upgrade', 'complex')`
- `is_available IS TRUE`
- Гидроизоляция: `has_hydroisolation IS TRUE` (независимо от `service_type`)
- Фильтр по времени работы (`open_time` / `close_time` vs `scheduled_time`)
- Если ни один сервис не подходит по времени — fallback: возвращает всех + `time_fallback=True` + `suggested_time`
- Если `malfunction_category ≠ None` — `JOIN service_categories WHERE name = ...`

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
| `SHEETS_SYNC_INTERVAL` | `300` | Интервал фоновой синхронизации, сек || `SHEETS_COLUMNS` | *(16 столбцов)* | Порядок столбцов листа «Сервисы» (через запятую) || `THROTTLE_RATE` | `0.2` | Мин. интервал между запросами, сек |
| `REDIS_URL` | `redis://localhost:6379/0` | URL Redis для FSM storage и throttling |
| `CALENDAR_DAYS` | `14` | Дней вперёд в календаре |
| `WORK_HOUR_START` | `8` | Начало рабочего дня (моск. вр.) |
| `WORK_HOUR_END` | `22` | Конец рабочего дня (моск. вр.) |
| `TIME_SLOT_MINUTES` | `60` | Шаг тайм-слота, мин |

FSM-хранилище — `RedisStorage` (из `aiogram.fsm.storage.redis`). Все FSM-состояния и throttle-таймстемпы сохраняются в Redis и переживают перезапуск бота. Конфигурируется через `REDIS_URL`.

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
| `SHEETS_COLUMNS` | *(16 столбцов)* | Порядок столбцов листа «Сервисы» через запятую |

FSM-хранилище — `RedisStorage` (все FSM-состояния и throttle-таймстемпы переживают перезапуск).

---

## Логирование

Структурированная схема логов описана в [LOG_SCHEMA.md](LOG_SCHEMA.md).

Ключевые теги: `SYNC_START`, `SYNC_FETCHED`, `SYNC_RESULT`, `SYNC_FETCH_ERR`, `SYNC_COLUMNS_MISMATCH`, `SHEETS_WRITE`, `SHEETS_WRITE_ERR`.

---

## Перезапуск при ошибках

### Клиентский бот

```powershell
# Остановить: Ctrl+C в терминале где запущен, затем:
python -m bot
```

### Партнёрский бот

```powershell
python -m partner_bot
```

### После изменения .env

Перезапуск обязателен — переменные считываются один раз при старте.

### При ошибке синхронизации с Google Sheets

1. Проверить `GOOGLE_SHEET_ID` и `GOOGLE_SA_PATH` в `.env`.
2. Посмотреть в логах тег `SYNC_FETCH_ERR` или `SYNC_COLUMNS_MISMATCH`.
3. Если ошибка сети — бот работает со старыми данными из БД и повторит синхронизацию через `SHEETS_SYNC_INTERVAL` сек.
4. Если ошибка «0 записей» — исправить таблицу и перезапустить бот.

### При ошибке БД (повреждён `esas.db`)

```powershell
# Удалить БД (будет пересоздана при старте):
Remove-Item esas.db
python -m bot
```

### При зависании

```powershell
# Найти процесс:
Get-Process python | Where-Object { $_.MainWindowTitle -eq "" }
# Завершить:
Stop-Process -Name python -Force
# Перезапустить:
python -m bot
```
