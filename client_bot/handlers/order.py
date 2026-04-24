"""Order creation FSM handlers -- full flow."""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import select

from client_bot.core.config import ADMIN_USERNAMES, SUPPORT_USER, COOPERATION_USER
from client_bot.core.formatting import e
from client_bot.core.database import async_session
from client_bot.texts import Btn, Client, ORDER_STATUS_RU
from client_bot.ui.keyboards import (
    brands_kb,
    calendar_kb,
    client_confirm_estimate_kb,
    client_pay_confirm_kb,
    client_ready_kb,
    client_visited_confirm_kb,
    client_visited_kb,
    confirm_kb,
    location_method_kb,
    main_menu_kb,
    malfunction_type_kb,
    metro_confirm_kb,
    my_order_card_kb,
    models_kb,
    order_select_kb,
    payment_kb,
    service_type_kb,
    support_kb,
    time_slots_kb,
    upgrade_category_kb,
)
from client_bot.services.metro_search import best_metro_match, top_metro_matches
from client_bot.services.notifications import send_by_token, send_with_retry
from client_bot.services.order_lifecycle import (
    ACTOR_CLIENT,
    ACTOR_SYSTEM,
    transition_order_status,
)
from client_bot.services.payment_policy import PaymentPolicy
from client_bot.services.ranking import RankingContext, rank_services
from client_bot.domain.models import Brand, MetroStation, Model, Order, Service, User
from client_bot.domain.order_rules import (
    money,
)
from client_bot.domain.schemas import (
    BrandNameInput,
    MetroTextInput,
    ModelNameInput,
    ProblemDescription,
)
from client_bot.domain.states import ClientOrderFSM, OrderFSM

logger = logging.getLogger(__name__)
router = Router(name="order")


_MENU_TEXTS = (Btn.SUBMIT_ORDER, Btn.MY_ORDERS, Btn.SUPPORT)
_SERVICE_TYPE_RU = {
    "repair": "Ремонт",
    "upgrade": "Апгрейд",
    "complex": "Комплексный",
}
_CANCELLABLE_ORDER_STATUSES = {"awaiting_payment", "paid", "accepted"}
_POST_PREPAYMENT_STATUSES = {
    "paid",
    "accepted",
    "in_progress",
    "ready_for_pickup",
    "completed",
    "rejected_by_partner",
    "interrupted",
    "client_refused",
    "disputed",
}

# Защита UI от автогенерируемых тестовых брендов, попадающих в рабочую БД.
_TEST_BRAND_RE = re.compile(
    r"^(?:hl_)?brand_[0-9a-f]{8,}$|^(?:test|pytest)_[0-9a-f]{6,}$",
    re.IGNORECASE,
)


def _pydantic_msg(exc: ValidationError) -> str:
    """Extract human-readable error message from Pydantic ValidationError."""
    raw = exc.errors()[0]["msg"] if exc.errors() else "Некорректный ввод"
    # Pydantic v2 prepends 'Value error, ' — strip it
    if raw.startswith("Value error, "):
        raw = raw[len("Value error, ") :]
    return raw


def _is_admin(username: str | None) -> bool:
    return bool(username) and username.lower() in ADMIN_USERNAMES


def _is_generated_test_brand_name(name: str) -> bool:
    return bool(_TEST_BRAND_RE.match(name.strip()))


async def _load_client_brands(session) -> list[Brand]:
    brands = (await session.execute(select(Brand).order_by(Brand.name))).scalars().all()
    filtered = [b for b in brands if not _is_generated_test_brand_name(b.name or "")]
    return filtered or brands


async def _safe_edit_or_answer(
    event: types.Message | types.CallbackQuery,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
) -> None:
    """Edit inline message when possible, otherwise send new message."""
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            await event.message.answer(text, reply_markup=reply_markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=reply_markup)


async def _handle_menu_interrupt(message: types.Message, state: FSMContext) -> bool:
    """Check for menu commands during text-input states. Returns True if handled."""
    if message.text not in _MENU_TEXTS:
        return False
    await state.clear()
    await message.answer(Client.PROCEDURE_INTERRUPTED)
    if message.text == Btn.SUBMIT_ORDER:
        await state.set_state(OrderFSM.service_type)
        await message.answer(
            Client.Order.SELECT_SERVICE_TYPE, reply_markup=service_type_kb()
        )
    elif message.text == Btn.MY_ORDERS:
        await my_orders_interrupt(message, state)
    elif message.text == Btn.SUPPORT:
        await message.answer(
            Client.Common.CHOOSE_SUPPORT_TOPIC,
            reply_markup=support_kb(SUPPORT_USER, COOPERATION_USER),
        )
    return True


# 1. ENTRY


@router.message(F.text == Btn.SUBMIT_ORDER)
async def start_order(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderFSM.service_type)
    logger.info("user=%s started order flow", message.from_user.id)
    await message.answer(
        Client.Order.SELECT_SERVICE_TYPE, reply_markup=service_type_kb()
    )


# 2. SERVICE TYPE


@router.callback_query(OrderFSM.service_type, F.data.startswith("stype:"))
async def pick_service_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    stype = callback.data.split(":")[1]
    await state.update_data(service_type=stype)
    logger.info("user=%s picked service_type=%s", callback.from_user.id, stype)
    await state.set_state(OrderFSM.brand)
    async with async_session() as session:
        brands = await _load_client_brands(session)
    await _safe_edit_or_answer(callback, Client.Order.SELECT_BRAND, brands_kb(brands))


# 3. BRAND


@router.callback_query(OrderFSM.brand, F.data.startswith("brand:"))
async def pick_brand(callback: types.CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[1]
    if raw == "other":
        await state.update_data(brand_id=None, brand_custom_name=None)
        await state.set_state(OrderFSM.brand_custom)
        logger.info("user=%s chose custom brand input", callback.from_user.id)
        await _safe_edit_or_answer(callback, Client.Order.ENTER_BRAND)
        return

    brand_id = int(raw)
    await state.update_data(brand_id=brand_id, brand_custom_name=None)
    logger.info("user=%s picked brand_id=%s", callback.from_user.id, brand_id)
    await state.set_state(OrderFSM.model)
    async with async_session() as session:
        models = (
            (
                await session.execute(
                    select(Model).where(Model.brand_id == brand_id).order_by(Model.name)
                )
            )
            .scalars()
            .all()
        )
    await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))


# 3a. BRAND CUSTOM


