# Модуль: Безопасность и Middleware

Файл: `bot/core/middlewares.py`

Middlewares подключаются к `dp.message` и `dp.callback_query` в порядке:

```
outer → ErrorMiddleware → ThrottlingMiddleware → ActionLoggerMiddleware → хендлер
```

---

## 1. ActionLoggerMiddleware

**Назначение**: централизованная запись всех действий пользователя в `user_actions`.

**Что логируется:**

| Поле | Описание |
|---|---|
| `user_id` | Telegram ID пользователя |
| `state` | Текущее FSM-состояние |
| `action_type` | `command` / `text_input` / `button_click` / `location` / `contact` / `photo` / `document` |
| `payload` | Текст / callback_data (max 500 симв.) |
| `status` | `success` / `error` / `flood_attempt` |
| `error_context` | Traceback при ошибке |
| `bot_response` | Текст первого ответа бота (max 500 симв.) |
| `bot_response_type` | `text` или `inline` (тип клавиатуры в ответе) |
| `timestamp` | Время записи |

**Перехват ответов бота:** `_ResponseCapture` временно оборачивает `Bot.send_message` и `Bot.edit_message_text` через `unittest.mock.patch.object`. Обёртки устанавливаются перед вызовом хендлера и снимаются в `finally`.

**Уровень логирования:** Успешные действия пользователей записываются на уровне `DEBUG` (по умолчанию не отображаются в консоли). Ошибки записываются на уровне `ERROR`. Для просмотра пользовательских действий установите уровень логгера `bot.core.middlewares` в `DEBUG`. Все действия также сохраняются в таблицу `user_actions` в БД независимо от уровня логирования.

---

## 2. ThrottlingMiddleware

**Назначение**: защита от спама / перегрузки.

**Реализация:**

- Время последнего запроса каждого пользователя хранится в **Redis** (ключ `throttle:{user_id}`, TTL = 1 с).
- Минимальный интервал: `throttle_rate` из `config.yaml` (по умолчанию 0.2 сек).
- При перезапуске бота throttle-данные сохраняются.
- При нарушении:
  1. Запрос игнорируется.
  2. В `user_actions` пишется `status="flood_attempt"`.

**Реализовано:** ✅

---

## 3. ErrorMiddleware

**Назначение**: перехват непредвиденных исключений.

**Реализация:**

1. Любое исключение в хендлере перехватывается.
2. Traceback записывается в `error_context` записи `user_actions`.
3. Пользователь получает: «Произошла техническая ошибка, попробуйте позже».

**Реализовано:** ✅

---

## Безопасность данных

- **HTML-экранирование**: все пользовательские данные (имя, адрес, банковские реквизиты и т.д.) в форматированных сообщениях экранируются через `e()` из `bot/core/formatting.py` — обёртка над `html.escape()`. Это полностью устраняет `TelegramBadRequest: can't parse entities`. Оба бота используют `DefaultBotProperties(parse_mode=ParseMode.HTML)` — режим HTML надёжнее Markdown, так как только `<`, `>`, `&` требуют экранирования (крайне редки в русских названиях и адресах).
- **Единый модуль форматирования**: `bot/core/formatting.py` заменяет дублированные per-file `_md_escape()` функции. Импортируется как `from bot.core.formatting import e`.
- **Админ-доступ**: проверяется через `username.lower() in ADMIN_USERNAMES` — `set[str]` без `@`, загружаемый из env.
- **Токен**: жёстко разрешающаяся переменная `os.environ["BOT_TOKEN"]`.
- **Sheets sync**: приватная таблица через Service Account (JSON-ключ в `GOOGLE_SA_PATH`); ключ исключён через `.gitignore` (`*.json`).
- **Партнёрский бот**: каждый `ServiceOwner.service_id` привязан к `telegram_id`; все запросы проверяют `order.service_id == owner.service_id`.
- **SQLite**: локальный файл `esas.db`; для prod — сменить `DATABASE_URL` на PostgreSQL.
- **Ввод пользователя**: весь текстовый ввод проходит через Pydantic-валидацию.
