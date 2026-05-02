# Инфраструктура и База Данных

## Технологии

- aiogram
- SQLAlchemy (async)
- PostgreSQL (основной runtime), SQLite (локальный fallback)
- Redis (FSM/throttling, с fallback)
- gspread + Google Service Account
- Ротационные файловые логи (`data/logs/*.app.log`, `data/logs/*.business.log`)

## Основные таблицы

- `services`: финальный рабочий профиль сервиса (каталог, доступность, цены, график).
- `service_drafts`: анкета и статус владельца (`owner_user_id`, `service_id`, draft-поля).
- `service_bank_details`: отдельные банковские реквизиты по `service_id`.
- `service_owner_settings`: настройки уведомлений владельца.
- `admin_notification_settings`: настройки уведомлений администраторов по scope (`client`/`partner`).
- `orders`: жизненный цикл клиентских заявок.
- `order_status_history`: аудит переходов статусов заказа (`from_status`, `to_status`, `actor`, `reason`, `metadata_json`).
- `sheets_retry_queue`: очередь отложенных ретраев write-back в Google Sheets.
- `schema_versions`: версия применённых миграций схемы.
- `users`, `brands`, `models`, `service_categories`, `metro_stations`, `user_actions`.

## Бизнес-слой переходов статусов

- Все изменения `Order.status` проходят через `client_bot.services.order_lifecycle.transition_order_status`.
- Сервис валидирует допустимость перехода и создаёт запись в `order_status_history`.
- Отдельный batch-сценарий `cancel_expired_awaiting_payment` переводит просроченные заявки в `cancelled` с причиной `payment_timeout`.

## Индексы для рабочих выборок

- `orders(service_id, status, created_at)`
- `orders(user_id, status, created_at)`
- `service_drafts(status, registration_complete)`

## Принцип разделения данных

- `ServiceDraft` — рабочий контур модерации и редактирования анкеты.
- `Service` — производственный контур выполнения заказов.
- Связь осуществляется через `service_id`.

## Матрица уведомлений

- `service_owner_settings`:
    - master-switch `notif_enabled`;
    - события владельца: `notif_new_order`, `notif_cancel`, `notif_client_comment`, `notif_estimate`, `notif_dispute`, `notif_completed`.
- `admin_notification_settings`:
    - ключ `(admin_user_id, scope)`;
    - `scope=client`: `notif_client_dispute`, `notif_client_cancel`, `notif_no_center`, `notif_order_completed`;
    - `scope=partner`: `notif_partner_application`, `notif_partner_profile_update`, `notif_partner_status_change`.

## Миграции и совместимость

`init_db()` делает:

1. `create_all`.
2. Точечные `ALTER TABLE` для старых БД.
3. Перенос legacy draft-данных в `service_drafts` и `service_bank_details`.
4. Пересборку legacy-таблицы `services` в каноничную схему, чтобы убрать устаревшие owner/draft-колонки.
5. Создание `order_status_history` и индексов `orders` / `service_drafts` для совместимости старых инсталляций.
6. Создание `schema_versions` и фиксация применённых версий миграций.
7. Приведение `sheets_retry_queue` к схеме delayed-retry (next_retry_at/last_error).
8. Приведение схем уведомлений:
    - расширение `service_owner_settings` новыми флагами;
    - создание `admin_notification_settings` и индекса по `scope`.

## Доступ к БД

- Асинхронный путь: `async_session`.
- Синхронный путь для отдельных write-back операций Sheets: `sync_engine`.

## Миграция legacy SQLite в Postgres

Если у тебя осталась старая база `data/esas.db`, её можно перенести в текущий Postgres-контур одноразовым скриптом `scripts/migrate_sqlite_to_postgres.py`.

Скрипт делает два шага:

1. По умолчанию сначала прогоняет legacy SQLite через текущие inline-миграции (`init_db()`), то есть приводит старую SQLite-схему к актуальному виду.
2. Затем копирует данные в Postgres по всем таблицам актуальной схемы и восстанавливает sequence для `id`.

### Важно перед запуском

