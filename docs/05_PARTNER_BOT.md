# Партнёрский бот (Бот для владельцев сервисов)

> **Статус:** Проектный документ — требует согласования перед реализацией.  
> Существующий клиентский бот далее именуется **client-bot**. Новый бот — **partner-bot**.

---

## 1. Концепция и цель

Partner-bot — Telegram-бот для владельцев / операторов сервисных центров.  
Решает три задачи:

1. **Самостоятельная регистрация** сервиса в каталоге без участия администратора ESAS.
2. **Оперативное управление заявками** — получение уведомлений, принятие/отклонение, маркировка выполнения.
3. **Редактирование собственного профиля** в каталоге с двусторонней синхронизацией с Google Sheets.

Оба бота работают на одной БД, одном коде бизнес-логики и одном Google Sheets документе.

---

## 2. Роли и уровни доступа

| Роль | Описание | Откуда берётся |
|---|---|---|
| `pending` | Зарегистрирован, ждёт одобрения | После `finish_registration` |
| `active` | Одобрен, работает с заявками | Admin одобряет вручную или через admin-bot |
| `suspended` | Временно заблокирован | Admin вручную |
| `rejected` | Заявка отклонена | Admin вручную |

Только `active`-владелец видит заявки и может редактировать профиль.  
`pending` видит только экран ожидания с кнопкой «Изменить данные».

---

## 3. Онбординг и регистрация

### 3.1 FSM `RegistrationFSM`

```
/start
  └─► (уже зарегистрирован?) ──Yes──► главное меню
            │
           No
            ▼
        reg_name          ← «Введите название вашего сервисного центра»
            ▼
        reg_service_type  ← inline: [Ремонт / Апгрейд / Комплексный]
            ▼
        reg_service_category  ← inline: [Электрика / Механика]
            ▼
        reg_hydroisolation  ← inline: [Да / Нет] — выполняем гидроизоляцию?
            ▼
        reg_address       ← Адрес»
            ▼
        reg_metro         ← «Ближайшее метро» (fuzzy, как в client-bot)
            ▼
        reg_phone         ← «Контактный телефон» (валидация формата, повторный запрос при неверном формате)
            ▼
        reg_telegram      ← «Telegram-аккаунт или канал» (необязательно, инлайн кнопка для пропуска)
            ▼
        reg_hours         ← «Время работы» формат HH:MM–HH:MM (08:00–22:00)
            ▼
        reg_diagnostics   ← «Стоимость диагностики (руб.)» (0 = бесплатно)
            ▼
        reg_diag_included ← inline: [Входит в стоимость / Оплачивается отдельно]
            ▼
        reg_confirm       ← карточка с итоговыми данными + [Отправить / Заново]
            ▼
        (создаётся ServiceOwner + запись Service со статусом is_available=False)
        (уведомление администратору ESAS в admin-bot/admin.py)
            ▼
        waiting_approval  ← «Ваша заявка принята, ожидайте одобрения»
```
Дополнительно на каждом этапе сохранять состояние и в случае прерывания возвращаться к текущему этапу. Должны также в текстовой клавиатуре быть кнопки моя анкета, продолжить заполнение (в случае если оно было начато и не закончено. В противном случае кнопка не отображается), изменить анкету. При изменении анкеты выводится ее текущее состояние и под ним в инлайн кнопках варианты полей для изменения. При /start высылается приветственное сообщение с инлайн кнопкой зарегестрировать сервис. Все эти поля существуют тоьлко пока эта анкета не одобрена админом
### 3.2 Валидация при регистрации

| Поле | Правило |
|---|---|
| Название | 3–200 символов, хотя бы одна буква |
| Адрес | 10–400 символов |
| Телефон | `\+?[0-9\- ]{7,20}` |
| Telegram | `@?[a-zA-Z0-9_]{5,32}` или `/skip` |
| Время работы | regex `^\d{2}:\d{2}[-–]\d{2}:\d{2}$`, open < close |
| Цена диагностики | целое число ≥ 0 |

