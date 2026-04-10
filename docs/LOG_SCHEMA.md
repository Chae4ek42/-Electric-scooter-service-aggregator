# Схема логирования ESAS

Все лог-записи используют формат:
```
%(asctime)s | %(levelname)-8s | %(name)s | %(message)s
```

Структурированные записи имеют вид:
```
EVENT_TAG | key1=value1 | key2=value2
```

---

## Этапы запуска бота

| Этап | Logger | Уровень | Сообщение | Когда возникает |
|---|---|---|---|---|
| 1. БД | `bot.__main__` | INFO | `Initialising database …` | Всегда |
| 2. Миграции | `bot.services.seed` | INFO | `Migration: added <table>.<col>` | Только при первом запуске / новых колонках |
| 3. Seed | `bot.services.seed` | INFO | `Seeded N brands …` | Только при пустой БД |
| 4. Sheets sync | `bot.__main__` | INFO | `Синхронизация с Google Sheets …` | Всегда (при наличии GOOGLE_SHEET_ID) |
| 5. FSM storage | `bot.__main__` | INFO/WARN | `FSM storage: Redis (…)` или `Redis unavailable (…), using MemoryStorage` | Всегда |
| 6. Polling | `aiogram` | INFO | `Run polling for bot @…` | Всегда |

---

## Google Sheets — синхронизация (`bot.services.sheets_sync`)

### Чтение (sync_services_from_sheet)

| Тег | Уровень | Формат | Описание |
|---|---|---|---|
| `SYNC_START` | INFO | `columns_configured=N \| columns=…` | Начало синхронизации, какие столбцы настроены в SHEETS_COLUMNS |
| `SYNC_FETCHED` | INFO | `rows=N \| sheet_headers=M` | Успешная загрузка: количество строк и заголовков |
| `SYNC_COLUMNS_MISMATCH` | WARNING | `missing_in_sheet={…}` | Столбцы из SHEETS_COLUMNS отсутствуют в таблице |
| `SYNC_COLUMNS_EXTRA` | DEBUG | `extra_in_sheet={…}` | Столбцы в таблице, не указанные в SHEETS_COLUMNS |
| `SYNC_FETCH_ERR` | ERROR | `error_type=… \| error=…` | Ошибка загрузки таблицы (сеть, HTTP, SA) |
| `SYNC_RESULT` | INFO | `added=N \| updated=M \| unchanged=K \| skipped=S` | Итог: сколько записей добавлено, обновлено, без изменений, пропущено |

**Диагностические предупреждения (внутри цикла строк):**

| Уровень | Паттерн | Описание |
|---|---|---|
| WARNING | `неизвестная специализация «…» у «…»` | Значение столбца «Специализация» не в списке ремонт/апгрейд/комплекс |
| WARNING | `неверный рейтинг «…» у «…»` | Столбец «Рейтинг Я.Карты» не парсится как float |
| WARNING | `неверная стоимость диагностики «…» у «…»` | Столбец «Диагностика» не парсится как float |

### Запись (sheets_writer)

| Тег | Уровень | Формат | Описание |
|---|---|---|---|
| `SHEETS_WRITE` | INFO | `op=add \| service=… \| columns=N` | Добавлена новая строка |
| `SHEETS_WRITE` | INFO | `op=update \| service=… \| row=N` | Обновлена существующая строка |
| `SHEETS_WRITE_ERR` | ERROR | `op=add/update \| service=…` | Ошибка записи (+ traceback) |

---

## Фоновый цикл синхронизации

| Logger | Уровень | Сообщение | Описание |
|---|---|---|---|
| `bot.__main__` | ERROR | `Sheets sync error: …` | Ошибка в фоновой синхронизации (бот продолжает работу) |

---

## FSM Storage

| Logger | Уровень | Сообщение | Описание |
|---|---|---|---|
| `bot.__main__` | INFO | `FSM storage: Redis (redis://…)` | Redis доступен и используется |
| `bot.__main__` | WARNING | `Redis unavailable (…), using MemoryStorage` | Redis недоступен, состояния в памяти |

---

## Middleware

| Middleware | Logger | Уровень | Описание |
|---|---|---|---|
| `ErrorMiddleware` | `bot.core.middlewares` | ERROR | Необработанное исключение в хендлере |
| `ThrottlingMiddleware` | `bot.core.middlewares` | DEBUG | Throttle-блокировка пользователя |
| `ActionLoggerMiddleware` | `bot.core.middlewares` | — | Пишет в таблицу `user_actions`, не в лог |

---

## Платёжный цикл

| Logger | Уровень | Сообщение | Описание |
|---|---|---|---|
| `bot.__main__` | ERROR | `Payment expire loop error: …` | Ошибка при автоотмене просроченных заявок |

---

## Service Account (SA)

| Logger | Уровень | Паттерн | Описание |
|---|---|---|---|
| `bot.services.sheets_sync` | WARNING | `SA read failed: <ErrorType>: <msg>` | Ошибка чтения через gspread (невалидный ключ, нет доступа) |

---

## Как читать логи при ошибках

### Бот не стартует
1. Искать `ERROR` или `CRITICAL` в первых 20 строках вывода.
2. `SYNC_FETCH_ERR` → проблема с Google Sheets (сеть, SA-ключ, ID таблицы).
3. `ValueError: … 0 записей` → таблица пуста или столбец «Название» отсутствует.
4. `SYNC_COLUMNS_MISMATCH` → проверить `SHEETS_COLUMNS` в `.env`.

### Бот работает, но данные не синхронизируются
1. Искать `SYNC_RESULT` — если `added=0, updated=0` → данные не изменились.
2. `SYNC_COLUMNS_MISMATCH` → столбец переименован в таблице.
3. `Sheets sync error` в фоновом цикле → временная проблема сети.

### Запись в таблицу не работает
1. `SHEETS_WRITE_ERR` → смотреть traceback (обычно проблема SA-прав).
2. `Sheets write disabled` (DEBUG) → `GOOGLE_SA_PATH` или `GOOGLE_SHEET_ID` не задан.
