"""Partner bot keyboards."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from client_bot.texts import Btn, ORDER_STATUS_RU

BACK_BTN = InlineKeyboardButton(text="Назад", callback_data="back")


def reg_back_kb() -> InlineKeyboardMarkup:
    """Inline keyboard with just one 'Назад' button — for text-input registration steps."""
    return InlineKeyboardMarkup(inline_keyboard=[[BACK_BTN]])


_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_UPGRADE_CATS = ["Окраска", "Прошивка", "Изменение конструкции", "Доп оснащение"]


# ── Reply keyboards ──────────────────────────────────────────


def partner_pending_menu_kb(
    has_draft: bool = False, is_admin: bool = False
) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=Btn.MY_DRAFT)],
    ]
    if has_draft:
        rows.append([KeyboardButton(text=Btn.CONTINUE_DRAFT)])
    rows.append([KeyboardButton(text=Btn.EDIT_DRAFT)])
    rows.append([KeyboardButton(text=Btn.SUPPORT)])
    if is_admin:
        rows.append([KeyboardButton(text=Btn.ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def partner_main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=Btn.INCOMING_ORDERS)],
        [
            KeyboardButton(text=Btn.ORDER_HISTORY),
            KeyboardButton(text=Btn.EDIT_PROFILE),
        ],
        [
            KeyboardButton(text=Btn.SERVICE_STATUS),
            KeyboardButton(text=Btn.MY_PROFILE),
        ],
        [
            KeyboardButton(text=Btn.BANK_DETAILS),
            KeyboardButton(text=Btn.NOTIF_SETTINGS),
        ],
        [KeyboardButton(text=Btn.SUPPORT)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=Btn.ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_only_menu_kb() -> ReplyKeyboardMarkup:
    """Keyboard for admins who are not active partners — only admin panel + support."""
    rows = [
        [KeyboardButton(text=Btn.ADMIN_PANEL)],
        [KeyboardButton(text=Btn.SUPPORT)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ── Registration ──────────────────────────────────────────────


def reg_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Зарегистрировать сервис", callback_data="reg:start"
                )
            ],
        ]
    )


def reg_service_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ремонт", callback_data="reg_stype:repair")],
            [InlineKeyboardButton(text="Апгрейд", callback_data="reg_stype:upgrade")],
            [InlineKeyboardButton(text="Комплекс", callback_data="reg_stype:complex")],
            [BACK_BTN],
        ]
    )


_REPAIR_CATS = ["Электрика", "Механика", "Электрика + механика"]


def reg_category_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=cat, callback_data=f"reg_cat:{cat}")]
        for cat in _REPAIR_CATS
    ]
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reg_upgrade_categories_kb(selected: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cat in _UPGRADE_CATS:
        icon = "✅ " if cat in selected else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon}{cat}", callback_data=f"reg_upcat:{cat}"
                )
            ]
        )
    if selected:
        rows.append(
            [InlineKeyboardButton(text="Готово ✓", callback_data="reg_upcat_done")]
        )
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reg_working_days_kb(selected: set[str]) -> InlineKeyboardMarkup:
    row1: list[InlineKeyboardButton] = []
    for d in _DAYS[:4]:
        icon = "✅ " if d in selected else ""
        row1.append(
            InlineKeyboardButton(text=f"{icon}{d}", callback_data=f"reg_day:{d}")
        )
    row2: list[InlineKeyboardButton] = []
    for d in _DAYS[4:]:
        icon = "✅ " if d in selected else ""
        row2.append(
            InlineKeyboardButton(text=f"{icon}{d}", callback_data=f"reg_day:{d}")
        )
    rows: list[list[InlineKeyboardButton]] = [row1, row2]
    if selected:
        rows.append(
            [InlineKeyboardButton(text="Готово ✓", callback_data="reg_days_done")]
        )
    rows.append([BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reg_legal_form_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ИП", callback_data="reg_legal:ИП")],
            [InlineKeyboardButton(text="Юр. лицо", callback_data="reg_legal:Юр. лицо")],
            [BACK_BTN],
        ]
    )


def reg_tax_system_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ОСНО", callback_data="reg_tax:ОСНО")],
            [InlineKeyboardButton(text="УСН", callback_data="reg_tax:УСН")],
            [InlineKeyboardButton(text="АУСН", callback_data="reg_tax:АУСН")],
            [
                InlineKeyboardButton(
                    text="Патентная система", callback_data="reg_tax:Патент"
                )
            ],
            [InlineKeyboardButton(text="НПД", callback_data="reg_tax:НПД")],
            [BACK_BTN],
        ]
    )


def reg_yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"{prefix}:no"),
            ],
            [BACK_BTN],
        ]
    )


def hydro_toggle_kb() -> InlineKeyboardMarkup:
    """Yes/No inline keyboard for hydroisolation toggle in profile editing."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="pedit:hydro:yes"),
                InlineKeyboardButton(text="Нет", callback_data="pedit:hydro:no"),
            ]
        ]
    )


