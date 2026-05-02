# Observability и Prometheus: подробное руководство

Этот документ описывает наблюдаемость ESAS на практике: что именно метрим, как поднимать Prometheus, как проверять сбор данных, какие запросы использовать для диагностики и как развивать мониторинг без регрессий.

## 1. Что входит в observability проекта

В проекте есть два рабочих контура:

- файловые логи с ротацией,
- метрики Prometheus (HTTP exporter внутри каждого сервиса).

Метрики поднимаются в каждом процессе при старте:

- `client-bot` (порт по умолчанию `9101`),
- `partner-bot` (порт по умолчанию `9102`),
- `sync-service` (порт по умолчанию `9103`).

Prometheus собирает их через `observability/prometheus.yml`.

## 2. Архитектура мониторинга в текущей поставке

Схема потока:

1. Сервисы публикуют `/metrics` через `prometheus_client.start_http_server(...)`.
2. Контейнер `prometheus` скрейпит три таргета (`client-bot`, `partner-bot`, `sync-service`) каждые 15 секунд.
3. Alert Rules берутся из `observability/alerts.yml` и вычисляются самим Prometheus.

Важно: в репозитории нет отдельного Alertmanager-контейнера, поэтому алерты видны в UI Prometheus (страница Alerts), но внешняя доставка (Telegram/Email/Webhook) требует отдельной интеграции Alertmanager.

## 3. Конфигурация exporter внутри сервисов

Базовая логика в `client_bot/core/metrics.py`:

- `METRICS_ENABLED`:
    - `1/true/yes/on` -> exporter включен,
    - `0/false/no/off` -> exporter выключен.
- `METRICS_HOST` (по умолчанию `0.0.0.0`).
- `METRICS_PORT` (если не задан, берется `default_port` сервиса).

Если `prometheus_client` недоступен, сервис стартует без exporter и пишет warning в лог.

## 4. Текущая метрика-модель ESAS

Собственные метрики проекта:

- `esas_runtime_errors_total{action_type=...}`
- `esas_sheets_sync_total{source=..., result=success|failure}`
- `esas_sheets_retry_enqueued_total{operation=...}`
- `esas_sheets_retry_processed_total{operation=..., result=...}`
- `esas_sheets_retry_queue_size`

Плюс стандартные метрики python/process из `prometheus_client` (`process_cpu_seconds_total`, `process_resident_memory_bytes`, и т.д.).

## 5. Файлы конфигурации Prometheus

### 5.1 `observability/prometheus.yml`

Текущие параметры:

- `scrape_interval: 15s`
- `evaluation_interval: 15s`
- `rule_files: /etc/prometheus/alerts.yml`
- таргеты:
    - `client-bot:9101`
    - `partner-bot:9102`
    - `sync-service:9103`

### 5.2 `observability/alerts.yml`

Активные правила:

- `EsasSheetsSyncFailure`
    - срабатывает, если за последние 10 минут есть хотя бы один `failure` в `esas_sheets_sync_total`,
    - `for: 5m`.
- `EsasSheetsRetryQueueHigh`
    - срабатывает, если `esas_sheets_retry_queue_size > 50` в течение 10 минут.
- `EsasRuntimeErrorsBurst`
    - срабатывает, если за 5 минут накопилось больше 5 runtime-errors,
    - `for: 2m`.

## 6. Быстрый запуск мониторинга (Docker Compose)

1. Поднять стек:

```powershell
docker compose up -d --build
```

2. Проверить, что exporter доступны в сети compose:

```powershell
docker compose ps
```

3. Открыть Prometheus UI:

```text
http://localhost:9090
```

4. Проверить состояния таргетов:

- Status -> Targets
- Должно быть `UP` для `client-bot`, `partner-bot`, `sync-service`.

## 7. Проверка метрик руками

Примеры запросов в Prometheus (таб Query):

```promql
up
```

```promql
up{job="client-bot"}
```

```promql
sum by (job) (up)
```

```promql
increase(esas_sheets_sync_total{result="failure"}[1h])
```

```promql
sum by (source, result) (increase(esas_sheets_sync_total[30m]))
```

```promql
topk(10, increase(esas_runtime_errors_total[1h]))
```

