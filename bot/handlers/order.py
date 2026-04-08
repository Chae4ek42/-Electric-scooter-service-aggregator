"""Order creation FSM handlers -- full flow."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from pydantic import ValidationError
from sqlalchemy import select

from bot.core.config import ADMIN_USERNAMES, SUPPORT_USER
from bot.core.database import async_session
from bot.ui.keyboards import (
    brands_kb,
    calendar_kb,
    confirm_kb,
    location_method_kb,
    main_menu_kb,
    malfunction_type_kb,
    metro_confirm_kb,
    models_kb,
    orders_list_action_kb,
    order_select_kb,
    payment_kb,
    service_type_kb,
    support_kb,
    time_slots_kb,
    upgrade_category_kb,
)
from bot.services.metro_search import best_metro_match, top_metro_matches
from bot.services.ranking import RankingContext, rank_services
from bot.domain.models import Brand, MetroStation, Model, Order, Service, User
from bot.domain.schemas import BrandNameInput, MetroTextInput, ModelNameInput, ProblemDescription
from bot.domain.states import OrderFSM

logger = logging.getLogger(__name__)
router = Router(name="order")

_MENU_TEXTS = ("Оставить заявку", "Мои заявки", "Техподдержка")


def _is_admin(username: str | None) -> bool:
    return bool(username) and username.lower() in ADMIN_USERNAMES


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
    await message.answer("Процедура прервана.")
    if message.text == "Оставить заявку":
        await state.set_state(OrderFSM.service_type)
        await message.answer("Выберите тип услуги:", reply_markup=service_type_kb())
    elif message.text == "Мои заявки":
        await my_orders_interrupt(message, state)
    elif message.text == "Техподдержка":
        await message.answer("Техническая поддержка:", reply_markup=support_kb(SUPPORT_USER))
    return True


# 1. ENTRY

@router.message(F.text == "Оставить заявку")
async def start_order(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderFSM.service_type)
    logger.info("user=%s started order flow", message.from_user.id)
    await message.answer("Выберите тип услуги:", reply_markup=service_type_kb())


# 2. SERVICE TYPE

@router.callback_query(OrderFSM.service_type, F.data.startswith("stype:"))
async def pick_service_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    stype = callback.data.split(":")[1]
    await state.update_data(service_type=stype)
    logger.info("user=%s picked service_type=%s", callback.from_user.id, stype)
    await state.set_state(OrderFSM.brand)
    async with async_session() as session:
        brands = (
            (await session.execute(select(Brand).order_by(Brand.name))).scalars().all()
        )
    await _safe_edit_or_answer(callback, "Выберите бренд самоката:", brands_kb(brands))


# 3. BRAND

@router.callback_query(OrderFSM.brand, F.data.startswith("brand:"))
async def pick_brand(callback: types.CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[1]
    if raw == "other":
        await state.update_data(brand_id=None, brand_custom_name=None)
        await state.set_state(OrderFSM.brand_custom)
        logger.info("user=%s chose custom brand input", callback.from_user.id)
        await _safe_edit_or_answer(callback, "Введите название бренда самоката:")
        return

    brand_id = int(raw)
    await state.update_data(brand_id=brand_id, brand_custom_name=None)
    logger.info("user=%s picked brand_id=%s", callback.from_user.id, brand_id)
    await state.set_state(OrderFSM.model)
    async with async_session() as session:
        models = (
            (await session.execute(
                select(Model).where(Model.brand_id == brand_id).order_by(Model.name)
            )).scalars().all()
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
        err_msg = exc.errors()[0]["msg"] if exc.errors() else "Некорректный ввод"
        await message.answer(err_msg)
        return
    logger.info("user=%s entered custom brand=%r", message.from_user.id, validated.text)
    await state.update_data(brand_id=None, brand_custom_name=validated.text)
    await state.update_data(model_id=None, model_custom_name=None)
    await state.set_state(OrderFSM.model_custom)
    await message.answer("Введите название модели самоката:")


# 4. MODEL

@router.callback_query(OrderFSM.model, F.data.startswith("model:"))
async def pick_model(callback: types.CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[1]
    if raw == "other":
        await state.update_data(model_id=None, model_custom_name=None)
        await state.set_state(OrderFSM.model_custom)
        await _safe_edit_or_answer(
            callback, "Введите название модели самоката (например: Xiaomi Mi 4 Pro):"
        )
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
        err_msg = exc.errors()[0]["msg"] if exc.errors() else "Некорректный ввод"
        await message.answer(err_msg)
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
            await state.update_data(model_id=placeholder.id, model_custom_name=validated.text)
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
        await _safe_edit_or_answer(event, "Выберите категорию неисправности:", malfunction_type_kb())
    else:
        await state.set_state(OrderFSM.upgrade_category)
        await _safe_edit_or_answer(event, "Выберите категорию апгрейда:", upgrade_category_kb())


# 5. MALFUNCTION TYPE (repair)

@router.callback_query(OrderFSM.malfunction_type, F.data.startswith("malf:"))
async def pick_malfunction(callback: types.CallbackQuery, state: FSMContext) -> None:
    category_name = callback.data.split(":")[1]
    await state.update_data(malfunction_category=category_name, upgrade_category=None)
    logger.info("user=%s picked malfunction=%s", callback.from_user.id, category_name)
    await state.set_state(OrderFSM.problem_description)
    await _safe_edit_or_answer(callback, "Опишите проблему:")


# 5a. UPGRADE CATEGORY

@router.callback_query(OrderFSM.upgrade_category, F.data.startswith("upcat:"))
async def pick_upgrade_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":")[1]
    await state.update_data(upgrade_category=cat, malfunction_category=None)
    logger.info("user=%s picked upgrade_category=%s", callback.from_user.id, cat)
    if cat == "Гидроизоляция":
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback,
            "Как вы хотите указать ближайшую станцию метро?",
            location_method_kb(),
        )
    else:
        await state.set_state(OrderFSM.problem_description)
        await _safe_edit_or_answer(callback, "Опишите, что вы хотите сделать:")


# 5b. PROBLEM DESCRIPTION

@router.message(OrderFSM.problem_description, F.text)
async def pick_problem_description(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = ProblemDescription(text=message.text)
    except ValidationError as exc:
        err_msg = exc.errors()[0]["msg"] if exc.errors() else "Некорректный ввод"
        await message.answer(err_msg)
        return
    logger.info("user=%s entered problem description (%d chars)", message.from_user.id, len(validated.text))
    await state.update_data(problem_description=validated.text)
    await state.set_state(OrderFSM.location_method)
    await message.answer(
        "Как вы хотите указать ближайшую станцию метро?",
        reply_markup=location_method_kb(),
    )


# 6. LOCATION / METRO

@router.callback_query(OrderFSM.location_method, F.data == "loc:metro")
async def choose_metro_text(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.metro_search)
    await _safe_edit_or_answer(callback, "Введите название станции метро (или его часть):")


@router.message(OrderFSM.metro_search, F.text)
async def handle_metro_text(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_interrupt(message, state):
        return
    try:
        validated = MetroTextInput(text=message.text)
    except ValidationError as e:
        err_msg = e.errors()[0]["msg"] if e.errors() else "Некорректный ввод"
        await message.answer(f"{err_msg}\nПопробуйте ещё раз:")
        return

    async with async_session() as session:
        stations = (await session.execute(select(MetroStation))).scalars().all()

    matches = top_metro_matches(validated.text, stations, limit=5)
    if not matches:
        await message.answer("Станция не найдена. Попробуйте ввести название точнее:")
        return

    if len(matches) == 1 or matches[0][1] > 0.85:
        station = matches[0][0]
        await state.update_data(metro_station=station.name)
        await state.set_state(OrderFSM.metro_confirm)
        logger.info("user=%s metro match: %s (score=%.2f)", message.from_user.id, station.name, matches[0][1])
        await message.answer(
            f"Найдена станция: *{station.name}* ({station.line})\n\nВсё верно?",
            reply_markup=metro_confirm_kb(station.name),
            parse_mode="Markdown",
        )
    else:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from bot.ui.keyboards import BACK_BTN
        buttons = []
        for st, score in matches:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{st.name} ({st.line})",
                    callback_data=f"metro_pick:{st.id}",
                )
            ])
        buttons.append([InlineKeyboardButton(text="Ввести заново", callback_data="metro_retry")])
        buttons.append([BACK_BTN])
        await state.set_state(OrderFSM.metro_confirm)
        await message.answer(
            "Найдено несколько станций. Выберите нужную:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


# 7. METRO CONFIRM

@router.callback_query(OrderFSM.metro_confirm, F.data == "metro_ok")
async def metro_confirmed(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.calendar_date)
    await _safe_edit_or_answer(callback, "Выберите дату:", calendar_kb())


@router.callback_query(OrderFSM.metro_confirm, F.data.startswith("metro_pick:"))
async def metro_picked_from_list(callback: types.CallbackQuery, state: FSMContext) -> None:
    station_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        station = (
            await session.execute(select(MetroStation).where(MetroStation.id == station_id))
        ).scalar_one_or_none()
    if station is None:
        await callback.answer("Станция не найдена", show_alert=True)
        return
    await state.update_data(metro_station=station.name)
    logger.info("user=%s picked metro=%s from list", callback.from_user.id, station.name)
    await state.set_state(OrderFSM.calendar_date)
    await _safe_edit_or_answer(callback, "Выберите дату:", calendar_kb())


@router.callback_query(OrderFSM.metro_confirm, F.data == "metro_retry")
async def metro_retry(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFSM.metro_search)
    await _safe_edit_or_answer(callback, "Введите название станции метро (или его часть):")


# 8. CALENDAR -- DATE

@router.callback_query(OrderFSM.calendar_date, F.data.startswith("date:"))
async def pick_date(callback: types.CallbackQuery, state: FSMContext) -> None:
    date_str = callback.data.split(":")[1]
    await state.update_data(scheduled_date=date_str)
    logger.info("user=%s picked date=%s", callback.from_user.id, date_str)
    await state.set_state(OrderFSM.calendar_time)
    await _safe_edit_or_answer(callback, f"Выберите время на {date_str}:", time_slots_kb(date_str))


# 9. CALENDAR -- TIME -> ranking -> confirm

@router.callback_query(OrderFSM.calendar_time, F.data.startswith("time:"))
async def pick_time(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка формата времени", show_alert=True)
        return
    time_str = f"{parts[1]}:{parts[2]}"
    await state.update_data(scheduled_time=time_str)
    data = await state.get_data()

    service_id: int | None = None
    diagnostics_price: float | None = None
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
        )
        result = await rank_services(ctx, session, limit=1)

        logger.info(
            "user=%s ranking: matches=%d time_fallback=%s suggested=%s",
            callback.from_user.id,
            len(result.matches),
            result.time_fallback,
            result.suggested_time,
        )

        if result.time_fallback and result.suggested_time:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"Записаться на {result.suggested_time}",
                    callback_data=f"time:{result.suggested_time}",
                )],
                [InlineKeyboardButton(
                    text="Выбрать другую дату",
                    callback_data="back",
                )],
            ])
            await _safe_edit_or_answer(
                callback,
                f"К сожалению, в {time_str} подходящие сервисы не работают.\n"
                f"Ближайшее доступное время: *{result.suggested_time}*",
                kb,
            )
            return

        if result.matches:
            best = result.matches[0]
            service_id = best.service.id
            diagnostics_price = best.service.diagnostics_price

    if service_id is None:
        await state.clear()
        await _safe_edit_or_answer(
            callback,
            "К сожалению, подходящих сервис-центров не найдено. Попробуйте позже.",
        )
        return

    await state.update_data(service_id=service_id, diagnostics_price=diagnostics_price)
    await state.set_state(OrderFSM.confirm)

    custom_name = data.get("model_custom_name")
    brand_custom = data.get("brand_custom_name")
    if brand_custom:
        model_display = f"{brand_custom} {custom_name}" if custom_name else brand_custom
    elif custom_name:
        brand_name = model.brand.name if model and model.brand else ""
        model_display = f"{brand_name} {custom_name}".strip()
    elif model:
        model_display = f"{model.brand.name} {model.name}" if model.brand else model.name
    else:
        model_display = ""

    price_line = ""
    if diagnostics_price:
        price_line = f"\nСтоимость диагностики: {diagnostics_price:.0f} руб."

    summary = (
        "*Подтвердите заявку:*\n\n"
        f"Модель: {model_display}\n"
        f"Метро: {data.get('metro_station', '')}\n"
        f"Дата: {data.get('scheduled_date', '')}\n"
        f"Время: {time_str}"
        f"{price_line}"
    )

    logger.info("user=%s confirm screen: service_id=%s diag_price=%s", callback.from_user.id, service_id, diagnostics_price)
    await _safe_edit_or_answer(callback, summary, confirm_kb())


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
            await session.execute(select(Service).where(Service.id == data["service_id"]))
        ).scalar_one_or_none()
        if not svc or not svc.is_available:
            await state.clear()
            await _safe_edit_or_answer(callback, "Сервис-центр стал недоступен. Начните заново.")
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
            status="awaiting_payment",
        )
        session.add(order)
        await session.commit()
        order_id = order.id

    await state.clear()

    price_text = ""
    dp = data.get("diagnostics_price")
    if dp:
        price_text = f"\n\nСтоимость диагностики: {dp:.0f} руб."

    logger.info("user=%s order #%s created (service_id=%s)", callback.from_user.id, order_id, data["service_id"])

    await _safe_edit_or_answer(
        callback,
        f"*Заявка №{order_id} создана!*{price_text}\n\n"
        "Система предоплаты находится в разработке.\n"
        "Мы свяжемся с вами для подтверждения записи.",
    )
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
    await state.clear()
    await _safe_edit_or_answer(
        callback,
        "Система оплаты находится в разработке.\n"
        "Мы свяжемся с вами для подтверждения записи.",
    )


@router.callback_query(F.data.startswith("pay:cancel:"))
async def payment_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    order_id = int(parts[2])
    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if order and order.status not in ("cancelled", "completed"):
            order.status = "cancelled"
            await session.commit()
            logger.info("user=%s cancelled order #%s via payment", callback.from_user.id, order_id)
    await state.clear()
    await _safe_edit_or_answer(callback, f"Заявка №{order_id} отменена.")


@router.callback_query(F.data == "noop")
async def noop_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("Недоступно", show_alert=False)


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
            brands = (await session.execute(select(Brand).order_by(Brand.name))).scalars().all()
        await _safe_edit_or_answer(callback, "Выберите бренд самоката:", brands_kb(brands))

    elif current == OrderFSM.model.state:
        await state.set_state(OrderFSM.brand)
        async with async_session() as session:
            brands = (await session.execute(select(Brand).order_by(Brand.name))).scalars().all()
        await _safe_edit_or_answer(callback, "Выберите бренд самоката:", brands_kb(brands))

    elif current == OrderFSM.model_custom.state:
        if data.get("brand_custom_name"):
            await state.set_state(OrderFSM.brand_custom)
            await _safe_edit_or_answer(callback, "Введите название бренда самоката:")
        else:
            await state.set_state(OrderFSM.model)
            brand_id = data.get("brand_id")
            async with async_session() as session:
                models = (
                    (await session.execute(
                        select(Model).where(Model.brand_id == brand_id).order_by(Model.name)
                    )).scalars().all()
                )
            await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))

    elif current == OrderFSM.malfunction_type.state:
        brand_id = data.get("brand_id")
        if brand_id:
            await state.set_state(OrderFSM.model)
            async with async_session() as session:
                models = (
                    (await session.execute(
                        select(Model).where(Model.brand_id == brand_id).order_by(Model.name)
                    )).scalars().all()
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
                    (await session.execute(
                        select(Model).where(Model.brand_id == brand_id).order_by(Model.name)
                    )).scalars().all()
                )
            await _safe_edit_or_answer(callback, "Выберите модель:", models_kb(models))
        else:
            await state.set_state(OrderFSM.model_custom)
            await _safe_edit_or_answer(callback, "Введите название модели самоката:")

    elif current == OrderFSM.problem_description.state:
        stype = data.get("service_type", "repair")
        if stype == "repair":
            await state.set_state(OrderFSM.malfunction_type)
            await _safe_edit_or_answer(callback, "Выберите категорию неисправности:", malfunction_type_kb())
        else:
            await state.set_state(OrderFSM.upgrade_category)
            await _safe_edit_or_answer(callback, "Выберите категорию апгрейда:", upgrade_category_kb())

    elif current == OrderFSM.location_method.state:
        upcat = data.get("upgrade_category")
        stype = data.get("service_type", "repair")
        if upcat == "Гидроизоляция":
            await state.set_state(OrderFSM.upgrade_category)
            await _safe_edit_or_answer(callback, "Выберите категорию апгрейда:", upgrade_category_kb())
        elif stype == "repair":
            await state.set_state(OrderFSM.problem_description)
            await _safe_edit_or_answer(callback, "Опишите проблему:")
        else:
            await state.set_state(OrderFSM.problem_description)
            await _safe_edit_or_answer(callback, "Опишите, что вы хотите сделать:")

    elif current in (OrderFSM.metro_search.state, OrderFSM.metro_confirm.state):
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback, "Как вы хотите указать ближайшую станцию метро?", location_method_kb()
        )

    elif current == OrderFSM.calendar_date.state:
        await state.set_state(OrderFSM.location_method)
        await _safe_edit_or_answer(
            callback, "Как вы хотите указать ближайшую станцию метро?", location_method_kb()
        )

    elif current == OrderFSM.calendar_time.state:
        await state.set_state(OrderFSM.calendar_date)
        await _safe_edit_or_answer(callback, "Выберите дату:", calendar_kb())

    elif current == OrderFSM.confirm.state:
        date_str = data.get("scheduled_date", "")
        await state.set_state(OrderFSM.calendar_time)
        await _safe_edit_or_answer(callback, f"Выберите время на {date_str}:", time_slots_kb(date_str))

    else:
        await state.clear()
        await _safe_edit_or_answer(callback, "Выберите тип услуги:", service_type_kb())


# MY ORDERS

@router.message(F.text == "Мои заявки")
async def my_orders_interrupt(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("Процедура прервана.")

    status_map = {
        "awaiting_payment": "Ожидает оплаты",
        "accepted": "Принята",
        "interrupted": "Прервана",
        "completed": "Завершена",
        "cancelled": "Отменена",
        "pending": "Ожидает",
        "paid": "Оплачено",
    }

    async with async_session() as session:
        orders = (
            (await session.execute(
                select(Order)
                .where(Order.user_id == message.from_user.id)
                .where(Order.status != "cancelled")
                .order_by(Order.created_at.desc())
            )).scalars().all()
        )

        if not orders:
            await message.answer("У вас пока нет активных заявок.")
            return

        lines: list[str] = []
        for o in orders:
            model = None
            if o.model_id:
                model = (
                    await session.execute(select(Model).where(Model.id == o.model_id))
                ).scalar_one_or_none()
            service = (
                await session.execute(select(Service).where(Service.id == o.service_id))
            ).scalar_one_or_none()

            if o.brand_custom_name:
                model_name = f"{o.brand_custom_name} {o.model_custom_name or ''}".strip()
            elif o.model_custom_name and model:
                model_name = f"{model.brand.name} {o.model_custom_name}"
            elif model:
                model_name = f"{model.brand.name} {model.name}"
            else:
                model_name = o.model_custom_name or ""

            status_text = status_map.get(o.status, o.status)

            if o.status == "awaiting_payment":
                svc_display = "будет назначен после оплаты"
            else:
                svc_display = service.name if service else ""

            lines.append(
                f"*Заявка №{o.id}*  --  {status_text}\n"
                f"  Модель: {model_name}\n"
                f"  Сервис-центр: {svc_display}\n"
                f"  Дата: {o.scheduled_date or ''} {o.scheduled_time or ''}\n"
                f"  Метро: {o.metro_station or ''}"
            )

        has_active = any(o.status == "awaiting_payment" for o in orders)
        markup = orders_list_action_kb() if has_active else None

    await message.answer("\n\n".join(lines), reply_markup=markup, parse_mode="Markdown")


# ORDERS ACTION CALLBACKS

@router.callback_query(F.data == "orders:action:pay")
async def orders_action_pay(callback: types.CallbackQuery) -> None:
    async with async_session() as session:
        orders = (
            (await session.execute(
                select(Order)
                .where(Order.user_id == callback.from_user.id)
                .where(Order.status == "awaiting_payment")
                .order_by(Order.created_at.desc())
            )).scalars().all()
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
            (await session.execute(
                select(Order)
                .where(Order.user_id == callback.from_user.id)
                .where(Order.status.in_(["awaiting_payment", "accepted"]))
                .order_by(Order.created_at.desc())
            )).scalars().all()
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
            if order.status not in ("awaiting_payment", "accepted"):
                await callback.answer("Эту заявку нельзя отменить.", show_alert=True)
                return
            order.status = "cancelled"
            await session.commit()
            logger.info("user=%s cancelled order #%s", callback.from_user.id, order_id)
            await callback.message.answer(f"Заявка №{order_id} отменена.")
            await callback.answer()
        elif action == "pay":
            if order.status != "awaiting_payment":
                await callback.answer("Эта заявка не ожидает оплаты.", show_alert=True)
                return
            await callback.message.answer(f"Для оплаты заявки №{order_id} свяжитесь с оператором.")
            await callback.answer()
        else:
            await callback.answer("Неизвестное действие.", show_alert=True)


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
async def handle_unexpected_text_in_fsm(message: types.Message, state: FSMContext) -> None:
    await message.answer("Пожалуйста, используйте кнопки для навигации.")