def reg_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="reg_skip")],
            [BACK_BTN],
        ]
    )


def reg_diag_included_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Входит в стоимость", callback_data="reg_diag_incl:yes"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оплачивается отдельно", callback_data="reg_diag_incl:no"
                )
            ],
            [BACK_BTN],
        ]
    )


def reg_confirm_kb(has_bank: bool = False) -> InlineKeyboardMarkup:
    bank_label = "✏️ Изменить реквизиты" if has_bank else "Заполнить банк. реквизиты"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить", callback_data="reg:submit"),
                InlineKeyboardButton(text="Заново", callback_data="reg:restart"),
            ],
            [InlineKeyboardButton(text=bank_label, callback_data="reg:fill_bank")],
            [BACK_BTN],
        ]
    )


def metro_confirm_kb(station_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Да, {station_name}", callback_data="reg_metro_ok"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Искать заново", callback_data="reg_metro_retry"
                )
            ],
            [BACK_BTN],
        ]
    )


def reg_city_confirm_kb(city_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Да, {city_name}", callback_data="reg_city_ok"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Искать заново", callback_data="reg_city_retry"
                )
            ],
            [BACK_BTN],
        ]
    )


# ── Draft edit ────────────────────────────────────────────────


def draft_edit_kb(
    service_type: str | None = None,
    *,
    started_fields: set[str] | None = None,
    city: str | None = None,
) -> InlineKeyboardMarkup:
    fields: list[tuple[str, str, str]] = [
        ("city", "Город", "edit_draft:city"),
        ("name", "Название", "edit_draft:name"),
        ("service_type", "Тип услуг", "edit_draft:service_type"),
    ]
    if service_type in ("upgrade", "complex"):
        fields.append(("upgrade_cats", "Категории апгрейда", "edit_draft:upgrade_cats"))
    if service_type in ("repair", "complex"):
        fields.append(("category", "Категория ремонта", "edit_draft:category"))
    fields += [
        ("hydro", "Гидроизоляция", "edit_draft:hydro"),
        ("hydro_price", "Цена гидроизоляции", "edit_draft:hydro_price"),
        ("address", "Адрес", "edit_draft:address"),
        ("phone", "Телефон", "edit_draft:phone"),
        ("working_days", "Рабочие дни", "edit_draft:working_days"),
        ("hours", "Время работы", "edit_draft:hours"),
        ("diagnostics", "Диагностика", "edit_draft:diagnostics"),
        ("diag_included", "Входит в стоимость", "edit_draft:diag_included"),
    ]
    if city and city.strip().lower() == "москва":
        fields.insert(7, ("metro", "Метро", "edit_draft:metro"))

    rows: list[list[InlineKeyboardButton]] = []
    for key, label, cb in fields:
        if started_fields is not None and key not in started_fields:
            continue
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    if not rows:
        rows.append(
            [InlineKeyboardButton(text="Название", callback_data="edit_draft:name")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Orders ────────────────────────────────────────────────────


def partner_order_actions_kb(order_id: int, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status in ("awaiting_payment", "paid"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="Принять", callback_data=f"pord:accept:{order_id}"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"pord:reject:{order_id}"
                )
            ]
        )
    elif status == "accepted":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Устройство принято",
                    callback_data=f"pord:in_progress:{order_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Указать итоговую стоимость",
                    callback_data=f"pord:set_cost:{order_id}",
                )
            ]
        )
    elif status == "in_progress":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Завершен", callback_data=f"pord:complete:{order_id}"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Указать итоговую стоимость",
                    callback_data=f"pord:set_cost:{order_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Написать клиенту",
                url="https://t.me/",
                callback_data=None,
            )
        ]
        if False
        else []
    )  # placeholder, real link set in handler
    rows.append([InlineKeyboardButton(text="К списку", callback_data="pord:back_list")])
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def partner_orders_list_kb(
    orders: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    def _display_order_code(order) -> str:
        code = str(getattr(order, "order_code", "") or "").strip()
        if code:
            return code
        oid = getattr(order, "id", 0)
        return f"{int(oid):06d}" if isinstance(oid, int) else "000000"

    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        label = (
            f"#{o.id}/{_display_order_code(o)} | "
            f"{o.scheduled_date or '?'} {o.scheduled_time or ''}"
        )
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"pord:detail:{o.id}")]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="<-", callback_data=f"pord:page:{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(text="->", callback_data=f"pord:page:{page + 1}")
        )
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def partner_order_detail_kb(
    order_id: int, status: str, client_username: str | None = None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status in ("awaiting_payment", "paid"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="Принять", callback_data=f"pord:accept:{order_id}"
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"pord:reject:{order_id}"
                ),
            ]
        )
    elif status == "accepted":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Принять в работу",
                    callback_data=f"pord:start_work:{order_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Клиент отказался",
                    callback_data=f"pord:client_refused:{order_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Указать итоговую стоимость",
                    callback_data=f"pord:set_cost:{order_id}",
                )
            ]
        )
    elif status == "in_progress":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Готов к выдаче",
                    callback_data=f"pord:ready:{order_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Изменить цену",
                    callback_data=f"pord:update_price:{order_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Указать итоговую стоимость",
                    callback_data=f"pord:set_cost:{order_id}",
                )
            ]
        )
    elif status == "ready_for_pickup":
        pass  # Ожидаем действия клиента
    if client_username:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Написать клиенту",
                    url=f"https://t.me/{client_username}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="К списку", callback_data="pord:back_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def orders_filter_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все", callback_data="pord:filter:all")],
            [
                InlineKeyboardButton(
                    text="Принятые", callback_data="pord:filter:accepted"
                )
            ],
            [
                InlineKeyboardButton(
                    text="В работе", callback_data="pord:filter:in_progress"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Готовы к выдаче", callback_data="pord:filter:ready_for_pickup"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выполненные", callback_data="pord:filter:completed"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отклоненные", callback_data="pord:filter:rejected_by_partner"
                )
            ],
        ]
    )