- Останови боты и sync-service, чтобы в SQLite больше никто не писал во время переноса.
- Целевой Postgres должен быть пустым. Не поднимай `client-bot`, `partner-bot` и `sync-service` на пустом Postgres до миграции, иначе они создадут схему и могут засеять справочники раньше времени.
- Для самого переноса используй URL вида `postgresql+psycopg://...`, а не runtime-URL `postgresql+asyncpg://...`: скрипт работает через синхронный SQLAlchemy engine.
- В текущем Docker-образе папка `scripts/` не копируется внутрь образа, поэтому на сервере без venv удобнее запускать миграцию через одноразовый контейнер с bind mount проекта в `/app`.

### Рекомендуемый сценарий на сервере через Docker Compose

1. Сделай резервную копию SQLite-файла:

```bash
cp data/esas.db data/esas.db.bak.$(date +%F_%H-%M-%S)
```

2. Останови сервисы приложения, чтобы зафиксировать данные:

```bash
docker compose stop client-bot partner-bot sync-service
```

3. Подними только Postgres и дождись, пока он станет healthy:

```bash
docker compose up -d postgres
docker compose ps
```

4. Запусти перенос через одноразовый контейнер на базе `client-bot`, примонтировав текущий проект в `/app`:

```bash
docker compose run --rm --no-deps -v "$(pwd):/app" client-bot \
    python /app/scripts/migrate_sqlite_to_postgres.py \
    --source-db /app/data/esas.db \
    --target-url postgresql+psycopg://esas:esas@postgres:5432/esas
```

Ожидаемое поведение:

- в stdout появятся строки вида `orders: copied 123 rows`;
- в конце появится `Migration completed successfully.`.

5. Проверь, что данные действительно появились в Postgres:

```bash
docker compose exec postgres psql -U esas -d esas -c "SELECT COUNT(*) FROM users;"
docker compose exec postgres psql -U esas -d esas -c "SELECT COUNT(*) FROM orders;"
docker compose exec postgres psql -U esas -d esas -c "SELECT COUNT(*) FROM services;"
```

6. После успешного переноса подними runtime-сервисы уже на Postgres:

```bash
docker compose up -d redis client-bot partner-bot sync-service
```

7. Проверь логи старта:

```bash
docker compose logs -f client-bot partner-bot sync-service
```

8. Когда убедишься, что всё работает, сделай уже backup самого Postgres:

```bash
docker compose exec -T postgres pg_dump -U esas -d esas > esas_postgres_after_migration.sql
```

### Когда нужен `--skip-normalize`

Если source-SQLite уже была однажды прогнана через текущие inline-миграции и переписывать её повторно не нужно, можно пропустить первый шаг:

```bash
docker compose run --rm --no-deps -v "$(pwd):/app" client-bot \
    python /app/scripts/migrate_sqlite_to_postgres.py \
    --source-db /app/data/esas.db \
    --target-url postgresql+psycopg://esas:esas@postgres:5432/esas \
    --skip-normalize
```

### Локальный запуск, если есть venv

Если перенос делается не на сервере, а локально и рядом есть Python/venv, можно запустить тот же скрипт напрямую:

```powershell
.\.venv\Scripts\python.exe .\scripts\migrate_sqlite_to_postgres.py --target-url postgresql+psycopg://esas:esas@localhost:5432/esas
```

### Типовые проблемы

- Если получаешь ошибки duplicate key / unique violation, значит целевой Postgres уже не пустой. Очисти его и повтори перенос только в чистую БД.
- Если запускаешь скрипт без bind mount проекта в `/app`, контейнер не увидит `scripts/migrate_sqlite_to_postgres.py`.
- Если по ошибке подставить `postgresql+asyncpg://...` в `--target-url`, перенос может упасть, потому что этот скрипт использует синхронный движок SQLAlchemy.

## Операционный контур

- `client_bot` и `partner_bot` работают поверх одной БД.
- `client_bot` и `partner_bot` триггерят full Sheets sync по факту commit в релевантных таблицах (`orders/users/services/service_drafts/service_bank_details`).
- `sync_service` на старте выполняет dry-run health-check и initial sync, затем обрабатывает delayed-retry очередь `sheets_retry_queue`.
