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

## Операционный контур

- `client_bot` и `partner_bot` работают поверх одной БД.
- `client_bot` и `partner_bot` триггерят full Sheets sync по факту commit в релевантных таблицах (`orders/users/services/service_drafts/service_bank_details`).
- `sync_service` на старте выполняет dry-run health-check и initial sync, затем обрабатывает delayed-retry очередь `sheets_retry_queue`.
