"""Partner bot tests — schemas, models, keyboards, states, imports."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError


# ── Partner schemas ────────────────────────────────────────────


class TestServiceNameInput:
    def test_valid(self):
        from client_bot.domain.schemas import ServiceNameInput

        r = ServiceNameInput(text="Ремонт Pro")
        assert r.text == "Ремонт Pro"

    def test_too_short(self):
        from client_bot.domain.schemas import ServiceNameInput

        with pytest.raises(ValidationError):
            ServiceNameInput(text="РП")

    def test_digits_only(self):
        from client_bot.domain.schemas import ServiceNameInput

        with pytest.raises(ValidationError):
            ServiceNameInput(text="12345")

    def test_max_length(self):
        from client_bot.domain.schemas import ServiceNameInput

        with pytest.raises(ValidationError):
            ServiceNameInput(text="A" * 201)


class TestAddressInput:
    def test_valid(self):
        from client_bot.domain.schemas import AddressInput

        r = AddressInput(text="Москва, ул. Ленина, д. 10")
        assert "Ленина" in r.text

    def test_too_short(self):
        from client_bot.domain.schemas import AddressInput

        with pytest.raises(ValidationError):
            AddressInput(text="Москва")


class TestPhoneInput:
    def test_valid_plus(self):
        from client_bot.domain.schemas import PhoneInput

        r = PhoneInput(text="+7 999 123-45-67")
        assert r.text.startswith("+7")

    def test_valid_no_plus(self):
        from client_bot.domain.schemas import PhoneInput

        r = PhoneInput(text="89991234567")
        assert r.text == "89991234567"

    def test_invalid_letters(self):
        from client_bot.domain.schemas import PhoneInput

        with pytest.raises(ValidationError):
            PhoneInput(text="phone abc")

    def test_too_short(self):
        from client_bot.domain.schemas import PhoneInput

        with pytest.raises(ValidationError):
            PhoneInput(text="123")


class TestTelegramHandleInput:
    def test_valid_with_at(self):
        from client_bot.domain.schemas import TelegramHandleInput

        r = TelegramHandleInput(text="@my_handle")
        assert r.text == "my_handle"

    def test_valid_without_at(self):
        from client_bot.domain.schemas import TelegramHandleInput

        r = TelegramHandleInput(text="my_handle")
        assert r.text == "my_handle"

    def test_too_short(self):
        from client_bot.domain.schemas import TelegramHandleInput

        with pytest.raises(ValidationError):
            TelegramHandleInput(text="ab")

    def test_special_chars(self):
        from client_bot.domain.schemas import TelegramHandleInput

        with pytest.raises(ValidationError):
            TelegramHandleInput(text="my handle!")


class TestWorkHoursInput:
    def test_valid_dash(self):
        from client_bot.domain.schemas import WorkHoursInput

        r = WorkHoursInput(text="09:00-21:00")
        assert r.text == "09:00-21:00"

    def test_valid_endash(self):
        from client_bot.domain.schemas import WorkHoursInput

        r = WorkHoursInput(text="09:00\u201321:00")
        assert "09:00" in r.text

    def test_invalid_format(self):
        from client_bot.domain.schemas import WorkHoursInput

        with pytest.raises(ValidationError):
            WorkHoursInput(text="9-21")

    def test_invalid_text(self):
        from client_bot.domain.schemas import WorkHoursInput

        with pytest.raises(ValidationError):
            WorkHoursInput(text="круглосуточно")


class TestDiagnosticsPriceInput:
    def test_zero(self):
        from client_bot.domain.schemas import DiagnosticsPriceInput

        r = DiagnosticsPriceInput(text="0")
        assert r.text == "0"

    def test_positive(self):
        from client_bot.domain.schemas import DiagnosticsPriceInput

        r = DiagnosticsPriceInput(text="500")
        assert r.text == "500"

    def test_negative(self):
        from client_bot.domain.schemas import DiagnosticsPriceInput

        with pytest.raises(ValidationError):
            DiagnosticsPriceInput(text="-100")

    def test_text(self):
        from client_bot.domain.schemas import DiagnosticsPriceInput

        with pytest.raises(ValidationError):
            DiagnosticsPriceInput(text="бесплатно")


class TestRejectReasonInput:
    def test_valid(self):
        from client_bot.domain.schemas import RejectReasonInput

        r = RejectReasonInput(text="Нет запчастей на данную модель")
        assert "запчастей" in r.text

    def test_too_short(self):
        from client_bot.domain.schemas import RejectReasonInput

        with pytest.raises(ValidationError):
            RejectReasonInput(text="Не")

    def test_too_long(self):
        from client_bot.domain.schemas import RejectReasonInput

        with pytest.raises(ValidationError):
            RejectReasonInput(text="X" * 501)


# ── Bank / legal schemas ──────────────────────────────────────


class TestBankAccountInput:
    def test_valid(self):
        from client_bot.domain.schemas import BankAccountInput

        r = BankAccountInput(text="40702810938000012345")
        assert len(r.text) == 20

    def test_too_short(self):
        from client_bot.domain.schemas import BankAccountInput

        with pytest.raises(ValidationError):
            BankAccountInput(text="1234567890")

    def test_letters(self):
        from client_bot.domain.schemas import BankAccountInput

        with pytest.raises(ValidationError):
            BankAccountInput(text="4070281093800001234a")


class TestBikInput:
    def test_valid(self):
        from client_bot.domain.schemas import BikInput

        r = BikInput(text="044525225")
        assert len(r.text) == 9

    def test_too_short(self):
        from client_bot.domain.schemas import BikInput

        with pytest.raises(ValidationError):
            BikInput(text="04452")

    def test_too_long(self):
        from client_bot.domain.schemas import BikInput

        with pytest.raises(ValidationError):
            BikInput(text="0445252251")


class TestCorrAccountInput:
    def test_valid(self):
        from client_bot.domain.schemas import CorrAccountInput

        r = CorrAccountInput(text="30101810400000000225")
        assert len(r.text) == 20

    def test_invalid(self):
        from client_bot.domain.schemas import CorrAccountInput

        with pytest.raises(ValidationError):
            CorrAccountInput(text="301018104")


class TestBankNameInput:
    def test_valid(self):
        from client_bot.domain.schemas import BankNameInput

        r = BankNameInput(text="ПАО Сбербанк")
        assert "Сбербанк" in r.text

    def test_too_short(self):
        from client_bot.domain.schemas import BankNameInput

        with pytest.raises(ValidationError):
            BankNameInput(text="ПА")


class TestOrgNameInput:
    def test_valid(self):
        from client_bot.domain.schemas import OrgNameInput

        r = OrgNameInput(text="ИП Звездилин Сергей Леонидович")
        assert "Звездилин" in r.text

    def test_too_short(self):
        from client_bot.domain.schemas import OrgNameInput

        with pytest.raises(ValidationError):
            OrgNameInput(text="ИП")


class TestInnInput:
    def test_valid_10(self):
        from client_bot.domain.schemas import InnInput

        r = InnInput(text="7707083893")
        assert len(r.text) == 10

    def test_valid_12(self):
        from client_bot.domain.schemas import InnInput

        r = InnInput(text="770708389312")
        assert len(r.text) == 12

    def test_invalid_11(self):
        from client_bot.domain.schemas import InnInput

        with pytest.raises(ValidationError):
            InnInput(text="77070838931")

    def test_letters(self):
        from client_bot.domain.schemas import InnInput

        with pytest.raises(ValidationError):
            InnInput(text="770708389a")


# ── Models ─────────────────────────────────────────────────────


class TestPartnerModels:
    def test_service_has_owner_fields(self):
        from client_bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        expected = {
            "id",
            "telegram_id",
            "status",
            "registered_at",
            "approved_at",
            "approved_by",
            "draft_name",
            "draft_service_type",
            "draft_category",
            "draft_address",
            "draft_metro",
            "draft_phone",
            "draft_telegram",
            "draft_open_time",
            "draft_close_time",
            "draft_hydroisolation",
            "draft_diagnostics_price",
            "draft_diag_included",
            "draft_upgrade_categories",
            "draft_working_days",
            "draft_legal_form",
            "draft_tax_system",
            "draft_bank_account",
            "draft_bank_name",
            "draft_bik",
            "draft_corr_account",
            "draft_org_name",
            "draft_inn",
        }
        assert expected.issubset(cols), f"Missing: {expected - cols}"

    def test_service_new_fields(self):
        from client_bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        assert "upgrade_categories" in cols
        assert "working_days" in cols

    def test_service_owner_settings_table(self):
        from client_bot.domain.models import ServiceOwnerSettings

        cols = {c.name for c in ServiceOwnerSettings.__table__.columns}
        assert "notif_new_order" in cols
        assert "notif_cancel" in cols

    def test_sheets_retry_queue_table(self):
        from client_bot.domain.models import SheetsRetryQueue

        cols = {c.name for c in SheetsRetryQueue.__table__.columns}
        assert "operation" in cols
        assert "payload_json" in cols
        assert "attempts" in cols

    def test_order_partner_fields(self):
        from client_bot.domain.models import Order

        cols = {c.name for c in Order.__table__.columns}
        assert "partner_comment" in cols
        assert "reject_reason" in cols
        assert "accepted_at" in cols
        assert "completed_at" in cols


# ── FSM States ─────────────────────────────────────────────────


class TestPartnerStates:
    def test_registration_fsm(self):
        from client_bot.domain.states import RegistrationFSM

        expected = [
            "reg_name",
            "reg_service_type",
            "reg_upgrade_categories",
            "reg_hydroisolation",
            "reg_address",
            "reg_metro_search",
            "reg_metro_confirm",
            "reg_phone",
            "reg_working_days",
            "reg_hours",
            "reg_diagnostics",
            "reg_diag_included",
            "reg_legal_form",
            "reg_tax_system",
            "reg_bank_details",
            "reg_confirm",
        ]
        for s in expected:
            assert hasattr(RegistrationFSM, s), f"Missing state: {s}"

    def test_no_old_category_state(self):
        from client_bot.domain.states import RegistrationFSM

        assert not hasattr(RegistrationFSM, "reg_service_category")

    def test_partner_profile_fsm(self):
        from client_bot.domain.states import PartnerProfileFSM

        assert hasattr(PartnerProfileFSM, "edit_field_select")
        assert hasattr(PartnerProfileFSM, "edit_field_value")

    def test_partner_order_fsm(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "reject_reason")


# ── Partner keyboards ──────────────────────────────────────────


class TestPartnerKeyboards:
    def test_main_menu_has_support(self):
        from partner_bot.ui.keyboards import partner_main_menu_kb

        kb = partner_main_menu_kb()
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert "Входящие заявки" in texts
        assert "История заявок" in texts
        assert "Редактировать профиль" in texts
        assert "Настройки уведомлений" in texts
        assert "Статус сервиса" in texts
        assert "Поддержка" in texts

    def test_pending_menu_has_support(self):
        from partner_bot.ui.keyboards import partner_pending_menu_kb

        kb = partner_pending_menu_kb(has_draft=False)
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert "Моя анкета" in texts
        assert "Поддержка" in texts
        assert "Продолжить заполнение" not in texts

    def test_pending_menu_with_draft(self):
        from partner_bot.ui.keyboards import partner_pending_menu_kb

        kb = partner_pending_menu_kb(has_draft=True)
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert "Продолжить заполнение" in texts

    def test_reg_start_kb(self):
        from partner_bot.ui.keyboards import reg_start_kb

        kb = reg_start_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Зарегистрировать сервис" in texts

    def test_reg_service_type_no_complex(self):
        from partner_bot.ui.keyboards import reg_service_type_kb

        kb = reg_service_type_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Ремонт" in texts
        assert "Апгрейд" in texts
        assert "Комплексный" not in texts

    def test_upgrade_categories_kb_empty(self):
        from partner_bot.ui.keyboards import reg_upgrade_categories_kb

        kb = reg_upgrade_categories_kb(set())
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Окраска" in texts
        assert "Прошивка" in texts
        assert "Изменение конструкции" in texts
        assert "Доп оснащение" in texts
        assert not any("Готово" in t for t in texts)

    def test_upgrade_categories_kb_selected(self):
        from partner_bot.ui.keyboards import reg_upgrade_categories_kb

        kb = reg_upgrade_categories_kb({"Окраска", "Прошивка"})
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("✅" in t and "Окраска" in t for t in texts)
        assert any("✅" in t and "Прошивка" in t for t in texts)
        assert any("Готово" in t for t in texts)

    def test_working_days_kb_empty(self):
        from partner_bot.ui.keyboards import reg_working_days_kb

        kb = reg_working_days_kb(set())
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Пн" in texts
        assert "Вс" in texts
        assert not any("Готово" in t for t in texts)

    def test_working_days_kb_selected(self):
        from partner_bot.ui.keyboards import reg_working_days_kb

        kb = reg_working_days_kb({"Пн", "Вт", "Ср"})
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("✅" in t and "Пн" in t for t in texts)
        assert any("Готово" in t for t in texts)

    def test_legal_form_kb(self):
        from partner_bot.ui.keyboards import reg_legal_form_kb

        kb = reg_legal_form_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "ИП" in texts
        assert "Юр. лицо" in texts

    def test_tax_system_kb(self):
        from partner_bot.ui.keyboards import reg_tax_system_kb

        kb = reg_tax_system_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "ОСНО" in texts
        assert "УСН" in texts
        assert "АУСН" in texts
        assert "Патентная система" in texts
        assert "НПД" in texts

    def test_draft_edit_kb_has_new_fields(self):
        from partner_bot.ui.keyboards import draft_edit_kb

        # upgrade type shows upgrade categories
        kb_upgrade = draft_edit_kb(service_type="upgrade")
        texts_upgrade = [btn.text for row in kb_upgrade.inline_keyboard for btn in row]
        assert "Категории апгрейда" in texts_upgrade
        assert "Категория ремонта" not in texts_upgrade

        # repair type shows repair category, not upgrade
        kb_repair = draft_edit_kb(service_type="repair")
        texts_repair = [btn.text for row in kb_repair.inline_keyboard for btn in row]
        assert "Категория ремонта" in texts_repair
        assert "Категории апгрейда" not in texts_repair

        # common fields always present
        for kb in (kb_upgrade, kb_repair):
            texts = [btn.text for row in kb.inline_keyboard for btn in row]
            assert "Рабочие дни" in texts
            assert "Орг.-правовая форма" in texts
            assert "Налогообложение" in texts
            assert "Банковские реквизиты" in texts

    def test_order_detail_awaiting(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "awaiting_payment", "client_user")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять" in texts
        assert "Отклонить" in texts
        assert any("Написать клиенту" in t for t in texts)

    def test_order_detail_accepted(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "accepted")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять в работу" in texts
        assert "Клиент отказался" in texts
        assert "Указать итоговую стоимость" in texts

    def test_order_detail_in_progress(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "in_progress")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Готов к выдаче" in texts
        assert "Указать итоговую стоимость" in texts

    def test_order_detail_completed(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "completed")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять" not in texts
        assert "К списку" in texts

    def test_order_detail_ready_for_pickup(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "ready_for_pickup")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "К списку" in texts
        assert "Принять в работу" not in texts

    def test_notif_settings_on(self):
        from partner_bot.ui.keyboards import notif_settings_kb

        kb = notif_settings_kb(True, True)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("[v]" in t for t in texts)

    def test_notif_settings_off(self):
        from partner_bot.ui.keyboards import notif_settings_kb

        kb = notif_settings_kb(False, False)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("[ ]" in t for t in texts)

    def test_quick_status_kb(self):
        from partner_bot.ui.keyboards import quick_status_kb

        kb = quick_status_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Закрыть на сегодня" in texts
        assert "Открыть сейчас" in texts

    def test_profile_edit_fields(self):
        from partner_bot.ui.keyboards import profile_edit_fields_kb

        kb = profile_edit_fields_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Название" in texts
        assert "Адрес" in texts
        assert "Телефон" in texts
        assert "Время работы" in texts

    def test_orders_list_kb(self):
        from partner_bot.ui.keyboards import partner_orders_list_kb

        class FakeOrder:
            def __init__(self, id, date, time):
                self.id = id
                self.scheduled_date = date
                self.scheduled_time = time

        orders = [
            FakeOrder(1, "10.04.2026", "14:00"),
            FakeOrder(2, "11.04.2026", "10:00"),
        ]
        kb = partner_orders_list_kb(orders, 0, 1)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("#1" in t for t in texts)
        assert any("#2" in t for t in texts)


# ── Admin partner keyboards ───────────────────────────────────


class TestAdminPartnerKeyboards:
    def test_admin_main_has_partners(self):
        from client_bot.ui.keyboards import admin_main_kb

        kb = admin_main_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Заявки партнёров" in texts

    def test_admin_partner_detail_pending(self):
        from client_bot.ui.keyboards import admin_partner_detail_kb

        kb = admin_partner_detail_kb(1, "pending")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "К списку" in texts
        assert "В главное меню" in texts

    def test_admin_partner_detail_active(self):
        from client_bot.ui.keyboards import admin_partner_detail_kb

        kb = admin_partner_detail_kb(1, "active")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "К списку" in texts

    def test_admin_partner_detail_suspended(self):
        from client_bot.ui.keyboards import admin_partner_detail_kb

        kb = admin_partner_detail_kb(1, "suspended")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "К списку" in texts


# ── Handler imports ────────────────────────────────────────────


class TestHandlerImports:
    def test_partner_common(self):
        from partner_bot.handlers.common import router

        assert router.name == "partner_common"

    def test_partner_registration(self):
        from partner_bot.handlers.registration import router

        assert router.name == "partner_registration"

    def test_partner_orders(self):
        from partner_bot.handlers.orders import router

        assert router.name == "partner_orders"

    def test_partner_profile(self):
        from partner_bot.handlers.profile import router

        assert router.name == "partner_profile"

    def test_partner_notifications(self):
        from partner_bot.handlers.notifications import router

        assert router.name == "partner_notifications"

    def test_sheets_writer(self):
        from client_bot.services.sheets_writer import (
            add_service_row,
            update_service_row,
            set_service_available,
        )

        assert callable(add_service_row)
        assert callable(update_service_row)
        assert callable(set_service_available)


# ── Config ─────────────────────────────────────────────────────


class TestPartnerConfig:
    def test_partner_bot_token_exists(self):
        from client_bot.core.config import PARTNER_BOT_TOKEN

        assert isinstance(PARTNER_BOT_TOKEN, str)

    def test_google_sa_path_exists(self):
        from client_bot.core.config import GOOGLE_SA_PATH

        assert isinstance(GOOGLE_SA_PATH, str)


# ── Hydro price & total cost ──────────────────────────────────


class TestHydroPriceField:
    def test_service_has_hydroisolation_price(self):
        from client_bot.domain.models import Service

        s = Service.__table__
        assert "hydroisolation_price" in s.columns.keys()

    def test_order_has_total_cost(self):
        from client_bot.domain.models import Order

        t = Order.__table__
        assert "total_cost" in t.columns.keys()

    def test_owner_has_draft_hydro_price(self):
        from client_bot.domain.models import Service

        t = Service.__table__
        assert "draft_hydro_price" in t.columns.keys()


# ── Config cleanup (PARTNER_BOT_NAME removed, SHEETS_COLUMNS hardcoded) ──


class TestConfigCleanup:
    def test_no_partner_bot_name_in_config(self):
        import client_bot.core.config as cfg

        assert not hasattr(
            cfg, "PARTNER_BOT_NAME"
        ), "PARTNER_BOT_NAME should be removed"

    def test_sheets_columns_is_hardcoded_list(self):
        from client_bot.core.config import SHEETS_COLUMNS

        assert isinstance(SHEETS_COLUMNS, list)
        assert len(SHEETS_COLUMNS) >= 15
        lower = [c.lower() for c in SHEETS_COLUMNS]
        assert "название" in lower
        assert "рейтинг я.карты" in lower
        assert "цена гидроизоляции" in lower

    def test_sheets_tab_constants(self):
        from client_bot.core.config import (
            SHEETS_TAB_SERVICES,
            SHEETS_TAB_ORDERS,
            SHEETS_TAB_CLIENTS,
        )

        assert SHEETS_TAB_SERVICES == "Сервисы"
        assert SHEETS_TAB_ORDERS == "Заявки"
        assert SHEETS_TAB_CLIENTS == "Клиенты"


# ── Partner common no PARTNER_BOT_NAME ─────────────────────────


class TestPartnerCommonNoBotName:
    def test_import_no_partner_bot_name(self):
        import partner_bot.handlers.common as pmod

        # Should not import PARTNER_BOT_NAME
        src = open(pmod.__file__, encoding="utf-8").read()
        assert "PARTNER_BOT_NAME" not in src


# ── Sheets writer new functions ────────────────────────────────


class TestSheetsWriterNewFunctions:
    def test_sync_orders_function_exists(self):
        from client_bot.services.sheets_writer import sync_all_orders_to_sheet

        assert callable(sync_all_orders_to_sheet)

    def test_sync_clients_function_exists(self):
        from client_bot.services.sheets_writer import sync_all_clients_to_sheet

        assert callable(sync_all_clients_to_sheet)

    def test_ensure_worksheet_function_exists(self):
        from client_bot.services.sheets_writer import _ensure_worksheet

        assert callable(_ensure_worksheet)

    def test_order_headers_defined(self):
        from client_bot.services.sheets_writer import _ORDER_HEADERS

        assert isinstance(_ORDER_HEADERS, list)
        assert "ID" in _ORDER_HEADERS

    def test_client_headers_defined(self):
        from client_bot.services.sheets_writer import _CLIENT_HEADERS

        assert isinstance(_CLIENT_HEADERS, list)
        assert "TG ID" in _CLIENT_HEADERS


# ── Database sync_engine ───────────────────────────────────────


class TestSyncEngine:
    def test_sync_engine_exists(self):
        from client_bot.core.database import sync_engine

        assert sync_engine is not None

    def test_sync_engine_url_no_aiosqlite(self):
        from client_bot.core.database import sync_engine

        assert "aiosqlite" not in str(sync_engine.url)


# ── Seed test data ─────────────────────────────────────────────


# ── Admin TelegramBadRequest fix ───────────────────────────────


class TestAdminBadRequestFix:
    def test_admin_imports_telegram_bad_request(self):
        import partner_bot.handlers.admin as amod

        src = open(amod.__file__, encoding="utf-8").read()
        assert "TelegramBadRequest" in src


# ── Video width/height ─────────────────────────────────────────


class TestVideoFix:
    def test_welcome_video_has_dimensions(self):
        import client_bot.handlers.common as cmod

        src = open(cmod.__file__, encoding="utf-8").read()
        assert "width=" in src
        assert "height=" in src


# ── Auto-payment ───────────────────────────────────────────────


class TestAutoPayment:
    def test_order_handler_has_asyncio(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "import asyncio" in src
        assert "asyncio.sleep(10)" in src

    def test_auto_pay_diagnostics_task(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "_auto_pay_diagnostics" in src

    def test_auto_pay_final_task(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "_auto_pay_final" in src

    def test_no_payment_stub_text(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "Система оплаты находится в разработке" not in src
        assert "Система предоплаты находится в разработке" not in src


# ── Diagnostics text ───────────────────────────────────────────


class TestDiagnosticsText:
    def test_diagnostics_included_text(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "Диагностика входит в стоимость ремонта" in src

    def test_diagnostics_not_included_text(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "диагностика не входит" in src

    def test_refund_policy_text(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "вернем ваши деньги" in src


# ── yandex_rating in confirm screen ───────────────────────────


class TestYandexRatingDisplay:
    def test_rating_shown_in_confirm(self):
        import client_bot.handlers.order as omod

        src = open(omod.__file__, encoding="utf-8").read()
        assert "svc_rating" in src
        assert "Рейтинг" in src


class TestPartnerOrderFSMStates:
    def test_has_set_total_cost(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "set_total_cost")

    def test_has_reject_reason(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "reject_reason")

    def test_not_duplicated(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert PartnerOrderFSM.set_total_cost is not PartnerOrderFSM.reject_reason


class TestRegistrationHydroPrice:
    def test_reg_hydro_price_state(self):
        from client_bot.domain.states import RegistrationFSM

        assert hasattr(RegistrationFSM, "reg_hydro_price")


class TestPartnerOrderDetailKbCost:
    def test_accepted_has_cost_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(1, "accepted")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Указать итоговую стоимость" in texts

    def test_in_progress_has_cost_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(1, "in_progress")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Указать итоговую стоимость" in texts

    def test_awaiting_no_cost_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(1, "awaiting_payment")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Указать итоговую стоимость" not in texts


class TestFSMReminderImport:
    def test_import(self):
        from client_bot.services.fsm_reminder import (
            FSMActivityMiddleware,
            fsm_reminder_loop,
        )

        assert callable(FSMActivityMiddleware)
        assert callable(fsm_reminder_loop)


class TestMetroTextUpdated:
    def test_new_metro_text_in_order(self):
        with open("bot/handlers/order.py", encoding="utf-8") as f:
            src = f.read()
        assert "Подберем самый ближайший сервис" in src
        assert "Как вы хотите указать ближайшую станцию метро?" not in src


class TestConfirmKbHasBack:
    def test_confirm_has_back(self):
        from client_bot.ui.keyboards import confirm_kb

        kb = confirm_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("Назад" in t or "◀" in t for t in texts)


class TestSupportKb:
    def test_has_tech_support_button(self):
        from client_bot.ui.keyboards import support_kb

        kb = support_kb("@testuser")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Техническая поддержка" in texts

    def test_has_cooperation_button(self):
        from client_bot.ui.keyboards import support_kb

        kb = support_kb("@testuser", "@coopuser")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Техническая поддержка" in texts
        assert "Вопросы по сотрудничеству" in texts

    def test_no_cooperation_without_param(self):
        from client_bot.ui.keyboards import support_kb

        kb = support_kb("@testuser")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Вопросы по сотрудничеству" not in texts

    def test_urls_correct(self):
        from client_bot.ui.keyboards import support_kb

        kb = support_kb("@sup", "@coop")
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        assert buttons[0].url == "https://t.me/sup"
        assert buttons[1].url == "https://t.me/coop"


class TestClientMainMenuKb:
    def test_has_support_button(self):
        from client_bot.ui.keyboards import main_menu_kb

        kb = main_menu_kb()
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert "Поддержка" in texts
        assert "Техподдержка" not in texts


class TestCooperationUserConfig:
    def test_exists(self):
        from client_bot.core.config import COOPERATION_USER

        assert isinstance(COOPERATION_USER, str)


class TestWelcomeText:
    def test_welcome_contains_service_map(self):
        from client_bot.handlers.common import _WELCOME_TEXT

        assert "Service Map" in _WELCOME_TEXT

    def test_welcome_video_path(self):
        from client_bot.handlers.common import _WELCOME_VIDEO

        assert _WELCOME_VIDEO.name == "client_start.mp4"


class TestPartnerStatusLabels:
    def test_no_abbreviated_statuses(self):
        from partner_bot.ui.keyboards import padm_partners_kb

        # Check the _STATUS_SHORT dict inside uses full labels
        import inspect

        src = inspect.getsource(padm_partners_kb)
        assert "ожид." not in src
        assert "актив." not in src
        assert "откл." not in src
        assert "приост." not in src


# ── New order lifecycle fields ─────────────────────────────────


class TestOrderNewFields:
    def test_estimate_fields(self):
        from client_bot.domain.models import Order

        t = Order.__table__
        assert "estimate_cost" in t.columns.keys()
        assert "estimate_items" in t.columns.keys()
        assert "estimate_deadline" in t.columns.keys()
        assert "estimate_description" in t.columns.keys()

    def test_client_feedback_fields(self):
        from client_bot.domain.models import Order

        t = Order.__table__
        assert "client_visited" in t.columns.keys()
        assert "client_confirmed_estimate" in t.columns.keys()
        assert "dispute_reason" in t.columns.keys()
        assert "refusal_reason" in t.columns.keys()


class TestNewFSMStates:
    def test_partner_estimate_states(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "estimate_cost")
        assert hasattr(PartnerOrderFSM, "estimate_items")
        assert hasattr(PartnerOrderFSM, "estimate_deadline")
        assert hasattr(PartnerOrderFSM, "estimate_description")
        assert hasattr(PartnerOrderFSM, "estimate_confirm")

    def test_partner_refused_state(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "client_refused_reason")

    def test_client_dispute_state(self):
        from client_bot.domain.states import ClientOrderFSM

        assert hasattr(ClientOrderFSM, "dispute_reason")


class TestClientNotificationKbs:
    def test_visited_kb(self):
        from client_bot.ui.keyboards import client_visited_kb

        kb = client_visited_kb(42)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Были ли вы в сервисе?" in texts

    def test_visited_confirm_kb(self):
        from client_bot.ui.keyboards import client_visited_confirm_kb

        kb = client_visited_confirm_kb(42)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Да" in texts
        assert "Нет" in texts
        assert "Назад" in texts

    def test_confirm_estimate_kb(self):
        from client_bot.ui.keyboards import client_confirm_estimate_kb

        kb = client_confirm_estimate_kb(42)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Подтвердить смету" in texts
        assert "Отклонить" in texts
        assert "Назад" in texts

    def test_ready_kb(self):
        from client_bot.ui.keyboards import client_ready_kb

        kb = client_ready_kb(42)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Оплатить и завершить" in texts
        assert "Оспорить" in texts

    def test_pay_confirm_kb(self):
        from client_bot.ui.keyboards import client_pay_confirm_kb

        kb = client_pay_confirm_kb(42)
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Да, оплатить" in texts
        assert "Нет" in texts
        assert "Назад" in texts


class TestStatusMaps:
    def test_partner_status_map(self):
        from partner_bot.handlers.orders import _STATUS_RU

        assert "ready_for_pickup" in _STATUS_RU
        assert "client_refused" in _STATUS_RU
        assert "disputed" in _STATUS_RU

    def test_admin_status_map(self):
        from client_bot.handlers.admin import _STATUS_RU

        assert "ready_for_pickup" in _STATUS_RU
        assert "client_refused" in _STATUS_RU
        assert "disputed" in _STATUS_RU
        assert "in_progress" in _STATUS_RU


class TestAdminFilterKb:
    def test_has_new_statuses(self):
        from client_bot.ui.keyboards import admin_filter_kb

        kb = admin_filter_kb()
        data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert any("in_progress" in d for d in data)
        assert any("ready_for_pickup" in d for d in data)
        assert any("disputed" in d for d in data)


class TestFormatDraftHidesEmpty:
    def test_no_hydro_price_when_no_hydro(self):
        from partner_bot.handlers.common import _format_draft
        from client_bot.domain.models import Service

        svc = Service(name="test", service_type="repair", telegram_id=123)
        svc.draft_hydroisolation = False
        svc.draft_hydro_price = None
        result = _format_draft(svc)
        assert "Цена гидроизоляции" not in result

    def test_shows_hydro_price_when_hydro(self):
        from partner_bot.handlers.common import _format_draft
        from client_bot.domain.models import Service

        svc = Service(name="test", service_type="repair", telegram_id=123)
        svc.draft_hydroisolation = True
        svc.draft_hydro_price = "1500"
        result = _format_draft(svc)
        assert "Цена гидроизоляции" in result
        assert "1500" in result


class TestPartnerFilterKb:
    def test_has_new_statuses_in_filter(self):
        from partner_bot.ui.keyboards import orders_filter_kb

        kb = orders_filter_kb()
        data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert any("in_progress" in d for d in data)
        assert any("ready_for_pickup" in d for d in data)


# ── Электрика + механика category ─────────────────────────────


class TestRepairCategoryElektrikaMekhanika:
    def test_repair_cats_has_three_options(self):
        from partner_bot.ui.keyboards import _REPAIR_CATS

        assert len(_REPAIR_CATS) == 3
        assert "Электрика + механика" in _REPAIR_CATS

    def test_reg_category_kb_has_combined(self):
        from partner_bot.ui.keyboards import reg_category_kb

        kb = reg_category_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Электрика + механика" in texts
        assert "Электрика" in texts
        assert "Механика" in texts

    def test_reg_category_kb_callback_data(self):
        from partner_bot.ui.keyboards import reg_category_kb

        kb = reg_category_kb()
        cbs = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert any("Электрика + механика" in cb for cb in cbs)


# ── Price change FSM states ─────────────────────────────────────


class TestPriceChangeFSMStates:
    def test_has_update_price_cost(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "update_price_cost")

    def test_has_update_price_reason(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert hasattr(PartnerOrderFSM, "update_price_reason")

    def test_states_are_distinct(self):
        from client_bot.domain.states import PartnerOrderFSM

        assert (
            PartnerOrderFSM.update_price_cost is not PartnerOrderFSM.update_price_reason
        )


# ── Price change model fields ───────────────────────────────────


class TestPriceChangeOrderFields:
    def test_order_has_price_change_reason(self):
        from client_bot.domain.models import Order

        t = Order.__table__
        assert "price_change_reason" in t.columns.keys()

    def test_order_has_price_updated_at(self):
        from client_bot.domain.models import Order

        t = Order.__table__
        assert "price_updated_at" in t.columns.keys()


# ── Price change keyboard button ─────────────────────────────────


class TestPriceChangeKeyboard:
    def test_in_progress_has_update_price_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(99, "in_progress")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Изменить цену" in texts

    def test_in_progress_update_price_callback(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(99, "in_progress")
        cbs = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert any("pord:update_price:99" in cb for cb in cbs)

    def test_accepted_has_no_update_price_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(99, "accepted")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Изменить цену" not in texts

    def test_awaiting_payment_has_no_update_price_button(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(99, "awaiting_payment", "client_user")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Изменить цену" not in texts


# ── Price change handler presence ────────────────────────────────


class TestPriceChangeHandlers:
    def test_update_price_handlers_exist(self):
        import partner_bot.handlers.orders as omod
        import inspect

        src = inspect.getsource(omod)
        assert "update_price_start" in src
        assert "update_price_cost_input" in src
        assert "update_price_reason_input" in src

    def test_handler_checks_in_progress_status(self):
        import partner_bot.handlers.orders as omod
        import inspect

        src = inspect.getsource(omod)
        # The handler should reject orders not in_progress
        assert 'order.status != "in_progress"' in src

    def test_handler_notifies_client(self):
        import partner_bot.handlers.orders as omod
        import inspect

        src = inspect.getsource(omod)
        assert "Failed to notify client about price update" in src