### 3.3 Что происходит при одобрении

1. Admin в admin-bot нажимает «Одобрить» (inline callback).
2. `ServiceOwner.status` → `active`, `Service.is_available` → `True`.
3. Владелец получает уведомление: «Ваш сервис одобрен и добавлен в каталог».
4. Google Sheets: строка сервиса добавляется/обновляется через Sheets API.
5. Следующий цикл `sheets_sync` (read) увидит её и подтвердит консистентность.

---

## 4. Главное меню (активный владелец)

```
╔══════════════════════════════╗
║  ESAS Partner — [Имя сервиса]║
╠══════════════════════════════╣
║  📋 Входящие заявки   (3 новых)
║  📊 История и статистика
║  ✏️  Редактировать профиль
║  🔔 Настройки уведомлений
║  📄 Мой статус в каталоге
║  ❓  Помощь
╚══════════════════════════════╝
```

---

## 5. Управление заявками

### 5.1 Входящие заявки

При нажатии «Входящие заявки» партнёр видит список непринятых заявок:

```
📋 Входящих заявок: 3

#1042 · iPhone 15 Pro · Ремонт
📅 12 апр · 14:00 · м. Проспект Мира
[👁 Подробнее]

#1038 · Xiaomi 14 · Апгрейд — Гидроизоляция
📅 11 апр · 11:00 · м. Чистые пруды
[👁 Подробнее]
```

Карточка заявки:
```
Заявка #1042
─────────────────
Устройство:   Apple iPhone 15 Pro
Тип услуги:   Ремонт
Проблема:     Не заряжается, стекло треснуто
Дата/время:   12 апреля · 14:00
Метро:        м. Проспект Мира (~350 м от вас)
Диагностика:  550 руб.
─────────────────
[✅ Принять]  [❌ Отклонить]  [💬 Написать клиенту]
В случае принятия заявки ее статус в бд меняется (Посмотри кстати текущие возможные статусы и добавь новые если необходимо), и клиенту высылается сообщение о том, что сервис принял заявку. В случае отклонения у сервиса требуется указать причину в сообщении ниже и также отправляется уведомление клиенту с причиной. Написать клиенту - ссылка на чат с клиентом. Убери поле метро и диагностика. Эмодзи убери тоже все
```

### 5.2 Жизненный цикл заявки (со стороны партнёра)

```
awaiting_payment
   ▼  (клиент оплатил диагностику)
new_for_partner       ← партнёр видит кнопку «Принять»
   ▼
accepted_by_partner   ← клиент видит имя сервиса, получает адрес
   ▼
in_progress           ← партнёр нажал Принять»
   ▼
completed             ← партнёр нажал Завершен»

     ─── или ───

rejected_by_partner   ← партнёр отклонил (клиент получает уведомление + автоподбор другого сервиса)
```

> Расширенный статусный граф относительно client-bot: добавляются `new_for_partner`, `in_progress`, `ready`, `rejected_by_partner`.

### 5.3 Действия партнёра в заявке

| Действие | Когда доступно | Результат |
|---|---|---|
| Принять | `new_for_partner` | → `accepted_by_partner`, клиент получает карточку сервиса |
| Отклонить | `new_for_partner` | → `rejected_by_partner`, клиент видит «сервис отказал» + повторный подбор |
| Написать клиенту | любой статус | Deep-link или Telegram username клиента (если разрешил) |
| Устройство принято | `accepted_by_partner` | → `in_progress` |
| Не пришёл | `accepted_by_partner` спустя N часов | → `interrupted` |
| Добавить комментарий | любой статус | сохраняется в `Order.partner_comment` |

### 5.4 История заявок

Фильтры: «Все», «Принятые», «Выполненные», «Отклонённые», «За неделю / месяц».  
Пагинация: по 10 заявок на страницу с inline «← Назад / →Далее».

