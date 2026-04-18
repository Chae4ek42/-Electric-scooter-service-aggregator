"""Keyboard builders for the Telegram bot."""

from __future__ import annotations

import datetime
import zoneinfo
from typing import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.core.config import (
    CALENDAR_DAYS,
    WORK_HOUR_END,
    WORK_HOUR_START,
    TIME_SLOT_MINUTES,
)
from bot.texts import Btn

_MOSCOW_TZ = zoneinfo.ZoneInfo("Europe/Moscow")

# ── Reply (text) keyboards ───────────────────────────────────


def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=Btn.SUBMIT_ORDER)],
        [
            KeyboardButton(text=Btn.MY_ORDERS),
            KeyboardButton(text=Btn.SUPPORT),
        ],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=Btn.ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def support_kb(support_user: str, cooperation_user: str = "") -> InlineKeyboardMarkup:
    """Inline-кнопки поддержки: техническая + сотрудничество."""
    sup = support_user.lstrip("@")
    rows = [
        [
            InlineKeyboardButton(
                text="Техническая поддержка",
                url=f"https://t.me/{sup}",
            )
        ],
    ]
    if cooperation_user:
        coop = cooperation_user.lstrip("@")
        rows.append(
            [
                InlineKeyboardButton(
                    text="Вопросы по сотрудничеству",
                    url=f"https://t.me/{coop}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Inline keyboards ─────────────────────────────────────────

BACK_BTN = InlineKeyboardButton(text="Назад", callback_data="back")


def _rows(
    buttons: list[InlineKeyboardButton], cols: int = 2
) -> list[list[InlineKeyboardButton]]:
    """Distribute buttons into rows of *cols* each."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), cols):
        rows.append(buttons[i : i + cols])
    return rows


def service_type_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Ремонт", callback_data="stype:repair")],
        [InlineKeyboardButton(text="Апгрейд", callback_data="stype:upgrade")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def brands_kb(brands: Sequence) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton(text=b.name, callback_data=f"brand:{b.id}") for b in brands
    ]
    rows = _rows(btns, cols=2)
    rows.append(
        [
            InlineKeyboardButton(
                text="Другой бренд (ввести)", callback_data="brand:other"
            )
        ]
    )
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_kb(models: Sequence) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton(text=m.name, callback_data=f"model:{m.id}")
        for m in models
        if m.name != "Другое"  # покажем «Другое» отдельной кнопкой внизу
    ]
    rows = _rows(btns, cols=1)
    # «Другое» — всегда предпоследней строкой
    rows.append(
        [
            InlineKeyboardButton(
                text="Другое (ввести вручную)", callback_data="model:other"
            )
        ]
    )
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def malfunction_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Механика", callback_data="malf:Механика")],
            [InlineKeyboardButton(text="Электрика", callback_data="malf:Электрика")],
            [BACK_BTN],
        ]
    )


def upgrade_category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Гидроизоляция", callback_data="upcat:Гидроизоляция"
                )
            ],
            [InlineKeyboardButton(text="Окраска", callback_data="upcat:Окраска")],
            [InlineKeyboardButton(text="Прошивка", callback_data="upcat:Прошивка")],
            [
                InlineKeyboardButton(
                    text="Изменение конструкции",
                    callback_data="upcat:Изменение конструкции",
                )
            ],
            [BACK_BTN],
        ]
    )


def services_kb(services: Sequence) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton(text=s.name, callback_data=f"service:{s.id}")
        for s in services
    ]
    rows = _rows(btns, cols=1)
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def location_method_kb() -> InlineKeyboardMarkup:
    # GPS временно заморожен — доступен только ввод станции метро
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ввести станцию метро", callback_data="loc:metro"
                ),
            ],
            [BACK_BTN],
        ]
    )


def geo_fallback_kb() -> InlineKeyboardMarkup:
    """Shown after clicking geo on desktop — keeps inline buttons visible."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ввести станцию метро", callback_data="loc:metro"
                )
            ],
            [BACK_BTN],
        ]
    )


def metro_confirm_kb(station_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Да, {station_name}", callback_data="metro_ok"
                )
            ],
            [InlineKeyboardButton(text="Искать заново", callback_data="metro_retry")],
            [BACK_BTN],
        ]
    )