# ── Profile ───────────────────────────────────────────────────


def profile_edit_fields_kb(*, show_metro: bool = True) -> InlineKeyboardMarkup:
    fields = [
        ("Город", "pedit:city"),
        ("Название", "pedit:name"),
        ("Адрес", "pedit:address"),
        ("Телефон", "pedit:phone"),
        ("Время работы", "pedit:hours"),
        ("Диагностика", "pedit:diagnostics"),
        ("Гидроизоляция", "pedit:hydro"),
        ("Цена гидроизоляции", "pedit:hydro_price"),
    ]
    if show_metro:
        fields.insert(3, ("Метро", "pedit:metro"))
    rows = [
        [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in fields
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bank_edit_fields_kb() -> InlineKeyboardMarkup:
    fields = [
        ("Форма", "bedit:legal_form"),
        ("Налогообложение", "bedit:tax_system"),
        ("Расч. счёт", "bedit:bank_account"),
        ("Банк", "bedit:bank_name"),
        ("БИК", "bedit:bik"),
        ("Корр. счёт", "bedit:corr_account"),
        ("Организация", "bedit:org_name"),
        ("ИНН", "bedit:inn"),
    ]
    rows = [
        [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in fields
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quick_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть сейчас", callback_data="pstatus:open")],
            [
                InlineKeyboardButton(
                    text="Закрыть на сегодня", callback_data="pstatus:pause_today"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть до конца недели",
                    callback_data="pstatus:pause_week",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть пока не открою",
                    callback_data="pstatus:close",
                )
            ],
        ]
    )


# ── Notifications ─────────────────────────────────────────────


def notif_settings_kb(
    *,
    enabled: bool,
    new_order: bool,
    cancel: bool,
    client_comment: bool,
    estimate: bool,
    dispute: bool,
    completed: bool,
) -> InlineKeyboardMarkup:
    def _icon(v: bool) -> str:
        return "[v]" if v else "[ ]"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_icon(enabled)} Все уведомления",
                    callback_data="notif:toggle:enabled",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(new_order)} Новые заявки",
                    callback_data="notif:toggle:new_order",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(cancel)} Отмены клиентом",
                    callback_data="notif:toggle:cancel",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(client_comment)} Комментарии клиента",
                    callback_data="notif:toggle:client_comment",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(estimate)} Реакция клиента на смету",
                    callback_data="notif:toggle:estimate",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(dispute)} Споры по заявкам",
                    callback_data="notif:toggle:dispute",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(completed)} Завершение заявки",
                    callback_data="notif:toggle:completed",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Включить все",
                    callback_data="notif:preset:all_on",
                ),
                InlineKeyboardButton(
                    text="Отключить все",
                    callback_data="notif:preset:all_off",
                ),
            ],
        ]
    )


# ── Admin keyboards ──────────────────────────────────────────


def padm_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Все партнёры", callback_data="padm:partners:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Фильтр по статусу", callback_data="padm:filter"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Статистика сервисов", callback_data="padm:services:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Настройки уведомлений", callback_data="padm:notif"
                )
            ],
        ]
    )