---

## 6. Редактирование профиля

Позволяет менять любое поле сервиса без обращения к администратору ESAS.  
После каждого изменения:
1. Запись обновляется в локальной БД.
2. `sheets_writer.update_service_row()` перезаписывает строку в Google Sheets.
3. Логируется в `user_actions` с `action_type="partner_profile_edit"`.

### 6.1 Редактируемые поля

```
✏️ Редактировать профиль

Название           Samsung Repair Pro
Тип услуг          Ремонт + Апгрейд (Комплексный)
Адрес              Москва, ул. Марксистская, 5
Ближайшее метро    м. Марксистская
Телефон            +7 (999) 123-45-67
Telegram           @samsungrepair
Время работы       09:00–21:00
Гидроизоляция      Да
Диагностика        400 руб. · Входит в стоимость
Статус             Активен

[Изменить] (выбор поля → inline-кнопки)
```

### 6.2 Быстрые статусы (одна кнопка)

| Кнопка | Действие |
|---|---|
| 🔴 Закрыть сегодня | `is_available=False` до 23:59, auto-reset в полночь |
| 🟢 Открыть сейчас | `is_available=True` немедленно |
| 🔧 На техобслуживании | `is_available=False` с причиной "обслуживание" |

### 6.3 Управление расписанием отдельных дней

- Партнёр может заблокировать конкретные даты (отпуск, праздники).
- Хранится в новой таблице `service_blocked_dates (service_id, date)`.
- client-bot при ранжировании фильтрует даты из этой таблицы.

## 8. Уведомления

### 8.1 Типы событий → партнёру

| Событие | Текст уведомления |
|---|---|
| Новая заявка | «📩 Новая заявка #N · iPhone 15 · Ремонт · 14 апр 14:00» + кнопки [Принять][Отклонить] |
| Клиент отменил | «❌ Клиент отменил заявку #N» |
### 8.2 Настройки уведомлений

Партнёр может отключить каждый тип отдельно:

```
🔔 Настройки уведомлений

✅ Новые заявки
✅ Отмены клиентом
```

Хранится в `ServiceOwnerSettings (owner_id, notif_new_order, notif_cancel, notif_payment, notif_reminder, notif_weekly_report)`.

## 9. Двусторонняя синхронизация с Google Sheets

Изменить полностью логику обработки гугл таблиц. Таблица будет приватная и нужно по прежнему читать ее и обновлять. Сам полностью реализуй эту логику

### 9.4 Стратегия консистентности

```
partner-bot изменил поле
       │
       ▼
local DB update (сразу)
       │
       ▼
sheets_writer.update_service_row() (async, best-effort)
       │
    success? ──No──► job в очереди retry_queue (SQLite таблица)
       │                   ▲
      Yes                  │
       │          sheets_sync (read) каждые 5 мин
       │          сравнивает Sheets со snapshot → если рассинхрон → retry
       ▼
Sheets updated
```

`retry_queue` таблица: `(id, service_id, payload_json, attempts, last_attempt_at, created_at)`.

---

## 10. Архитектура монорепо (shared services)

### 10.1 Предлагаемая структура