# ── Calendar ──────────────────────────────────────────────────


def calendar_kb(booked_dates: set[str] | None = None) -> InlineKeyboardMarkup:
    """Generate a keyboard with today (if slots remain) + next CALENDAR_DAYS days.

    Today is included only when the next full hour is before WORK_HOUR_END.
    All dates are calculated in Moscow time.
    """
    if booked_dates is None:
        booked_dates = set()

    _RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    now_msk = datetime.datetime.now(tz=_MOSCOW_TZ)
    today = now_msk.date()

    # Include today only if at least one timeslot still fits today
    today_next_hour = now_msk.hour + 1
    include_today = today_next_hour < WORK_HOUR_END

    buttons: list[InlineKeyboardButton] = []
    start_delta = 0 if include_today else 1
    for delta in range(start_delta, CALENDAR_DAYS + 1):
        day = today + datetime.timedelta(days=delta)
        if delta == 0:
            label = f"Сегодня {day.strftime('%d.%m')} ({_RU_DAYS[day.weekday()]})"
        else:
            label = f"{day.strftime('%d.%m')} ({_RU_DAYS[day.weekday()]})"
        cb = f"date:{day.strftime('%d.%m.%Y')}"
        buttons.append(InlineKeyboardButton(text=label, callback_data=cb))

    rows = _rows(buttons, cols=3)
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def time_slots_kb(date_str: str) -> InlineKeyboardMarkup:
    """Generate hourly time-slot buttons between WORK_HOUR_START and WORK_HOUR_END.

    For today (Moscow time), past slots are filtered out.
    The earliest available slot is the next full hour.
    """
    requested_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
    now_msk = datetime.datetime.now(tz=_MOSCOW_TZ)
    today = now_msk.date()

    if requested_date == today:
        # Earliest slot = next full hour (can't book the current or past hour)
        min_hour = now_msk.hour + 1
    else:
        min_hour = WORK_HOUR_START

    buttons: list[InlineKeyboardButton] = []
    for hour in range(max(WORK_HOUR_START, min_hour), WORK_HOUR_END):
        for minute_offset in range(0, 60, TIME_SLOT_MINUTES):
            label = f"{hour:02d}:{minute_offset:02d}"
            buttons.append(
                InlineKeyboardButton(text=label, callback_data=f"time:{label}")
            )

    if not buttons:
        # Safety fallback — calendar_kb already prevents selecting today without slots
        buttons.append(
            InlineKeyboardButton(text="Нет доступного времени", callback_data="noop")
        )

    rows = _rows(buttons, cols=4)
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="confirm:yes"),
                InlineKeyboardButton(text="Отменить", callback_data="confirm:no"),
            ],
            [BACK_BTN],
        ]
    )


def payment_kb(order_id: int) -> InlineKeyboardMarkup:
    """Stub payment keyboard — used inline within orders list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к оплате", callback_data=f"pay:proceed:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отменить заявку", callback_data=f"pay:cancel:{order_id}"
                )
            ],
        ]
    )


def orders_list_action_kb() -> InlineKeyboardMarkup:
    """Кнопки действий под списком заявок."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить заявку", callback_data="orders:action:pay"
                ),
                InlineKeyboardButton(
                    text="Отменить заявку", callback_data="orders:action:cancel"
                ),
            ]
        ]
    )


def order_select_kb(orders: list, action: str) -> InlineKeyboardMarkup:
    """Список заявок для выбора действия. action: 'pay' или 'cancel'."""
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"№{o.id}",
                    callback_data=f"orders:select:{action}:{o.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Client notification keyboards ────────────────────────────


def client_visited_kb(order_id: int) -> InlineKeyboardMarkup:
    """Были ли вы в сервисе? (после отказа/завершения)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Были ли вы в сервисе?",
                    callback_data=f"cord:ask_visited:{order_id}",
                )
            ]
        ]
    )


def client_visited_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """Да / Нет / Назад для 'Были ли вы в сервисе?'"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да", callback_data=f"cord:visited:{order_id}:yes"
                ),
                InlineKeyboardButton(
                    text="Нет", callback_data=f"cord:visited:{order_id}:no"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад", callback_data=f"cord:visited_back:{order_id}"
                )
            ],
        ]
    )