def padm_notif_settings_kb(
    *,
    enabled: bool,
    partner_application: bool,
    profile_update: bool,
    status_change: bool,
) -> InlineKeyboardMarkup:
    def _icon(v: bool) -> str:
        return "[v]" if v else "[ ]"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_icon(enabled)} Все уведомления",
                    callback_data="padm:notif:toggle:enabled",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(partner_application)} Новые анкеты партнёров",
                    callback_data="padm:notif:toggle:partner_application",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(profile_update)} Изменения профиля партнёра",
                    callback_data="padm:notif:toggle:profile_update",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{_icon(status_change)} Смена статуса партнёра",
                    callback_data="padm:notif:toggle:status_change",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Включить все",
                    callback_data="padm:notif:preset:all_on",
                ),
                InlineKeyboardButton(
                    text="Отключить все",
                    callback_data="padm:notif:preset:all_off",
                ),
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="padm:main")],
        ]
    )


def padm_services_kb(
    services: Sequence,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for svc in services:
        name = (getattr(svc, "name", None) or "?").strip() or "?"
        total_orders = int(getattr(svc, "orders_total", 0) or 0)
        label = f"#{svc.id} | {name} | заявок: {total_orders}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"padm:service:{svc.id}:from:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◄ Назад",
                callback_data=f"padm:services:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="padm:noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ►",
                callback_data=f"padm:services:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="padm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def padm_service_detail_kb(from_page: int, service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Заявки сервиса",
                    callback_data=f"padm:service_orders:{service_id}:0:from:{from_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К списку сервисов",
                    callback_data=f"padm:services:{from_page}",
                )
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="padm:main")],
        ]
    )


def padm_service_orders_kb(
    service_id: int,
    orders: Sequence,
    page: int,
    total_pages: int,
    from_page: int,
) -> InlineKeyboardMarkup:
    def _display_order_code(order) -> str:
        code = str(getattr(order, "order_code", "") or "").strip()
        if code:
            return code
        oid = getattr(order, "id", 0)
        return f"{int(oid):06d}" if isinstance(oid, int) else "000000"

    rows: list[list[InlineKeyboardButton]] = []

    for order in orders:
        status = ORDER_STATUS_RU.get(order.status, order.status)
        date = order.scheduled_date or "?"
        label = f"#{order.id}/{_display_order_code(order)} | {date} | {status}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=(
                        f"padm:service_order:{service_id}:{order.id}:{page}:from:{from_page}"
                    ),
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◄ Назад",
                callback_data=f"padm:service_orders:{service_id}:{page - 1}:from:{from_page}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="padm:noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ►",
                callback_data=f"padm:service_orders:{service_id}:{page + 1}:from:{from_page}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="К сервису",
                callback_data=f"padm:service:{service_id}:from:{from_page}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="padm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def padm_service_order_detail_kb(
    service_id: int,
    page: int,
    from_page: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="К списку заявок",
                    callback_data=f"padm:service_orders:{service_id}:{page}:from:{from_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К сервису",
                    callback_data=f"padm:service:{service_id}:from:{from_page}",
                )
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="padm:main")],
        ]
    )


def padm_partners_kb(
    owners: Sequence, page: int, total_pages: int, status_filter: str | None = None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    _STATUS_SHORT = {
        "ожидает": "Ожидает",
        "активный": "Активен",
        "отклонён": "Отклонён",
        "приостановлен": "Приостановлен",
    }
    for o in owners:
        service = getattr(o, "service", None)
        if service is None:
            availability = "—"
        else:
            availability = (
                "Да" if bool(getattr(service, "is_available", False)) else "Нет"
            )
        label = f"#{o.id} | {o.draft_name or '?'} | {_STATUS_SHORT.get(o.status, o.status)} | дост.: {availability}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"padm:partner:{o.id}")]
        )
    nav: list[InlineKeyboardButton] = []
    suffix = f":status:{status_filter}" if status_filter else ""
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◄ Назад", callback_data=f"padm:partners:{page - 1}{suffix}"
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}", callback_data="padm:noop"
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд ►", callback_data=f"padm:partners:{page + 1}{suffix}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="padm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def padm_partner_detail_kb(
    owner_id: int,
    status: str,
    service_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status == "ожидает":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Одобрить", callback_data=f"padm:approve:{owner_id}"
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"padm:reject_partner:{owner_id}"
                ),
            ]
        )
    elif status == "активный":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Приостановить", callback_data=f"padm:suspend:{owner_id}"
                )
            ]
        )
    elif status == "приостановлен":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Восстановить", callback_data=f"padm:unsuspend:{owner_id}"
                )
            ]
        )
    if service_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="К сервису",
                    callback_data=f"padm:service:{service_id}:from:0",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="К списку", callback_data="padm:partners:0")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
