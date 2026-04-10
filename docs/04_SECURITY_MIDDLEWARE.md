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

**Реализовано:** ✅ Middleware перехватывает `Message` и `CallbackQuery`. В лог выводится подробная строка: `user_id`, `@username`, `action`, `state`, `payload`, `status`, `elapsed_ms`, `response_type`.

---

## 2. ThrottlingMiddleware

**Назначение**: защита от спама / перегрузки.

**Реализация:**

- Время последнего запроса каждого пользователя хранится в **Redis** (ключ `throttle:{user_id}`, TTL = 1 с).
- Минимальный интервал: `THROTTLE_RATE = 0.2` сек.
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

- **Markdown-экранирование**: все пользовательские данные (имя, адрес, банковские реквизиты и т.д.) в форматированных Markdown-сообщениях экранируются через `_md_escape()` (символы `\`, `*`, `_`, `` ` ``, `[`). Это предотвращает `TelegramBadRequest: can't parse entities`.
- **Админ-доступ**: проверяется через `username.lower() in ADMIN_USERNAMES` — `set[str]` без `@`, загружаемый из env.
- **Токен**: жёстко разрешающаяся переменная `os.environ["BOT_TOKEN"]`.
- **Sheets sync**: приватная таблица через Service Account (JSON-ключ в `GOOGLE_SA_PATH`); ключ исключён через `.gitignore` (`*.json`).
- **Партнёрский бот**: каждый `ServiceOwner.service_id` привязан к `telegram_id`; все запросы проверяют `order.service_id == owner.service_id`.
- **SQLite**: локальный файл `esas.db`; для prod — сменить `DATABASE_URL` на PostgreSQL.
- **Ввод пользователя**: весь текстовый ввод проходит через Pydantic-валидацию.