def client_confirm_estimate_kb(order_id: int) -> InlineKeyboardMarkup:
    """Подтвердить смету / Отклонить"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить смету",
                    callback_data=f"cord:confirm_estimate:{order_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"cord:reject_estimate:{order_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"cord:estimate_back:{order_id}",
                )
            ],
        ]
    )


def client_ready_kb(order_id: int) -> InlineKeyboardMarkup:
    """Оплатить и завершить / Оспорить"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить и завершить",
                    callback_data=f"cord:pay_final:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оспорить",
                    callback_data=f"cord:dispute:{order_id}",
                )
            ],
        ]
    )


def client_pay_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """Да / Нет / Назад для оплаты"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, оплатить",
                    callback_data=f"cord:pay_confirm:{order_id}",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=f"cord:pay_cancel:{order_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"cord:pay_back:{order_id}",
                )
            ],
        ]
    )


ADMIN_PAGE_SIZE = 10


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все заявки", callback_data="adm:orders:0")],
            [
                InlineKeyboardButton(
                    text="Заявки по статусу", callback_data="adm:filter"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Заявки партнёров", callback_data="adm:partners:0"
                )
            ],
        ]
    )


def admin_partner_detail_kb(owner_id: int, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="К списку", callback_data="adm:partners:0")])
    rows.append([InlineKeyboardButton(text="В главное меню", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_filter_kb() -> InlineKeyboardMarkup:
    statuses = [
        ("awaiting_payment", "Ожид. оплаты"),
        ("paid", "Оплачено"),
        ("accepted", "Приняты"),
        ("in_progress", "В работе"),
        ("ready_for_pickup", "Готовы к выдаче"),
        ("completed", "Завершены"),
        ("client_refused", "Клиент отказался"),
        ("disputed", "Оспорены"),
        ("no_center", "Не найден центр"),
    ]
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"adm:orders:0:status:{key}")]
        for key, label in statuses
    ]
    rows.append(
        [InlineKeyboardButton(text="Все статусы", callback_data="adm:orders:0")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_orders_kb(
    orders: Sequence,
    page: int,
    total_pages: int,
    status_filter: str | None = None,
) -> InlineKeyboardMarkup:
    """Paginated list of orders."""
    _STATUS_LABELS = {
        "new": "Новая",
        "awaiting_payment": "Ожидает",
        "paid": "Оплачено",
        "accepted": "Принята",
        "in_progress": "В работе",
        "ready_for_pickup": "К выдаче",
        "interrupted": "Прервана",
        "completed": "Завершена",
        "cancelled": "Отменена",
        "client_refused": "Отказ",
        "disputed": "Оспорена",
        "rejected_by_partner": "Отклонена",
        "no_center": "Нет центра",
    }

    def _fmt_date(d: str | None) -> str:
        # Формат хранения: DD.MM.YYYY — берём только DD.MM
        if not d:
            return "?"
        return d[:5]

    def _fmt_model(o) -> str:
        if o.model_custom_name:
            return o.model_custom_name
        if o.model:
            return o.model.name
        return "—"

    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        status = _STATUS_LABELS.get(o.status, o.status)
        date = _fmt_date(o.scheduled_date)
        model = _fmt_model(o)
        label = f"№{o.id} · {date} · {status} · {model}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"adm:order:{o.id}")]
        )

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    suffix = f":status:{status_filter}" if status_filter else ""
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◄ Назад", callback_data=f"adm:orders:{page - 1}{suffix}"
            )
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="adm:noop")
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ►", callback_data=f"adm:orders:{page + 1}{suffix}"
            )
        )
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="В главное меню", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_detail_kb(order_id: int) -> InlineKeyboardMarkup:
    """Action buttons in order detail view."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принята",
                    callback_data=f"adm:setstatus:{order_id}:accepted",
                ),
                InlineKeyboardButton(
                    text="❌ Отменена",
                    callback_data=f"adm:setstatus:{order_id}:cancelled",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Прервана",
                    callback_data=f"adm:setstatus:{order_id}:interrupted",
                ),
                InlineKeyboardButton(
                    text="✔️ Завершена",
                    callback_data=f"adm:setstatus:{order_id}:completed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад к списку", callback_data="adm:back_list"
                )
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="adm:main")],
        ]
    )