@router.message(OrderFSM.brand_custom, F.text)
async def pick_brand_custom(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = BrandNameInput(text=message.text)
    except ValidationError as exc:
        await message.answer(_pydantic_msg(exc))
        return
    logger.info("user=%s entered custom brand=%r", message.from_user.id, validated.text)
    await state.update_data(brand_id=None, brand_custom_name=validated.text)
    await state.update_data(model_id=None, model_custom_name=None)
    await state.set_state(OrderFSM.model_custom)
    await message.answer(Client.Order.ENTER_MODEL)


# 4. MODEL


@router.callback_query(OrderFSM.model, F.data.startswith("model:"))
async def pick_model(callback: types.CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[1]
    if raw == "other":
        await state.update_data(model_id=None, model_custom_name=None)
        await state.set_state(OrderFSM.model_custom)
        await _safe_edit_or_answer(callback, Client.Order.ENTER_MODEL_EXAMPLE)
        return
    model_id = int(raw)
    await state.update_data(model_id=model_id, model_custom_name=None)
    logger.info("user=%s picked model_id=%s", callback.from_user.id, model_id)
    await _proceed_after_model(callback, state)


# 4a. MODEL CUSTOM


@router.message(OrderFSM.model_custom, F.text)
async def pick_model_custom(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = ModelNameInput(text=message.text)
    except ValidationError as exc:
        await message.answer(_pydantic_msg(exc))
        return
    data = await state.get_data()
    brand_id = data.get("brand_id")
    if brand_id:
        async with async_session() as session:
            placeholder = (
                await session.execute(
                    select(Model)
                    .where(Model.brand_id == brand_id)
                    .where(Model.name == "Другое")
                )
            ).scalar_one_or_none()
        if placeholder:
            await state.update_data(
                model_id=placeholder.id, model_custom_name=validated.text
            )
        else:
            await state.update_data(model_id=None, model_custom_name=validated.text)
    else:
        await state.update_data(model_id=None, model_custom_name=validated.text)
    logger.info("user=%s entered custom model=%r", message.from_user.id, validated.text)
    await _proceed_after_model(message, state)


async def _proceed_after_model(
    event: types.CallbackQuery | types.Message, state: FSMContext
) -> None:
    data = await state.get_data()
    if data["service_type"] == "repair":
        await state.set_state(OrderFSM.malfunction_type)
        await _safe_edit_or_answer(
            event, Client.Order.SELECT_MALFUNCTION, malfunction_type_kb()
        )
    else:
        await state.set_state(OrderFSM.upgrade_category)
        await _safe_edit_or_answer(
            event, Client.Order.SELECT_UPGRADE_CATEGORY, upgrade_category_kb()
        )


# 5. MALFUNCTION TYPE (repair)


@router.callback_query(OrderFSM.malfunction_type, F.data.startswith("malf:"))
async def pick_malfunction(callback: types.CallbackQuery, state: FSMContext) -> None:
    category_name = callback.data.split(":")[1]
    await state.update_data(malfunction_category=category_name, upgrade_category=None)
    logger.info("user=%s picked malfunction=%s", callback.from_user.id, category_name)
    await state.set_state(OrderFSM.problem_description)
    await _safe_edit_or_answer(callback, Client.Order.DESCRIBE_PROBLEM)


# 5a. UPGRADE CATEGORY


@router.callback_query(OrderFSM.upgrade_category, F.data.startswith("upcat:"))
async def pick_upgrade_category(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    cat = callback.data.split(":")[1]
    await state.update_data(upgrade_category=cat, malfunction_category=None)
    logger.info("user=%s picked upgrade_category=%s", callback.from_user.id, cat)
    if cat == "Гидроизоляция":
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback,
            Client.Order.CHOOSE_METRO,
            location_method_kb(),
        )
    else:
        await state.set_state(OrderFSM.problem_description)
        await _safe_edit_or_answer(callback, Client.Order.DESCRIBE_UPGRADE)


# 5b. PROBLEM DESCRIPTION


@router.message(OrderFSM.problem_description, F.text)
async def pick_problem_description(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = ProblemDescription(text=message.text)
    except ValidationError as exc:
        await message.answer(
            f"{_pydantic_msg(exc)}\nПожалуйста, опишите вашу проблему подробнее."
        )
        return
    logger.info(
        "user=%s entered problem description (%d chars)",
        message.from_user.id,
        len(validated.text),
    )
    await state.update_data(problem_description=validated.text)
    await state.set_state(OrderFSM.location_method)
    await message.answer(
        Client.Order.CHOOSE_METRO,
        reply_markup=location_method_kb(),
    )


# 6. LOCATION / METRO


@router.callback_query(OrderFSM.location_method, F.data == "loc:metro")
async def choose_metro_text(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.metro_search)
    await _safe_edit_or_answer(
        callback, "Введите название станции метро (или его часть):"
    )


@router.message(OrderFSM.metro_search, F.text)
async def handle_metro_text(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = MetroTextInput(text=message.text)
    except ValidationError as exc:
        await message.answer(f"{_pydantic_msg(exc)}\nПопробуйте ещё раз:")
        return

    async with async_session() as session:
        stations = (await session.execute(select(MetroStation))).scalars().all()

    matches = top_metro_matches(validated.text, stations, limit=5)
    if not matches:
        await message.answer(Client.Order.METRO_NOT_FOUND)
        return

    if len(matches) == 1 or matches[0][1] > 0.85:
        station = matches[0][0]
        await state.update_data(metro_station=station.name)
        await state.set_state(OrderFSM.metro_confirm)
        logger.info(
            "user=%s metro match: %s (score=%.2f)",
            message.from_user.id,
            station.name,
            matches[0][1],
        )
        await message.answer(
            f"Найдена станция: <b>{e(station.name)}</b> ({e(station.line)})\n\nВсё верно?",
            reply_markup=metro_confirm_kb(station.name),
        )
    else:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from client_bot.ui.keyboards import BACK_BTN

        buttons = []
        for st, score in matches:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{st.name} ({st.line})",
                        callback_data=f"metro_pick:{st.id}",
                    )
                ]
            )
        buttons.append(
            [InlineKeyboardButton(text="Ввести заново", callback_data="metro_retry")]
        )
        buttons.append([BACK_BTN])
        await state.set_state(OrderFSM.metro_confirm)
        await message.answer(
            Client.Order.METRO_MULTIPLE,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


# 7. METRO CONFIRM


@router.callback_query(OrderFSM.metro_confirm, F.data == "metro_ok")
async def metro_confirmed(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.calendar_date)
    await _safe_edit_or_answer(callback, Client.Order.SELECT_DATE, calendar_kb())


@router.callback_query(OrderFSM.metro_confirm, F.data.startswith("metro_pick:"))
async def metro_picked_from_list(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    station_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        station = (
            await session.execute(
                select(MetroStation).where(MetroStation.id == station_id)
            )
        ).scalar_one_or_none()
    if station is None:
        await callback.answer("Станция не найдена", show_alert=True)
        return
    await state.update_data(metro_station=station.name)
    logger.info(
        "user=%s picked metro=%s from list", callback.from_user.id, station.name
    )
    await state.set_state(OrderFSM.calendar_date)
    await _safe_edit_or_answer(callback, Client.Order.SELECT_DATE, calendar_kb())


@router.callback_query(OrderFSM.metro_confirm, F.data == "metro_retry")
async def metro_retry(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.metro_search)
    await _safe_edit_or_answer(callback, Client.Order.ENTER_METRO)


# 8. CALENDAR -- DATE


@router.callback_query(OrderFSM.calendar_date, F.data.startswith("date:"))
async def pick_date(callback: types.CallbackQuery, state: FSMContext) -> None:
    date_str = callback.data.split(":")[1]
    await state.update_data(scheduled_date=date_str)
    logger.info("user=%s picked date=%s", callback.from_user.id, date_str)
    await state.set_state(OrderFSM.calendar_time)
    await _safe_edit_or_answer(
        callback, f"Выберите время на {date_str}:", time_slots_kb(date_str)
    )


# 9. CALENDAR -- TIME -> ranking -> confirm


async def _process_time_choice(
    callback: types.CallbackQuery,
    state: FSMContext,
    time_str: str,
    forced_service_id: int | None = None,
) -> None:
    await state.update_data(scheduled_time=time_str)
    data = await state.get_data()

    service_id: int | None = None
    diagnostics_price: float | None = None
    diagnostics_included: bool = False
    hydroisolation_price: str | None = None
    svc_rating: float | None = None
    model = None

    async with async_session() as session:
        if data.get("model_id"):
            model = (
                await session.execute(select(Model).where(Model.id == data["model_id"]))
            ).scalar_one_or_none()

        ctx = RankingContext(
            service_type=data.get("service_type", "repair"),
            malfunction_category=data.get("malfunction_category"),
            upgrade_category=data.get("upgrade_category"),
            user_metro=data.get("metro_station"),
            scheduled_time=time_str,
            scheduled_date=data.get("scheduled_date"),
        )
        result = await rank_services(ctx, session, limit=1)

        logger.info(
            "user=%s ranking: matches=%d time_fallback=%s suggested_date=%s suggested_time=%s suggested_slots=%s",
            callback.from_user.id,
            len(result.matches),
            result.time_fallback,
            result.suggested_date,
            result.suggested_time,
            result.suggested_slots,
        )

        slot_suggestions = list(result.suggested_slots)
        if not slot_suggestions and result.suggested_time:
            slot_suggestions = [(result.suggested_date or "", result.suggested_time)]

        if result.time_fallback and slot_suggestions:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            rows: list[list[InlineKeyboardButton]] = []
            labels: list[str] = []
            for suggested_date, suggested_time in slot_suggestions:
                if suggested_date:
                    suggested_label = f"{suggested_date} {suggested_time}"
                    if result.fallback_service_id is not None:
                        suggested_cb = (
                            f"time_suggest:{result.fallback_service_id}:"
                            f"{suggested_date}:{suggested_time}"
                        )
                    else:
                        suggested_cb = f"time_suggest:{suggested_date}:{suggested_time}"
                else:
                    suggested_label = suggested_time
                    suggested_cb = f"time:{suggested_time}"
                labels.append(suggested_label)
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Записаться: {suggested_label}",
                            callback_data=suggested_cb,
                        )
                    ]
                )

            rows.append(
                [
                    InlineKeyboardButton(
                        text="Выбрать другую дату",
                        callback_data="back",
                    )
                ]
            )
            kb = InlineKeyboardMarkup(inline_keyboard=rows)

            selected_date = data.get("scheduled_date")
            selected_slot = (
                f"на {e(selected_date)} в {e(time_str)}"
                if selected_date
                else f"в {e(time_str)}"
            )
            suggested_lines = "\n".join(f"• <b>{e(label)}</b>" for label in labels)
            service_hint = (
                f"\nСервис: <b>{e(result.fallback_service_name)}</b>"
                if result.fallback_service_name
                else ""
            )
            await _safe_edit_or_answer(
                callback,
                f"К сожалению, {selected_slot} подходящие сервисы не работают.{service_hint}\n"
                f"Ближайшие доступные слоты:\n{suggested_lines}",
                kb,
            )
            return

        if result.matches:
            best = result.matches[0]
            if forced_service_id is not None:
                forced_match = next(
                    (m for m in result.matches if m.service.id == forced_service_id),
                    None,
                )
                if forced_match is not None:
                    best = forced_match
            service_id = best.service.id
            diagnostics_price = best.service.diagnostics_price
            diagnostics_included = best.service.diagnostics_included
            hydroisolation_price = best.service.hydroisolation_price
            svc_rating = best.service.yandex_rating

    if service_id is None:
        logger.warning(
            "user=%s no services found: type=%s malf=%s upcat=%s metro=%s date=%s time=%s",
            callback.from_user.id,
            data.get("service_type"),
            data.get("malfunction_category"),
            data.get("upgrade_category"),
            data.get("metro_station"),
            data.get("scheduled_date"),
            time_str,
        )
        # Сохраняем заявку со статусом «не найден центр»
        async with async_session() as session:
            user = (
                await session.execute(
                    select(User).where(User.id == callback.from_user.id)
                )
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    id=callback.from_user.id,
                    username=callback.from_user.username or "",
                    full_name=callback.from_user.full_name or "Unknown",
                )
                session.add(user)
                await session.flush()

            order = Order(
                user_id=callback.from_user.id,
                service_id=None,
                model_id=data.get("model_id"),
                model_custom_name=data.get("model_custom_name"),
                brand_custom_name=data.get("brand_custom_name"),
                metro_station=data.get("metro_station"),
                scheduled_date=data.get("scheduled_date"),
                scheduled_time=data.get("scheduled_time"),
                problem_description=data.get("problem_description"),
                upgrade_category=data.get("upgrade_category"),
                diagnostics_price=None,
                order_code=str(random.randint(100000, 999999)),
                status="no_center",
            )
            session.add(order)
            await session.commit()
            order_id = order.id
        logger.info(
            "user=%s order #%s created with no_center", callback.from_user.id, order_id
        )
        await state.clear()
        await _safe_edit_or_answer(
            callback,
            f"Заявка №{order_id} создана.\n"
            "К сожалению, подходящих сервис-центров не найдено.\n"
            "Мы уведомим вас, когда появится подходящий сервис.",
        )
        return

    await state.update_data(
        service_id=service_id,
        diagnostics_price=diagnostics_price,
        diagnostics_included=diagnostics_included,
        hydroisolation_price=hydroisolation_price,
    )
    await state.set_state(OrderFSM.confirm)

    custom_name = data.get("model_custom_name")
    brand_custom = data.get("brand_custom_name")
    if brand_custom:
        model_display = f"{brand_custom} {custom_name}" if custom_name else brand_custom
    elif custom_name:
        brand_name = model.brand.name if model and model.brand else ""
        model_display = f"{brand_name} {custom_name}".strip()
    elif model:
        model_display = (
            f"{model.brand.name} {model.name}" if model.brand else model.name
        )
    else:
        model_display = ""

    price_line = ""
    is_hydro = data.get("upgrade_category") == "Гидроизоляция"
    if is_hydro and hydroisolation_price:
        price_line = (
            f"\n\nСтоимость гидроизоляции: {hydroisolation_price}"
            f"\nПредоплата: 500 руб."
        )
    elif diagnostics_price:
        if diagnostics_included:
            price_line = (
                f"\nСтоимость диагностики: {diagnostics_price:.0f} руб.\n\n"
                "Диагностика входит в стоимость ремонта.\n"
                "Если вы передумали до приезда в сервис "
                "- вернем ваши деньги 🤝"
            )
        else:
            price_line = (
                f"\nСтоимость диагностики: {diagnostics_price:.0f} руб.\n\n"
                "В этом сервисе, диагностика не входит "
                "в стоимость ремонта.\n"
                "Если выполнена только диагностика, без ремонта "
                "- деньги не возвращаются.\n"
                "Если вы передумали до приезда в сервис "
                "- вернем ваши деньги 🤝"
            )

    rating_line = ""
    if svc_rating is not None:
        rating_line = f"\nРейтинг сервиса: {svc_rating}"

    summary = (
        "<b>Подтвердите заявку:</b>\n\n"
        f"Модель: {e(model_display)}\n"
        f"Метро: {e(data.get('metro_station', ''))}\n"
        f"Дата: {data.get('scheduled_date', '')}\n"
        f"Время: {e(time_str)}"
        f"{rating_line}"
        f"{price_line}"
    )

    logger.info(
        "user=%s confirm screen: service_id=%s diag_price=%s",
        callback.from_user.id,
        service_id,
        diagnostics_price,
    )
    await _safe_edit_or_answer(callback, summary, confirm_kb())


@router.callback_query(OrderFSM.calendar_time, F.data.startswith("time:"))
async def pick_time(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка формата времени", show_alert=True)
        return
    time_str = f"{parts[1]}:{parts[2]}"
    await _process_time_choice(callback, state, time_str)


@router.callback_query(OrderFSM.calendar_time, F.data.startswith("time_suggest:"))
async def pick_suggested_time(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка формата времени", show_alert=True)
        return

    forced_service_id: int | None = None
    if len(parts) >= 5 and parts[1].isdigit():
        forced_service_id = int(parts[1])
        date_str = parts[2]
        time_str = f"{parts[3]}:{parts[4]}"
    else:
        date_str = parts[1]
        time_str = f"{parts[2]}:{parts[3]}"

    await state.update_data(scheduled_date=date_str)
    await _process_time_choice(
        callback,
        state,
        time_str,
        forced_service_id=forced_service_id,
    )


# 10. CONFIRM


@router.callback_query(OrderFSM.confirm, F.data == "confirm:yes")
async def confirm_order(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == callback.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=callback.from_user.id,
                username=callback.from_user.username or "",
                full_name=callback.from_user.full_name or "Unknown",
            )
            session.add(user)
            await session.flush()

        svc = (
            await session.execute(
                select(Service).where(Service.id == data["service_id"])
            )
        ).scalar_one_or_none()
        if not svc or not svc.is_available:
            await state.clear()
            await _safe_edit_or_answer(callback, Client.Order.SERVICE_UNAVAILABLE)
            return

        order = Order(
            user_id=callback.from_user.id,
            service_id=data["service_id"],
            model_id=data.get("model_id"),
            model_custom_name=data.get("model_custom_name"),
            brand_custom_name=data.get("brand_custom_name"),
            metro_station=data.get("metro_station"),
            scheduled_date=data.get("scheduled_date"),
            scheduled_time=data.get("scheduled_time"),
            problem_description=data.get("problem_description"),
            upgrade_category=data.get("upgrade_category"),
            diagnostics_price=data.get("diagnostics_price"),
            order_code=str(random.randint(100000, 999999)),
            status="awaiting_payment",
        )
        session.add(order)
        await session.commit()
        order_id = order.id

    await state.clear()

    logger.info(
        "user=%s order #%s created (service_id=%s)",
        callback.from_user.id,
        order_id,
        data["service_id"],
    )

    await _safe_edit_or_answer(
        callback,
        f"<b>Заявка №{order_id} создана!</b>\n\n⏳ Обработка оплаты...",
    )

    # Auto-complete payment after 10 seconds (mock)
    import asyncio

    async def _auto_pay_diagnostics():
        await asyncio.sleep(10)
        async with async_session() as s:
            o = (
                await s.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
            if o and o.status == "awaiting_payment":
                await transition_order_status(
                    s,
                    o,
                    "paid",
                    actor=f"{ACTOR_SYSTEM}:mock_payment",
                    reason="auto_mock_payment",
                    metadata={"flow": "order_confirm"},
                )
                o.payment_id = f"AUTO-DIAG-{order_id}"
                await s.commit()
                logger.info("auto-payment completed for order #%s", order_id)

                model_str = _client_model_name(o)

                try:
                    await callback.message.answer(
                        _build_client_order_full_text(
                            o,
                            title=f"✅ Оплата заявки №{order_id} прошла успешно.",
                        )
                    )
                except Exception:
                    logger.exception(
                        "Failed to send auto-payment message for order #%s",
                        order_id,
                    )

                # Notify partner
                _notify_partner(
                    o, _build_partner_new_order_text(order_id, o, model_str)
                )

    asyncio.create_task(_auto_pay_diagnostics())
    await callback.message.answer(
        "Главное меню:",
        reply_markup=main_menu_kb(is_admin=_is_admin(callback.from_user.username)),
    )


@router.callback_query(OrderFSM.confirm, F.data == "confirm:no")
async def cancel_order(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    logger.info("user=%s cancelled order at confirm", callback.from_user.id)
    await _safe_edit_or_answer(callback, "Заявка отменена.")


# 11. PAYMENT STUB


@router.callback_query(F.data.startswith("pay:proceed:"))
async def payment_proceed(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    order_id = int(parts[2])
    await state.clear()

    await _safe_edit_or_answer(callback, "⏳ Обработка оплаты...")

    import asyncio

    async def _auto_pay():
        await asyncio.sleep(10)
        async with async_session() as s:
            o = (
                await s.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
            if o and o.status == "awaiting_payment":
                await transition_order_status(
                    s,
                    o,
                    "paid",
                    actor=f"{ACTOR_SYSTEM}:mock_payment",
                    reason="auto_mock_payment",
                    metadata={"flow": "payment_proceed"},
                )
                o.payment_id = f"AUTO-PAY-{order_id}"
                await s.commit()
                logger.info("auto-payment completed for order #%s", order_id)

                model_str = _client_model_name(o)

                try:
                    await callback.message.answer(
                        _build_client_order_full_text(
                            o,
                            title=f"✅ Оплата заявки №{order_id} прошла успешно.",
                        )
                    )
                except Exception:
                    logger.exception("Failed to send auto-payment message")

                # Notify partner
                _notify_partner(
                    o, _build_partner_new_order_text(order_id, o, model_str)
                )

    asyncio.create_task(_auto_pay())


@router.callback_query(F.data.startswith("pay:cancel:"))
async def payment_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    order_id = int(parts[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if order and order.status in _CANCELLABLE_ORDER_STATUSES:
            await transition_order_status(
                session,
                order,
                "cancelled",
                actor=ACTOR_CLIENT,
                reason="payment_cancel",
            )
            await session.commit()
            logger.info(
                "user=%s cancelled order #%s via payment",
                callback.from_user.id,
                order_id,
            )
    await state.clear()
    await _safe_edit_or_answer(callback, f"Заявка №{order_id} отменена.")


@router.callback_query(F.data == "noop")
async def noop_handler(callback: types.CallbackQuery) -> None:
    await callback.answer(Client.Common.UNAVAILABLE, show_alert=False)


# UNIVERSAL BACK BUTTON


@router.callback_query(F.data == "back")
async def universal_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    data = await state.get_data()

    if current is None or current == OrderFSM.service_type.state:
        await state.clear()
        await _safe_edit_or_answer(callback, "Выберите тип услуги:", service_type_kb())
        return

    if current == OrderFSM.brand.state:
        await state.set_state(OrderFSM.service_type)
        await _safe_edit_or_answer(callback, "Выберите тип услуги:", service_type_kb())

    elif current == OrderFSM.brand_custom.state:
        await state.set_state(OrderFSM.brand)
        async with async_session() as session:
            brands = await _load_client_brands(session)
        await _safe_edit_or_answer(
            callback, "Выберите бренд самоката:", brands_kb(brands)
        )

    elif current == OrderFSM.model.state:
        await state.set_state(OrderFSM.brand)
        async with async_session() as session:
            brands = await _load_client_brands(session)
        await _safe_edit_or_answer(
            callback, "Выберите бренд самоката:", brands_kb(brands)
        )

    elif current == OrderFSM.model_custom.state:
        if data.get("brand_custom_name"):
            await state.set_state(OrderFSM.brand_custom)
            await _safe_edit_or_answer(callback, "Введите название бренда самоката:")
        else:
            await state.set_state(OrderFSM.model)
            brand_id = data.get("brand_id")
            async with async_session() as session:
                models = (
                    (
                        await session.execute(
                            select(Model)
                            .where(Model.brand_id == brand_id)
                            .order_by(Model.name)
                        )
                    )
                    .scalars()
                    .all()
                )
            await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))

    elif current == OrderFSM.malfunction_type.state:
        brand_id = data.get("brand_id")
        if brand_id:
            await state.set_state(OrderFSM.model)
            async with async_session() as session:
                models = (
                    (
                        await session.execute(
                            select(Model)
                            .where(Model.brand_id == brand_id)
                            .order_by(Model.name)
                        )
                    )
                    .scalars()
                    .all()
                )
            await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))
        else:
            await state.set_state(OrderFSM.model_custom)
            await _safe_edit_or_answer(callback, "Введите название модели самоката:")

    elif current == OrderFSM.upgrade_category.state:
        brand_id = data.get("brand_id")
        if brand_id:
            await state.set_state(OrderFSM.model)
            async with async_session() as session:
                models = (
                    (
                        await session.execute(
                            select(Model)
                            .where(Model.brand_id == brand_id)
                            .order_by(Model.name)
                        )
                    )
                    .scalars()
                    .all()
                )
            await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))
        else:
            await state.set_state(OrderFSM.model_custom)
            await _safe_edit_or_answer(callback, "Введите название модели самоката:")

    elif current == OrderFSM.problem_description.state:
        stype = data.get("service_type", "repair")
        if stype == "repair":
            await state.set_state(OrderFSM.malfunction_type)
            await _safe_edit_or_answer(
                callback, "Выберите категорию неисправности:", malfunction_type_kb()
            )
        else:
            await state.set_state(OrderFSM.upgrade_category)
            await _safe_edit_or_answer(
                callback, "Выберите категорию апгрейда:", upgrade_category_kb()
            )

    elif current == OrderFSM.location_method.state:
        upcat = data.get("upgrade_category")
        stype = data.get("service_type", "repair")
        if upcat == "Гидроизоляция":
            await state.set_state(OrderFSM.upgrade_category)
            await _safe_edit_or_answer(
                callback, "Выберите категорию апгрейда:", upgrade_category_kb()
            )
        elif stype == "repair":
            await state.set_state(OrderFSM.problem_description)
            await _safe_edit_or_answer(callback, "Опишите проблему:")
        else:
            await state.set_state(OrderFSM.problem_description)
            await _safe_edit_or_answer(callback, "Опишите, что вы хотите сделать:")

    elif current in (OrderFSM.metro_search.state, OrderFSM.metro_confirm.state):
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback,
            "Выберете ближайшее к вам метро (Подберем самый ближайший сервис, под вашу проблему)",
            location_method_kb(),
        )

    elif current == OrderFSM.calendar_date.state:
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback,
            "Выберете ближайшее к вам метро (Подберем самый ближайший сервис, под вашу проблему)",
            location_method_kb(),
        )

    elif current == OrderFSM.calendar_time.state:
        await state.set_state(OrderFSM.calendar_date)
        await _safe_edit_or_answer(callback, Client.Order.SELECT_DATE, calendar_kb())

    elif current == OrderFSM.confirm.state:
        date_str = data.get("scheduled_date", "")
        await state.set_state(OrderFSM.calendar_time)
        await _safe_edit_or_answer(
            callback, f"Выберите время на {date_str}:", time_slots_kb(date_str)
        )

    else:
        await state.clear()
        await _safe_edit_or_answer(callback, "Выберите тип услуги:", service_type_kb())


# MY ORDERS


def _can_cancel_order(order: Order) -> bool:
    return order.status in _CANCELLABLE_ORDER_STATUSES


def _can_contact_or_comment(order: Order) -> bool:
    return order.status in _POST_PREPAYMENT_STATUSES and order.service is not None


def _order_pay_callback(order: Order) -> str | None:
    if order.status == "awaiting_payment":
        return f"pay:proceed:{order.id}"
    if order.status == "ready_for_pickup":
        return f"cord:pay_final:{order.id}"
    return None


def _service_type_code(order: Order) -> str:
    if order.service:
        return order.service.service_type
    return "upgrade" if order.upgrade_category else "repair"


def _build_client_order_short_text(order: Order) -> str:
    model_name = _client_model_name(order)
    status_text = ORDER_STATUS_RU.get(order.status, order.status)

    if order.status == "awaiting_payment":
        service_name = "будет назначен после оплаты"
    else:
        service_name = order.service.name if order.service else "—"

    lines = [
        f"<b>Заявка №{order.id}</b> - {e(status_text)}",
        f"Модель: {e(model_name)}",
        f"Сервис: {e(service_name)}",
        f"Тип услуги: {_SERVICE_TYPE_RU.get(_service_type_code(order), _service_type_code(order))}",
        f"Дата: {e(order.scheduled_date or '—')} {e(order.scheduled_time or '')}".rstrip(),
    ]
    if order.upgrade_category:
        lines.append(f"Категория: {e(order.upgrade_category)}")
    return "\n".join(lines)


def _build_client_order_full_text(order: Order, title: str | None = None) -> str:
    model_name = _client_model_name(order)
    status_text = ORDER_STATUS_RU.get(order.status, order.status)

    if order.status == "awaiting_payment":
        service_name = "будет назначен после оплаты"
    else:
        service_name = order.service.name if order.service else "—"

    lines: list[str] = []
    if title:
        lines.extend([title, ""])

    lines.extend(
        [
            f"Заявка №{order.id}",
            f"Статус: {e(status_text)}",
            f"Модель: {e(model_name)}",
            f"Сервис: {e(service_name)}",
            f"Тип услуги: {_SERVICE_TYPE_RU.get(_service_type_code(order), _service_type_code(order))}",
            f"Метро: {e(order.metro_station or '—')}",
            f"Дата: {e(order.scheduled_date or '—')} {e(order.scheduled_time or '')}".rstrip(),
        ]
    )

    if order.order_code:
        lines.append(f"Код заказа: {e(order.order_code)}")
        lines.append("По прибытии в сервис назовите номер заказа.")
    if order.upgrade_category:
        lines.append(f"Категория апгрейда: {e(order.upgrade_category)}")
    if order.problem_description:
        lines.append(f"Описание проблемы: {e(order.problem_description)}")
    if order.diagnostics_price is not None:
        lines.append(f"Диагностика: {order.diagnostics_price:.0f} руб.")
    if order.estimate_cost is not None:
        lines.append(f"Смета: {order.estimate_cost:.0f} руб.")
    if order.total_cost is not None:
        lines.append(f"Итоговая стоимость: {order.total_cost:.0f} руб.")
    if order.partner_comment:
        lines.append(f"Комментарий по заявке: {e(order.partner_comment)}")
    if order.reject_reason:
        lines.append(f"Причина отказа: {e(order.reject_reason)}")
    if order.refusal_reason:
        lines.append(f"Причина отказа клиента: {e(order.refusal_reason)}")
    if order.dispute_reason:
        lines.append(f"Причина оспаривания: {e(order.dispute_reason)}")

    if order.service and _can_contact_or_comment(order):
        if order.service.address:
            lines.append(f"Адрес сервиса: {e(order.service.address)}")
        if order.service.phone:
            lines.append(f"Телефон сервиса: {e(order.service.phone)}")
        if order.service.telegram_handle:
            handle = order.service.telegram_handle
            if handle and not handle.startswith("@"):
                handle = f"@{handle}"
            lines.append(f"Telegram сервиса: {e(handle)}")

    return "\n".join(lines)


@router.message(F.text == Btn.MY_ORDERS)
async def my_orders_interrupt(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer(Client.PROCEDURE_INTERRUPTED)

    async with async_session() as session:
        orders = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.user_id == message.from_user.id)
                    .where(Order.status != "cancelled")
                    .order_by(Order.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        if not orders:
            await message.answer(Client.Order.NO_ORDERS)
            return

    for order in orders:
        await message.answer(
            _build_client_order_short_text(order),
            reply_markup=my_order_card_kb(
                order.id,
                pay_callback=_order_pay_callback(order),
                can_cancel=_can_cancel_order(order),
                can_contact=_can_contact_or_comment(order),
                can_comment=_can_contact_or_comment(order),
            ),
        )


# ORDERS ACTION CALLBACKS


@router.callback_query(F.data == "orders:action:pay")
async def orders_action_pay(callback: types.CallbackQuery) -> None:
    async with async_session() as session:
        orders = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.user_id == callback.from_user.id)
                    .where(Order.status == "awaiting_payment")
                    .order_by(Order.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    if not orders:
        await callback.answer("Нет заявок, ожидающих оплаты.", show_alert=True)
        return
    await callback.message.answer(
        "Выберите заявку для оплаты:", reply_markup=order_select_kb(orders, "pay")
    )
    await callback.answer()


@router.callback_query(F.data == "orders:action:cancel")
async def orders_action_cancel(callback: types.CallbackQuery) -> None:
    async with async_session() as session:
        orders = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.user_id == callback.from_user.id)
                    .where(Order.status.in_(tuple(_CANCELLABLE_ORDER_STATUSES)))
                    .order_by(Order.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    if not orders:
        await callback.answer("Нет заявок, доступных для отмены.", show_alert=True)
        return
    await callback.message.answer(
        "Выберите заявку для отмены:", reply_markup=order_select_kb(orders, "cancel")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("orders:select:"))
async def orders_select(callback: types.CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Неверный формат.", show_alert=True)
        return
    action = parts[2]
    try:
        order_id = int(parts[3])
    except ValueError:
        await callback.answer("Неверный ID.", show_alert=True)
        return

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()

        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        if action == "cancel":
            if not _can_cancel_order(order):
                await callback.answer("Эту заявку нельзя отменить.", show_alert=True)
                return
            await transition_order_status(
                session,
                order,
                "cancelled",
                actor=ACTOR_CLIENT,
                reason="orders_list_cancel",
            )
            await session.commit()
            logger.info("user=%s cancelled order #%s", callback.from_user.id, order_id)
            await callback.message.answer(f"Заявка №{order_id} отменена.")
            await callback.answer()
        elif action == "pay":
            if order.status != "awaiting_payment":
                await callback.answer("Эта заявка не ожидает оплаты.", show_alert=True)
                return
            await callback.message.answer(
                f"Для оплаты заявки №{order_id} свяжитесь с оператором."
            )
            await callback.answer()
        else:
            await callback.answer("Неизвестное действие.", show_alert=True)


@router.callback_query(F.data.startswith("myord:cancel:"))
async def my_order_cancel(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if not _can_cancel_order(order):
            await callback.answer("Эту заявку нельзя отменить.", show_alert=True)
            return

        await transition_order_status(
            session,
            order,
            "cancelled",
            actor=ACTOR_CLIENT,
            reason="my_orders_cancel",
        )
        await session.commit()

    await callback.message.answer(f"Заявка №{order_id} отменена.")
    await callback.answer("Заявка отменена")


@router.callback_query(F.data.startswith("myord:full:"))
async def my_order_full(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.message.answer(
        _build_client_order_full_text(
            order,
            title=f"Полная информация по заявке №{order_id}",
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("myord:contact:"))
async def my_order_contact(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if not _can_contact_or_comment(order):
        await callback.answer(
            "Связь с сервисом доступна после предоплаты.",
            show_alert=True,
        )
        return

    service = order.service
    if service is None:
        await callback.answer("Сервис не найден.", show_alert=True)
        return

    lines = [
        f"Контакты сервиса по заявке №{order_id}:",
        f"Сервис: {e(service.name or '—')}",
    ]
    if service.address:
        lines.append(f"Адрес: {e(service.address)}")
    if service.phone:
        lines.append(f"Телефон: {e(service.phone)}")
    if service.telegram_handle:
        handle = service.telegram_handle
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        lines.append(f"Telegram: {e(handle)}")

    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("myord:comment:"))
async def my_order_comment_start(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if not _can_contact_or_comment(order):
        await callback.answer(
            "Комментарий доступен после предоплаты.",
            show_alert=True,
        )
        return

    await state.update_data(comment_order_id=order_id)
    await state.set_state(ClientOrderFSM.order_comment)
    await callback.message.answer(
        "Введите комментарий для сервиса (минимум 3 символа):"
    )
    await callback.answer()


@router.message(ClientOrderFSM.order_comment, F.text)
async def my_order_comment_input(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return

    comment = message.text.strip()
    if len(comment) < 3:
        await message.answer("Комментарий слишком короткий. Напишите подробнее:")
        return

    data = await state.get_data()
    order_id = data.get("comment_order_id")
    if not order_id:
        await state.clear()
        return

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != message.from_user.id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        if not _can_contact_or_comment(order):
            await message.answer("Комментарий можно добавить только после предоплаты.")
            await state.clear()
            return

        stamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
            "%d.%m.%Y %H:%M UTC"
        )
        entry = f"[{stamp}] {comment}"
        order.partner_comment = (
            f"{order.partner_comment}\n{entry}" if order.partner_comment else entry
        )
        await session.commit()
        model_str = _client_model_name(order)

    await state.clear()
    await message.answer("Комментарий добавлен и отправлен сервису.")

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        _notify_partner(
            order,
            f"Клиент добавил комментарий к заявке #{order_id}\n\n"
            f"Устройство: {e(model_str)}\n"
            f"Дата: {e(order.scheduled_date or '—')} {e(order.scheduled_time or '')}\n"
            f"Комментарий:\n{e(comment)}",
        )


# ══════════════════════════════════════════════════════════════
# CLIENT ORDER ACTIONS (inline notifications)
# ══════════════════════════════════════════════════════════════


def _notify_partner(order: Order, text: str) -> None:
    """Fire-and-forget partner notification (runs in background)."""
    import asyncio

    async def _send():
        from client_bot.core.config import PARTNER_BOT_TOKEN

        try:
            # Find partner owner linked to service
            async with async_session() as session:
                from client_bot.domain.models import ServiceDraft

                owner = (
                    await session.execute(
                        select(ServiceDraft)
                        .where(ServiceDraft.service_id == order.service_id)
                        .where(ServiceDraft.status == "активный")
                    )
                ).scalar_one_or_none()
                if not owner:
                    logger.error(
                        "PARTNER_NOTIFY_ERR | order=%s | service=%s | reason=owner_not_found",
                        order.id,
                        order.service_id,
                    )
                    return
                await send_by_token(
                    PARTNER_BOT_TOKEN,
                    owner.owner_user_id,
                    text,
                    dedupe_key=f"partner_notify:{order.id}:{text[:80]}",
                )
                logger.info(
                    "PARTNER_NOTIFY | order=%s | partner=%s",
                    order.id,
                    owner.owner_user_id,
                )
        except Exception:
            logger.exception(
                "PARTNER_NOTIFY_ERR | order=%s | service=%s",
                order.id,
                order.service_id,
            )

    asyncio.create_task(_send())


def _build_partner_new_order_text(order_id: int, order: Order, model_str: str) -> str:
    service_type = order.service.service_type if order.service else None
    if service_type is None:
        service_type = "upgrade" if order.upgrade_category else "repair"

    user = order.user
    if user and user.username:
        client_info = f"@{user.username}"
    elif user and user.full_name:
        client_info = user.full_name
    else:
        client_info = "—"

    slot = f"{order.scheduled_date or '—'} {order.scheduled_time or ''}".strip()
    lines = [
        f"🆕 Новая заявка #{order_id}",
        "",
        f"Устройство: {e(model_str)}",
        f"Клиент: {e(client_info)} (ID: {order.user_id})",
        f"Тип услуги: {_SERVICE_TYPE_RU.get(service_type, service_type)}",
        f"Метро: {e(order.metro_station or '—')}",
        f"Дата: {e(slot)}",
    ]
    if order.upgrade_category:
        lines.append(f"Категория апгрейда: {e(order.upgrade_category)}")
    if order.problem_description:
        lines.append(f"Описание: {e(order.problem_description)}")
    if order.diagnostics_price is not None:
        lines.append(f"Диагностика: {order.diagnostics_price:.0f} руб.")
    if order.order_code:
        lines.append(f"Код заказа: {e(order.order_code)}")
        lines.append("Клиенту нужно назвать номер заказа при визите.")
    return "\n".join(lines)


# ── Visited? ──────────────────────────────────────────────────


@router.callback_query(F.data.startswith("cord:ask_visited:"))
async def ask_visited(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=client_visited_confirm_kb(order_id)
        )
    except Exception:
        await callback.message.answer(
            "Были ли вы в сервисе?",
            reply_markup=client_visited_confirm_kb(order_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cord:visited:"))
async def visited_answer(callback: types.CallbackQuery) -> None:
    parts = callback.data.split(":")
    order_id = int(parts[2])
    answer = parts[3] == "yes"

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        order.client_visited = answer
        await session.commit()

    answer_text = "Да" if answer else "Нет"
    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\nВы были в сервисе: {answer_text}",
        )
    except Exception:
        pass
    await callback.answer(f"Ответ сохранён: {answer_text}")


@router.callback_query(F.data.startswith("cord:visited_back:"))
async def visited_back(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=client_visited_kb(order_id)
        )
    except Exception:
        pass
    await callback.answer()


# ── Confirm estimate ──────────────────────────────────────────


@router.callback_query(F.data.startswith("cord:confirm_estimate:"))
async def confirm_estimate(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        order.client_confirmed_estimate = True
        await session.commit()
        svc_name = order.service.name if order.service else ""

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ Смета подтверждена.",
        )
    except Exception:
        pass
    await callback.answer("Смета подтверждена")

    # Notify partner
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        _notify_partner(
            order,
            f"✅ Клиент подтвердил смету по заявке #{order_id}.",
        )


@router.callback_query(F.data.startswith("cord:reject_estimate:"))
async def reject_estimate(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        order.client_confirmed_estimate = False
        await session.commit()

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ Смета отклонена.",
        )
    except Exception:
        pass
    await callback.answer("Смета отклонена")

    # Notify partner
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order:
        _notify_partner(
            order,
            f"❌ Клиент отклонил смету по заявке #{order_id}.\n"
            "Свяжитесь с клиентом для уточнения.",
        )


@router.callback_query(F.data.startswith("cord:estimate_back:"))
async def estimate_back(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=client_confirm_estimate_kb(order_id)
        )
    except Exception:
        pass
    await callback.answer()


# ── Pay final ─────────────────────────────────────────────────


@router.callback_query(F.data.startswith("cord:pay_final:"))
async def pay_final(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "ready_for_pickup":
            await callback.answer("Невозможно.", show_alert=True)
            return
        total = money(order.total_cost or order.estimate_cost or 0)
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category,
            order.diagnostics_price,
        )
        remainder = PaymentPolicy.remainder(
            total,
            order.upgrade_category,
            order.diagnostics_price,
        )

    await callback.message.answer(
        f"Подтвердите оплату заявки #{order_id}\n\n" f"К оплате: {remainder:.0f} руб.",
        reply_markup=client_pay_confirm_kb(order_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cord:pay_confirm:"))
async def pay_confirm(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "ready_for_pickup":
            await callback.answer("Невозможно.", show_alert=True)
            return

    try:
        await callback.message.edit_text("⏳ Обработка оплаты...")
    except Exception:
        await callback.message.answer("⏳ Обработка оплаты...")
    await callback.answer()

    import asyncio

    async def _auto_pay_final():
        await asyncio.sleep(10)
        async with async_session() as s:
            o = (
                await s.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
            if not o or o.status != "ready_for_pickup":
                return
            await transition_order_status(
                s,
                o,
                "completed",
                actor=f"{ACTOR_CLIENT}:mock_final_payment",
                reason="final_mock_payment",
            )
            o.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            o.payment_id = f"AUTO-FINAL-{order_id}"
            await s.commit()
            total = money(o.total_cost or o.estimate_cost or 0)
            model_str = _client_model_name(o)

        logger.info("auto final payment for order #%s", order_id)

        try:
            await callback.message.answer(
                _build_client_order_full_text(
                    o,
                    title=(f"✅ Заявка №{order_id} завершена.\n" "Оплата произведена."),
                )
            )
        except Exception:
            logger.exception("Failed to send final payment message")

        # Notify partner
        async with async_session() as s:
            o = (
                await s.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
        if o:
            _notify_partner(
                o,
                f"✅ Заявка #{order_id} завершена\n\n"
                f"Клиент оплатил и забрал устройство.\n"
                f"Устройство: {model_str}\n"
                f"Итого: {total:.0f} руб.",
            )

    asyncio.create_task(_auto_pay_final())


@router.callback_query(F.data.startswith("cord:pay_cancel:"))
async def pay_cancel(callback: types.CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\nОплата отменена.",
        )
    except Exception:
        pass
    await callback.answer("Оплата отменена")


@router.callback_query(F.data.startswith("cord:pay_back:"))
async def pay_back(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
    if order and order.status == "ready_for_pickup":
        try:
            await callback.message.edit_reply_markup(
                reply_markup=client_ready_kb(order_id)
            )
        except Exception:
            pass
    await callback.answer()


# ── Dispute ───────────────────────────────────────────────────


@router.callback_query(F.data.startswith("cord:dispute:"))
async def dispute_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if order.status != "ready_for_pickup":
            await callback.answer("Невозможно.", show_alert=True)
            return

    await state.update_data(dispute_order_id=order_id)
    await state.set_state(ClientOrderFSM.dispute_reason)
    await callback.message.answer("Опишите возникшую проблему:")
    await callback.answer()


@router.message(ClientOrderFSM.dispute_reason, F.text)
async def dispute_reason_input(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 5:
        await message.answer("Опишите проблему подробнее (минимум 5 символов):")
        return

    data = await state.get_data()
    order_id = data.get("dispute_order_id")
    if not order_id:
        await state.clear()
        return

    import datetime

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != message.from_user.id:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        await transition_order_status(
            session,
            order,
            "disputed",
            actor=ACTOR_CLIENT,
            reason="client_dispute",
        )
        order.dispute_reason = text
        await session.commit()
        svc_name = order.service.name if order.service else ""
        svc_id = order.service_id
        model_str = _client_model_name(order)
        total = money(order.total_cost or order.estimate_cost or 0)
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category,
            order.diagnostics_price,
        )
        user_obj = order.user

    await state.clear()
    await message.answer(
        f"⚠️ Заявка #{order_id} оспорена\n\n"
        "Ваша жалоба принята. Администратор свяжется с вами "
        "в ближайшее время."
    )

    logger.info("client %s disputed order #%s", message.from_user.id, order_id)

    # Notify all admins
    try:
        from client_bot.core.config import CLIENT_BOT_TOKEN

        from aiogram import Bot

        bot = Bot(token=CLIENT_BOT_TOKEN)
        client_info = f"@{user_obj.username}" if user_obj and user_obj.username else ""
        client_name = user_obj.full_name if user_obj else ""
        admin_text = (
            f"⚠️ Оспаривание заявки #{order_id}\n\n"
            f"Клиент: {client_name} ({client_info}, ID: {message.from_user.id})\n"
            f"Сервис: {svc_name} (ID: {svc_id})\n"
            f"Устройство: {model_str}\n\n"
            f"Причина оспаривания:\n{text}\n\n"
            f"Итоговая стоимость: {total:.0f} руб.\n"
            f"Предоплата: {prepayment:.0f} руб."
        )
        async with async_session() as session:
            for admin_username in ADMIN_USERNAMES:
                # Try to find admin's user_id
                admin_user = (
                    await session.execute(
                        select(User).where(User.username == admin_username)
                    )
                ).scalar_one_or_none()
                if admin_user:
                    try:
                        await send_with_retry(
                            bot,
                            admin_user.id,
                            admin_text,
                            dedupe_key=f"admin_dispute:{order_id}:{admin_user.id}",
                        )
                    except Exception:
                        pass
        await bot.session.close()
    except Exception:
        logger.exception("Failed to notify admins about dispute")


def _client_model_name(order: Order) -> str:
    """Helper: human-readable model name for client-side."""
    if order.brand_custom_name:
        return f"{order.brand_custom_name} {order.model_custom_name or ''}".strip()
    if order.model_custom_name:
        return order.model_custom_name
    if order.model:
        return (
            f"{order.model.brand.name} {order.model.name}"
            if order.model.brand
            else order.model.name
        )
    return "—"


# ══════════════════════════════════════════════════════════════
# FSM reminder: continue / cancel
# ══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "fsm_remind:cancel")
async def fsm_remind_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(Client.FORM_CANCELLED)
    except Exception:
        await callback.message.answer(Client.FORM_CANCELLED)
    await callback.answer()


@router.callback_query(F.data == "fsm_remind:continue")
async def fsm_remind_continue(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Re-send the message for the current FSM step."""
    current = await state.get_state()
    data = await state.get_data()

    if not current:
        try:
            await callback.message.edit_text(Client.NO_ACTIVE_FORM)
        except Exception:
            pass
        await callback.answer()
        return

    await callback.answer(Client.LETS_CONTINUE)

    # Map state → message + keyboard
    if current == OrderFSM.service_type.state:
        await callback.message.answer(
            "Выберите тип услуги:", reply_markup=service_type_kb()
        )
    elif current == OrderFSM.brand.state:
        async with async_session() as session:
            brands = await _load_client_brands(session)
        await callback.message.answer(
            "Выберите бренд самоката:", reply_markup=brands_kb(brands)
        )
    elif current == OrderFSM.brand_custom.state:
        await callback.message.answer("Введите название бренда самоката:")
    elif current == OrderFSM.model.state:
        brand_id = data.get("brand_id")
        if brand_id:
            async with async_session() as session:
                mods = (
                    (
                        await session.execute(
                            select(Model)
                            .where(Model.brand_id == brand_id)
                            .order_by(Model.name)
                        )
                    )
                    .scalars()
                    .all()
                )
            await callback.message.answer(
                "Выберите модель:", reply_markup=models_kb(mods)
            )
        else:
            await callback.message.answer("Введите название модели самоката:")
    elif current == OrderFSM.model_custom.state:
        await callback.message.answer("Введите название модели самоката:")
    elif current == OrderFSM.malfunction_type.state:
        await callback.message.answer(
            "Выберите категорию неисправности:", reply_markup=malfunction_type_kb()
        )
    elif current == OrderFSM.upgrade_category.state:
        await callback.message.answer(
            "Выберите категорию апгрейда:", reply_markup=upgrade_category_kb()
        )
    elif current == OrderFSM.problem_description.state:
        stype = data.get("service_type", "repair")
        if stype == "repair":
            await callback.message.answer("Опишите проблему:")
        else:
            await callback.message.answer("Опишите, что вы хотите сделать:")
    elif current == OrderFSM.location_method.state:
        await callback.message.answer(
            "Выберете ближайшее к вам метро "
            "(Подберем самый ближайший сервис, под вашу проблему)",
            reply_markup=location_method_kb(),
        )
    elif current == OrderFSM.metro_search.state:
        await callback.message.answer("Введите название станции метро (или его часть):")
    elif current == OrderFSM.metro_confirm.state:
        metro = data.get("metro_station", "")
        if metro:
            await callback.message.answer(
                f"Найдена станция: <b>{e(metro)}</b>\n\nВсё верно?",
                reply_markup=metro_confirm_kb(metro),
            )
        else:
            await callback.message.answer(
                "Введите название станции метро (или его часть):"
            )
            await state.set_state(OrderFSM.metro_search)
    elif current == OrderFSM.calendar_date.state:
        await callback.message.answer(
            Client.Order.SELECT_DATE, reply_markup=calendar_kb()
        )
    elif current == OrderFSM.calendar_time.state:
        date_str = data.get("scheduled_date", "")
        await callback.message.answer(
            f"Выберите время на {date_str}:", reply_markup=time_slots_kb(date_str)
        )
    elif current == OrderFSM.confirm.state:
        await callback.message.answer(
            "Нажмите кнопку подтверждения:", reply_markup=confirm_kb()
        )
    else:
        # Unknown state — should not happen since reminder only fires for OrderFSM
        await callback.message.answer("Нет активной формы.")


# CATCH-ALL


@router.message(OrderFSM.service_type)
@router.message(OrderFSM.brand)
@router.message(OrderFSM.model)
@router.message(OrderFSM.malfunction_type)
@router.message(OrderFSM.upgrade_category)
@router.message(OrderFSM.location_method)
@router.message(OrderFSM.calendar_date)
@router.message(OrderFSM.calendar_time)
@router.message(OrderFSM.confirm)
@router.message(OrderFSM.metro_confirm)
async def handle_unexpected_text_in_fsm(
    message: types.Message, state: FSMContext
) -> None:
    await message.answer(Client.USE_BUTTONS)
