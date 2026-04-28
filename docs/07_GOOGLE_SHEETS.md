# Google Sheets Integration

## Транспорт и авторизация

- Используется только Service Account (`GOOGLE_SA_PATH`).
- CSV fallback отключен.
- Ключ таблицы: `GOOGLE_SHEET_ID`.

## Листы

- `Сервисы` — двусторонняя синхронизация сервисных профилей.
- `Реквизиты` — двусторонняя синхронизация банковских данных.
- `Заявки` — выгрузка заказов (write-back).
- `Клиенты` — выгрузка клиентской сводки (write-back).

## Ключевая контрактная точка

**ID сервиса обязателен**.

Синк и write-back выполняются по `service_id`, а не по имени сервиса.

## Inbound sync (Sheets -> DB)

- `sync_services_from_sheet()`:
    - upsert по `ID`,
    - дублирующиеся/битые `ID` пропускаются с warning-логом (без падения процесса),
    - неизвестная специализация/boolean/time/float обрабатывается через fallback к текущему значению,
    - неизвестная категория создаётся автоматически,
    - изменения зеркалируются в связанный `ServiceDraft`,
    - поддержка `dry_run=True` (валидация и расчёт изменений без коммита).
- `sync_bank_details_from_sheet()`:
    - upsert реквизитов по `ID`,
    - строки с `ID`, отсутствующим в `services`, пропускаются с warning-логом,
    - изменения реквизитов зеркалируются в связанный `ServiceDraft` (и при insert, и при update),
    - поддержка `dry_run=True` (без коммита).

## Outbound sync (DB -> Sheets)

- `update_service_row(service)` обновляет строку сервиса по ID.
- `set_service_available(service_id, available)` меняет только флаг доступности.
- `update_service_bank_row(service_id)` синхронизирует банковские реквизиты.
- Полные выгрузки:
    - `sync_all_orders_to_sheet()`
    - `sync_all_clients_to_sheet()`

## Поведение при отсутствии листа

Если лист отсутствует, он создаётся автоматически с корректными заголовками.

## Политика ошибок

- `first_run=True`: ошибка синка пробрасывается (fail-fast на запуске sync-service).
- Фоновый цикл: ошибки логируются, следующая попытка выполняется по интервалу.

## Health-check на запуске sync_service

- Перед initial sync вызывается `run_full_sync(first_run=True, dry_run=True)`.
- Успешный dry-run фиксируется логом `SYNC_HEALTH_OK`.
- В dry-run режиме outbound write-back (`Заявки`, `Клиенты`) не выполняется.