```promql
esas_sheets_retry_queue_size
```

```promql
rate(process_cpu_seconds_total[5m])
```

```promql
process_resident_memory_bytes
```

## 8. Практические диагностики (готовые рецепты)

### 8.1 sync-service часто падает на интеграциях

Смотри:

- `increase(esas_runtime_errors_total{action_type="sync_service_runtime_error"}[30m])`
- `increase(esas_sheets_sync_total{source="startup:sync-service",result="failure"}[30m])`
- `esas_sheets_retry_queue_size`

Илллюстрация: если `queue_size` растет и есть стабильный `failure`, проблема обычно в доступности Google API или авторизации Service Account.

### 8.2 Клиентский/партнерский бот жив, но не пишет бизнес-действия

Проверки:

- `up{job="client-bot"}` или `up{job="partner-bot"}`,
- логи `*.app.log` и `*.business.log`,
- self-check через `/health` в соответствующем боте.

### 8.3 Всплеск ошибок в пользовательском сценарии

Проверки:

- `increase(esas_runtime_errors_total[5m])`
- разрез по `action_type`: `sum by (action_type) (increase(esas_runtime_errors_total[5m]))`

Дальше ищи корреляцию по timestamp в `data/logs/<service>.app.log`.

## 9. Как безопасно добавлять новые метрики

Рекомендуемый шаблон:

1. Добавить новую Counter/Gauge в `client_bot/core/metrics.py`.
2. Добавить helper-функцию (`mark_...`, `set_...`) рядом с существующими.
3. Вызвать helper в бизнес-сценарии, где есть стабильный момент фиксации события.
4. Добавить/обновить PromQL-запросы в документации.
5. При необходимости добавить alert rule с понятным `for` и неагрессивным порогом.

Антипаттерны:

- считать attempt вместо финального результата,
- использовать слишком высокую кардинальность labels (например, `user_id`, `order_id`),
- добавлять дублирующие метрики с разными именами для одного события.

## 10. Алертинг: базовые принципы настройки порогов

Для текущих метрик:

- `runtime_errors`: лучше считать через `increase(...[window])`, а не абсолютное значение.
- `retry_queue_size`: использовать `for`, чтобы отсеять короткие всплески.
- `sync_failure`: обязательно смотреть в связке с `success`, иначе можно поймать ложную тревогу при кратком флапе.

Если добавляешь новое правило:

1. сначала проверить запрос как график в Prometheus минимум на час исторических данных,
2. убедиться, что правило не срабатывает в нормальном рабочем режиме,
3. только потом включать в `alerts.yml`.

## 11. Логи и метрики вместе

Для расследований всегда комбинируй:

- метрики для обнаружения аномалии,
- логи для root cause.

Часто полезные команды:

```powershell
Get-Content .\data\logs\client-bot.app.log -Tail 200
```

```powershell
Get-Content .\data\logs\partner-bot.app.log -Tail 200
```

```powershell
Get-Content .\data\logs\sync-service.business.log -Tail 200
```

```powershell
Select-String -Path .\data\logs\*.app.log -Pattern "ERROR|Exception|traceback"
```

## 12. Self-check как оперативный health endpoint для админов

Команда в client/partner боте:

```text
/health
```

Проверяет DB/Redis/Sheets и возвращает агрегированный статус. При деградации пишет событие в `user_actions` (например, `healthcheck_degraded`).

## 13. Короткий runbook по инциденту

1. Проверить `up` по всем job.
2. Проверить Alerts в Prometheus.
3. Проверить `increase(esas_runtime_errors_total[5m])` и топ `action_type`.
4. Проверить `esas_sheets_retry_queue_size`.
5. Сопоставить с логами и временными метками.
6. После фикса убедиться, что:
    - `up == 1`,
    - очередь retry стабилизируется,
    - алерты переходят в resolved.

## 14. Что улучшать дальше (backlog)

- вынести Alertmanager в compose и подключить каналы доставки (Telegram/Webhook),
- добавить SLI/SLO (доступность ботов, скорость обработки очереди ретраев),
- добавить recording rules для часто используемых PromQL,
- расширить дашборды Grafana по задержкам и ошибкам пользовательских сценариев.
