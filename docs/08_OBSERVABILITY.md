# Observability: Loki + Promtail + Grafana

This setup provides centralized logs for all Docker containers, including parsed fields from ESAS JSON logs (`request_id`, `user_id`, `chat_id`, `service`, `level`).

## Services

- `loki`: log storage and query backend
- `promtail`: log collector from Docker `json-file` logs
- `grafana`: UI for exploring logs

These services are under Compose profile `observability`, so they do not affect the default app startup.

## Start

```powershell
docker compose --profile observability up -d
```

Or rebuild app images and run everything together:

```powershell
docker compose --profile observability up -d --build
```

## Access

- Grafana: `http://localhost:3000`
- Login: `admin`
- Password: value from `GRAFANA_ADMIN_PASSWORD` (fallback: `admin`)

Loki datasource is provisioned automatically.

## Provisioned Dashboard

Grafana provisions dashboard automatically:

- Dashboard: `ESAS Logs Overview`
- UID: `esas-logs-overview`
- Folder: `ESAS`

Main widgets:

- Events per minute by service
- Total ERROR in 5m
- Live log stream
- Request flow filtered by `request_id` regex

## Provisioned Alerts

Grafana provisions alert rules automatically (folder `ESAS Alerts`):

1. `ESAS errors spike per service`
: Fires when any service has more than 5 `ERROR` logs in 5 minutes (`for: 2m`).

2. `ESAS total errors spike`
: Fires when all ESAS services together have more than 20 `ERROR` logs in 5 minutes (`for: 2m`).

Alert rules file:

- `observability/grafana/provisioning/alerting/esas-rules.yaml`

## Useful LogQL queries

All ESAS app logs:

```logql
{service=~"client-bot|partner-bot|sync-service"}
```

Only errors:

```logql
{service=~"client-bot|partner-bot|sync-service", level="ERROR"}
```

Specific request flow:

```logql
{request_id="<request_id>"}
```

Specific user:

```logql
{user_id="<telegram_user_id>"}
```

## Troubleshooting

If no logs appear in Grafana:

1. Check collector health:

```powershell
docker compose --profile observability logs -f promtail
```

2. Check Loki availability:

```powershell
curl http://localhost:3100/ready
```

3. Ensure app services produce JSON logs (`LOG_FORMAT=json`).

4. If dashboard/alerts do not appear after changes, force-recreate Grafana:

```powershell
docker compose --profile observability up -d --force-recreate grafana
```
