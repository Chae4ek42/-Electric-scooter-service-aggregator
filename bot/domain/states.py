"""FSM state groups for the order flow."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OrderFSM(StatesGroup):
    service_type = State()  # repair / upgrade
    brand = State()  # choose brand
    brand_custom = State()  # text input for custom brand name
    model = State()  # choose model
    model_custom = State()  # text input when user picks «Другое»
    malfunction_type = State()  # Механика / Электрика (only for repair)
    upgrade_category = (
        State()
    )  # Гидроизоляция / Окраска / Прошивка / Изменение конструкции
    problem_description = State()  # describe the problem (text input)
    location_method = State()  # geo / metro text-search
    metro_search = State()  # waiting for text input of metro name
    metro_confirm = State()  # confirm fuzzy match result
    calendar_date = State()  # choose date
    calendar_time = State()  # choose time
    confirm = State()  # final confirmation