```
ESAS/
├── shared/                        ← общий код, не зависит от бота
│   ├── core/
│   │   ├── config.py              (расширяется — добавляются PARTNER_BOT_TOKEN и др.)
│   │   ├── database.py
│   │   └── middlewares.py
│   ├── domain/
│   │   ├── models.py              (добавляются ServiceOwner, OwnerSettings, BlockedDate)
│   │   ├── states.py              (ClientFSM + PartnerRegistrationFSM + PartnerOrderFSM)
│   │   └── schemas.py
│   └── services/
│       ├── metro_graph.py
│       ├── metro_search.py
│       ├── ranking.py
│       ├── seed.py
│       ├── sheets_sync.py         (read → shared)
│       └── sheets_writer.py       (write → новый)
│
├── client_bot/                    ← переименован из bot/
│   ├── handlers/
│   │   ├── order.py
│   │   ├── admin.py
│   │   └── common.py
│   ├── ui/
│   │   └── keyboards.py
│   ├── __init__.py
│   └── __main__.py
│
├── partner_bot/                   ← новый
│   ├── handlers/
│   │   ├── registration.py        (RegistrationFSM)
│   │   ├── profile.py             (редактирование профиля)
│   │   ├── orders.py              (входящие, история, действия)
│   │   ├── notifications.py       (настройки уведомлений)
│   │   ├── analytics.py           (статистика, CSV)
│   │   └── common.py              (start, help, меню)
│   ├── ui/
│   │   └── keyboards.py           (партнёрские клавиатуры)
│   ├── services/
│   │   └── notify.py              (логика рассылки уведомлений партнёрам)
│   ├── __init__.py
│   └── __main__.py
│
├── docs/
├── tests/
├── pyproject.toml
└── .env
```

> **Вариант B (минимальные изменения):** не переименовывать `bot/` в `client_bot/`, а добавить `partner_bot/` рядом и импортировать из `bot.*`. Менее чисто, но не требует рефакторинга путей импорта.

### 10.2 Что шерится без изменений

| Модуль | Почему шерится |
|---|---|
| `database.py` | Одна БД, один пул соединений |
| `models.py` | Одни и те же таблицы |
| `schemas.py` | Те же Pydantic-валидаторы |
| `metro_graph.py` + `metro_search.py` | Одна граф-задача |
| `ranking.py` | Ранжирование используется только client-bot; partner-bot читает результат |
| `seed.py` | Инициализация БД одна |
| `sheets_sync.py` | Read-синхронизация — один фоновый таск |
| `middlewares.py` | `ActionLoggerMiddleware`, `ErrorMiddleware`, `ThrottleMiddleware` |

### 10.3 Запуск двух ботов
 — один процесс, два Dispatcher'а** через `asyncio.gather`:
```python
# main.py
async def main():
    await asyncio.gather(
        client_bot_main(),
        partner_bot_main(),
    )
```
Плюс: `sheets_sync` фоновая задача запускается один раз.  
Минус: падение одного бота роняет оба.

**Рекомендация:** два процесса + supervisor (systemd, Docker Compose).

---

## 11. Новые модели данных

### 11.1 `ServiceOwner`

```python
class ServiceOwner(Base):
    __tablename__ = "service_owners"

    id: int (PK)
    telegram_id: BigInteger (unique, FK к users.id)
    service_id: int (FK к services.id, nullable — до одобрения)
    status: String(20)          # pending | active | suspended | rejected
    registered_at: DateTime
    approved_at: DateTime (nullable)
    approved_by: String(200)    # username admin'а
```

### 11.2 `ServiceOwnerSettings`

```python
class ServiceOwnerSettings(Base):
    __tablename__ = "service_owner_settings"

    owner_id: int (PK, FK service_owners.id)
    notif_new_order: Boolean (default=True)
    notif_cancel: Boolean (default=True)
    notif_payment: Boolean (default=True)
    notif_reminder_1h: Boolean (default=True)
    notif_weekly_report: Boolean (default=False)
    quiet_hours_from: String(5) (nullable)   # "22:00"
    quiet_hours_to: String(5) (nullable)     # "09:00"
```

### 11.3 `ServiceBlockedDate`

```python
class ServiceBlockedDate(Base):
    __tablename__ = "service_blocked_dates"

    id: int (PK)
    service_id: int (FK services.id)
    blocked_date: Date
    reason: String(200) (nullable)
```

### 11.4 Расширение `Order`

Новые поля, которых ещё нет:

| Поле | Тип | Назначение |
|---|---|---|
| `partner_status` | String(30) | Внутренний статус партнёра (`new_for_partner`, `accepted_by_partner`, `in_progress`, `ready`) |
| `partner_comment` | Text | Комментарий партнёра |
| `accepted_at` | DateTime | Когда партнёр принял заявку |
| `completed_at` | DateTime | Когда отмечено «Выдано» |

### 11.5 `SheetsRetryQueue`

```python
class SheetsRetryQueue(Base):
    __tablename__ = "sheets_retry_queue"

    id: int (PK)
    service_id: int (FK services.id)
    operation: String(50)   # "update" | "add" | "set_available"
    payload_json: Text
    attempts: int (default=0)
    last_attempt_at: DateTime (nullable)
    created_at: DateTime
```

---

## 12. FSM партнёрского бота

### 12.1 `RegistrationFSM`

```
reg_name → reg_service_type → reg_address → reg_metro_search →
reg_metro_confirm → reg_phone → reg_telegram → reg_hours →
reg_hydroisolation → reg_diagnostics → reg_diag_included → reg_confirm
```

### 12.2 `PartnerProfileFSM`

```
edit_field_select → edit_field_value → edit_field_confirm
```

### 12.3 `PartnerOrderFSM`

```
order_reject_reason   ← опциональный ввод причины отклонения
order_comment_input   ← ввод текстового комментария к заявке
```

Большинство действий — inline callbacks без FSM (принять/отклонить по callback_data).

---

## 13. Уведомление администратора ESAS

При регистрации нового сервиса admin получает в admin-bot:

```
🆕 Новый сервис ожидает одобрения

Название:    Samsung Repair Pro
Тип:         Комплексный
Адрес:       Москва, ул. Марксистская, 5
Метро:       м. Марксистская
Телефон:     +7 999 123 45 67
Telegram:    @samsungrepair
Часы:        09:00–21:00
Диагностика: 400 руб. (входит в стоимость)
Гидроизол.:  Да

Владелец: @owner_username (TG: 123456789)
Зарегистрирован: 08 апр 2026 · 14:23

[✅ Одобрить]  [❌ Отклонить]  [✏️ Написать]
```

---

## 14. Интеграция notify.py в client-bot

Когда клиент оплачивает или отменяет заявку, client-bot вызывает:

```python
# shared/services/partner_notify.py
async def notify_partner_new_order(bot: Bot, order: Order) -> None: ...
async def notify_partner_order_cancelled(bot: Bot, order: Order) -> None: ...
async def notify_partner_payment_received(bot: Bot, order: Order) -> None: ...
```

Для этого client-bot при инициализации передаёт инстанс **partner bot'а** (или его `Bot` объект) в общий `notify.py`.  
Альтернатива — один `Bot`-объект для партнёрских уведомлений инициализируется в shared-слое.

---

## 15. Безопасность

| Угроза | Защита |
|---|---|
| Злоумышленник угадывает `service_id` | Каждый `ServiceOwner.service_id` привязан к `telegram_id`; middleware проверяет владение |
| Partner читает чужие заявки | `Order.service_id` сверяется с `ServiceOwner.service_id` в каждом запросе |
| Запись мусора в Sheets | `sheets_writer` принимает только `Service` ORM-объект, прошедший Pydantic-валидацию |
| Спам регистраций | ThrottleMiddleware + ручное одобрение admin'ом |
| Service Account credentials в коде | Только через env `GOOGLE_SA_B64` или путь к файлу вне репозитория |

---

## 16. Открытые вопросы (требуют решения)

| # | Вопрос | Варианты |
|---|---|---|
| 1 | Один владелец = один сервис? | A) Да, 1:1.
| 2 | Дать ли партнёру видеть username/телефон клиента? да
| 3 | Отзывы на сервисы  C) Не нужны |
| 4 | Переименование `bot/` → `client_bot/` | A) Сделать сразу
| 5 | Заблокированные даты — нужны? | B) Достаточно `is_available` toggle |
| 6 | `max_concurrent_orders` — нужен? |B) Нет |

--