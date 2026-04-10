"""FSM state groups for the order flow and partner bot."""

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


class RegistrationFSM(StatesGroup):
    reg_name = State()
    reg_service_type = State()
    reg_upgrade_categories = State()
    reg_hydroisolation = State()
    reg_address = State()
    reg_metro_search = State()
    reg_metro_confirm = State()
    reg_phone = State()
    reg_telegram = State()
    reg_working_days = State()
    reg_hours = State()
    reg_diagnostics = State()
    reg_diag_included = State()
    reg_legal_form = State()
    reg_tax_system = State()
    reg_bank_details = State()
    reg_confirm = State()


class PartnerProfileFSM(StatesGroup):
    edit_field_select = State()
    edit_field_value = State()


class PartnerOrderFSM(StatesGroup):
    reject_reason = State()
