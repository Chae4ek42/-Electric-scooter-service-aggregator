# Google Sheets Integration

## Транспорт и авторизация

- Используется только Service Account (`GOOGLE_SA_PATH`).
- CSV fallback отключен.
- Ключ таблицы: `GOOGLE_SHEET_ID`.

## Листы

- `Сервисы` — write-only выгрузка данных из БД.
- `Реквизиты` — write-only выгрузка данных из БД.
- `Заявки` — выгрузка заказов (write-back).
- `Клиенты` — выгрузка клиентской сводки (write-back).
- `Метрики сервисов` — выгрузка бизнес-метрик по каждому сервису (write-back).

## Ключевая контрактная точка

**ID сервиса обязателен**.

Синк и write-back выполняются по `service_id`, а не по имени сервиса.

## Inbound sync (Sheets -> DB)

- Отключён политикой write-only.
- Бот не читает данные из Google Sheets для изменения локальной БД.
- `sync_services_from_sheet()` и `sync_bank_details_from_sheet()` сохранены только для обратной совместимости и возвращают `0` без чтения листов.

## Outbound sync (DB -> Sheets)

- `update_service_row(service)` обновляет строку сервиса по ID.
- `set_service_available(service_id, available)` меняет только флаг доступности.
- `update_service_bank_row(service_id)` синхронизирует банковские реквизиты.
- Полные выгрузки:
    - `sync_all_orders_to_sheet()`
    - `sync_all_clients_to_sheet()`
    - `sync_all_service_metrics_to_sheet()`

### Режим запуска

- Full sync запускается event-driven: после коммитов SQLAlchemy по релевантным моделям (`Order`, `User`, `Service`, `ServiceDraft`, `ServiceBankDetails`).
- Используется debounce-окно (`SHEETS_SYNC_DEBOUNCE_SECONDS`), чтобы не запускать лишние полные выгрузки при каскадных изменениях в одном сценарии.
- Периодический full-sync по интервалу больше не используется.

## Бизнес-метрики сервиса (лист `Метрики сервисов`)

Для каждого сервиса выгружаются KPI:

- общее количество заявок,
- количество `paid`, `completed`, заявок в рабочем контуре,
- негативные исходы (`cancelled/rejected_by_partner/client_refused/interrupted/disputed`),
- конверсия в работу и конверсия в завершение,
- выручка по `completed`,
- средний и медианный чек,
- средняя длительность цикла до `completed` (в часах).

## Поведение при отсутствии листа

Если лист отсутствует, он создаётся автоматически с корректными заголовками.

## Политика ошибок

- Любой неуспешный full sync попадает в `sheets_retry_queue` (операция `sync_full`).
- Retry выполняется с exponential backoff через `next_retry_at` и ограничением по числу попыток.
- Обработчик очереди работает в `sync_service` (`SheetsRetryWorker`).
- При превышении лимита попыток элемент удаляется из очереди с warning-логом.

## Health-check на запуске sync_service

- Перед initial sync вызывается `run_full_sync(first_run=True, dry_run=True)`.
- Успешный dry-run фиксируется логом `SYNC_HEALTH_OK` (write-only режим).
- В dry-run режиме outbound write-back (`Заявки`, `Клиенты`) не выполняется.

## Колонки листа `Заявки`

- В write-back добавлен столбец `Код заявки` (6-значный order code).
