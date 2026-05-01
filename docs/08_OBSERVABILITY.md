# Логирование: файловый режим

Проект работает без Grafana/Loki/Promtail. Все сервисы пишут логи в файлы с ротацией.

## Что пишет каждый сервис

Для каждого процесса (`client-bot`, `partner-bot`, `sync-service`) создаются 2 файла:

- `data/logs/<service>.app.log` — обычные операционные логи.
- `data/logs/<service>.business.log` — бизнес-события (`logger` с префиксом `esas.business...`).

Примеры:

- `data/logs/client-bot.app.log`
- `data/logs/client-bot.business.log`

## Ротация

- Максимальный размер файла: `30 MB`.
- Количество архивов: управляется `LOG_FILE_BACKUPS` (по умолчанию `5`).

Параметры через env:

- `LOG_DIR=/app/data/logs`
- `LOG_FILE_MAX_MB=30`
- `LOG_FILE_BACKUPS=5`

## Формат

- `LOG_FORMAT=text` или `LOG_FORMAT=json`.
- Во всех записях доступен контекст: `request_id`, `user_id`, `chat_id`, `service`.

## Регистрация ошибок

- Ошибки пользовательских апдейтов регистрируются в `user_actions` с `action_type=system_error`.
- Для фоновых задач используется guarded-запуск, который:
    - пишет exception в лог,
    - сохраняет событие в `user_actions`.
- На уровне процесса включены runtime hooks:
    - `asyncio_unhandled_exception`,
    - `process_unhandled_exception`,
    - `thread_unhandled_exception`.

Это позволяет расследовать инциденты даже если ошибка произошла вне обычного handler-потока.

## Быстрые команды

Self-check инфраструктуры (доступно админам в client/partner bot):

```text
/health
```

Команда проверяет БД, Redis и Google Sheets. При деградации пишет событие в `user_actions` с `action_type=healthcheck_degraded`.

Просмотр последних строк:

```powershell
Get-Content .\data\logs\client-bot.app.log -Tail 200
```

Просмотр бизнес-событий:

```powershell
Get-Content .\data\logs\sync-service.business.log -Tail 200
```

Поиск ошибок:

```powershell
Select-String -Path .\data\logs\*.app.log -Pattern "ERROR"
```
