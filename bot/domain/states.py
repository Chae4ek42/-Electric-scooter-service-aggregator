"""FSM state groups for the order flow."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OrderFSM(StatesGroup):
    service_type = State()  # repair / upgrade
    brand = State()  # choose brand
    model = State()  # choose model
    model_custom = State()  # text input when user picks «Другое»
    malfunction_type = State()  # Механика / Электрика (only for repair)
    specific_problem = State()  # choose service
    location_method = State()  # geo / metro text-search
    metro_search = State()  # waiting for text input of metro name
    metro_confirm = State()  # confirm fuzzy match result
    calendar_date = State()  # choose date
    calendar_time = State()  # choose time
    confirm = State()  # final confirmation
    payment = State()  # awaiting payment (stub)
