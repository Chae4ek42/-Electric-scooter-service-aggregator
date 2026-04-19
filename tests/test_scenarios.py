"""Comprehensive tests for all user scenarios.

Covers: texts, formatting, metro graph, metro search, ranking,
schemas, models, states, keyboards, sync_service.
"""

from __future__ import annotations

import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError


# ═══════════════════════════════════════════════════════════════
# 1. bot/texts.py — centralized message texts
# ═══════════════════════════════════════════════════════════════


class TestTextsTypeMappings:
    """TYPE_RU, ORDER_STATUS_RU, PARTNER_STATUS_RU cover all model values."""

    def test_type_ru_keys(self):
        from bot.texts import TYPE_RU

        assert set(TYPE_RU.keys()) == {"repair", "upgrade", "complex"}

    def test_type_ru_values_are_russian(self):
        from bot.texts import TYPE_RU

        for v in TYPE_RU.values():
            assert any("\u0400" <= c <= "\u04ff" for c in v), f"'{v}' not Cyrillic"

    def test_order_status_ru_covers_all(self):
        from bot.texts import ORDER_STATUS_RU

        expected = {
            "new",
            "awaiting_payment",
            "paid",
            "accepted",
            "in_progress",
            "ready_for_pickup",
            "completed",
            "cancelled",
            "client_refused",
            "disputed",
            "rejected_by_partner",
            "interrupted",
            "pending",
            "no_center",
        }
        assert expected.issubset(set(ORDER_STATUS_RU.keys()))

    def test_partner_status_ru_keys(self):
        from bot.texts import PARTNER_STATUS_RU

        assert set(PARTNER_STATUS_RU.keys()) == {
            "ожидает",
            "активный",
            "отклонён",
            "приостановлен",
        }


class TestBtnUniqueLabels:
    """Button labels are unique (no accidental duplicate)."""

    def test_no_duplicate_btn_labels(self):
        from bot.texts import Btn

        labels = [
            v
            for k, v in vars(Btn).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        assert len(labels) == len(set(labels)), f"Duplicates in Btn: {labels}"


class TestPartnerMenuTexts:
    """PARTNER_MENU_TEXTS contains all expected buttons."""

    def test_contains_key_buttons(self):
        from bot.texts import PARTNER_MENU_TEXTS, Btn

        assert Btn.INCOMING_ORDERS in PARTNER_MENU_TEXTS
        assert Btn.ORDER_HISTORY in PARTNER_MENU_TEXTS
        assert Btn.EDIT_PROFILE in PARTNER_MENU_TEXTS
        assert Btn.NOTIF_SETTINGS in PARTNER_MENU_TEXTS
        assert Btn.SERVICE_STATUS in PARTNER_MENU_TEXTS
        assert Btn.SUPPORT in PARTNER_MENU_TEXTS
        assert Btn.ADMIN_PANEL in PARTNER_MENU_TEXTS

    def test_is_tuple(self):
        from bot.texts import PARTNER_MENU_TEXTS

        assert isinstance(PARTNER_MENU_TEXTS, tuple)


class TestClientTextsFormatStrings:
    """Texts with {placeholders} format without errors."""

    def test_time_not_available_format(self):
        from bot.texts import Client

        text = Client.Order.TIME_NOT_AVAILABLE.format(time="14:00", suggested="15:00")
        assert "14:00" in text
        assert "15:00" in text

    def test_order_code_format(self):
        from bot.texts import Client

        text = Client.Order.ORDER_CODE_MSG.format(code="ABC123")
        assert "ABC123" in text

    def test_hydro_prepayment_format(self):
        from bot.texts import Client

        text = Client.Order.HYDRO_PREPAYMENT.format(price="1500 руб.")
        assert "1500 руб." in text

    def test_profile_remod_warning_format(self):
        from bot.texts import Partner

        text = Partner.Profile.REMOD_WARNING.format(label="Адрес")
        assert "Адрес" in text

    def test_profile_enter_value_format(self):
        from bot.texts import Partner

        text = Partner.Profile.ENTER_VALUE.format(label="Телефон")
        assert "Телефон" in text


class TestTextsNonEmpty:
    """All text fields are non-empty strings."""

    def test_client_texts_not_empty(self):
        from bot.texts import Client

        for cls in (Client.Common, Client.Order, Client.Admin):
            for k, v in vars(cls).items():
                if not k.startswith("_") and isinstance(v, str):
                    assert len(v) > 0, f"Client.{cls.__name__}.{k} is empty"

    def test_partner_texts_not_empty(self):
        from bot.texts import Partner

        for cls in (
            Partner.Common,
            Partner.Profile,
            Partner.Orders,
            Partner.Registration,
            Partner.Admin,
            Partner.Notifications,
        ):
            for k, v in vars(cls).items():
                if not k.startswith("_") and isinstance(v, str):
                    assert len(v) > 0, f"Partner.{cls.__name__}.{k} is empty"


# ═══════════════════════════════════════════════════════════════
# 2. bot/core/formatting.py — HTML escaping
# ═══════════════════════════════════════════════════════════════


class TestFormatting:
    def test_escape_angle_brackets(self):
        from bot.core.formatting import e

        assert e("<script>") == "&lt;script&gt;"

    def test_escape_ampersand(self):
        from bot.core.formatting import e

        assert e("A & B") == "A &amp; B"

    def test_empty_string(self):
        from bot.core.formatting import e

        assert e("") == ""

    def test_non_string_input(self):
        from bot.core.formatting import e

        assert e(42) == "42"
        assert e(None) == "None"

    def test_russian_text_unchanged(self):
        from bot.core.formatting import e

        text = "Ремонт самоката"
        assert e(text) == text

    def test_mixed_content(self):
        from bot.core.formatting import e

        assert e("a<b>c&d") == "a&lt;b&gt;c&amp;d"

    def test_already_escaped(self):
        from bot.core.formatting import e

        assert e("&amp;") == "&amp;amp;"


# ═══════════════════════════════════════════════════════════════
# 3. bot/services/metro_graph.py — BFS transfer distance
# ═══════════════════════════════════════════════════════════════


class TestMetroGraph:
    def test_same_station(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance("Арбатская", "Арбатская") == 0

    def test_case_insensitive(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance("АРБАТСКАЯ", "арбатская") == 0

    def test_direct_transfer(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance("Охотный Ряд", "Театральная") == 1

    def test_indirect_transfer(self):
        from bot.services.metro_graph import metro_transfer_distance

        # Охотный Ряд → Театральная → Площадь Революции
        assert metro_transfer_distance("Охотный Ряд", "Площадь Революции") == 2

    def test_library_cluster(self):
        from bot.services.metro_graph import metro_transfer_distance

        # All directly connected
        assert metro_transfer_distance("Библиотека имени Ленина", "Боровицкая") == 1
        assert metro_transfer_distance("Арбатская", "Александровский сад") == 1

    def test_unconnected_stations(self):
        from bot.services.metro_graph import metro_transfer_distance

        # Stations not in graph at all
        assert metro_transfer_distance("Митино", "Бутово") is None

    def test_empty_input(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance("", "Арбатская") is None
        assert metro_transfer_distance("Арбатская", "") is None
        assert metro_transfer_distance("", "") is None

    def test_whitespace_handling(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance("  Арбатская  ", "арбатская") == 0

    def test_graph_is_bidirectional(self):
        from bot.services.metro_graph import metro_transfer_distance

        assert metro_transfer_distance(
            "Охотный Ряд", "Театральная"
        ) == metro_transfer_distance("Театральная", "Охотный Ряд")

    def test_transfer_graph_populated(self):
        from bot.services.metro_graph import TRANSFER_GRAPH

        assert len(TRANSFER_GRAPH) >= 20


# ═══════════════════════════════════════════════════════════════
# 4. bot/services/metro_search.py — fuzzy matching
# ═══════════════════════════════════════════════════════════════


def _make_station(name, line="Тестовая"):
    from bot.domain.models import MetroStation

    s = MetroStation()
    s.id = hash(name) % 10000
    s.name = name
    s.line = line
    s.lat = None
    s.lon = None
    return s


class TestMetroSearch:
    @pytest.fixture
    def stations(self):
        return [
            _make_station("Арбатская"),
            _make_station("Арбатско-Покровская"),
            _make_station("Тверская"),
            _make_station("Сокольники"),
            _make_station("Комсомольская"),
            _make_station("Коммунарка"),
        ]

    def test_exact_match(self, stations):
        from bot.services.metro_search import best_metro_match

        m = best_metro_match("Арбатская", stations)
        assert m is not None
        assert m.name == "Арбатская"

    def test_partial_match(self, stations):
        from bot.services.metro_search import best_metro_match

        m = best_metro_match("арбат", stations)
        assert m is not None
        assert "Арбат" in m.name

    def test_case_insensitive(self, stations):
        from bot.services.metro_search import best_metro_match

        m = best_metro_match("ТВЕРСКАЯ", stations)
        assert m is not None
        assert m.name == "Тверская"

    def test_no_match_below_threshold(self, stations):
        from bot.services.metro_search import best_metro_match

        m = best_metro_match("xyz123", stations, threshold=0.9)
        assert m is None

    def test_empty_query(self, stations):
        from bot.services.metro_search import best_metro_match

        assert best_metro_match("", stations) is None

    def test_whitespace_query(self, stations):
        from bot.services.metro_search import best_metro_match

        assert best_metro_match("   ", stations) is None

    def test_top_matches_limit(self, stations):
        from bot.services.metro_search import top_metro_matches

        results = top_metro_matches("ком", stations, limit=2)
        assert len(results) <= 2

    def test_top_matches_sorted_by_score(self, stations):
        from bot.services.metro_search import top_metro_matches

        results = top_metro_matches("арбат", stations, limit=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_matches_empty_query(self, stations):
        from bot.services.metro_search import top_metro_matches

        assert top_metro_matches("", stations) == []

    def test_single_station_match(self):
        from bot.services.metro_search import best_metro_match

        stations = [_make_station("Тверская")]
        m = best_metro_match("тверская", stations)
        assert m is not None
        assert m.name == "Тверская"


# ═══════════════════════════════════════════════════════════════
# 5. bot/services/ranking.py — pure functions
# ═══════════════════════════════════════════════════════════════


class TestHaversine:
    def test_same_point(self):
        from bot.services.ranking import haversine_km

        assert haversine_km(55.75, 37.62, 55.75, 37.62) == 0.0

    def test_moscow_to_spb(self):
        from bot.services.ranking import haversine_km

        d = haversine_km(55.7558, 37.6173, 59.9343, 30.3351)
        assert 620 < d < 650  # ~634 km

    def test_short_distance(self):
        from bot.services.ranking import haversine_km

        # Two nearby points in Moscow (~1 km)
        d = haversine_km(55.7558, 37.6173, 55.7650, 37.6173)
        assert 0.5 < d < 2.0

    def test_symmetry(self):
        from bot.services.ranking import haversine_km

        d1 = haversine_km(55.75, 37.62, 59.93, 30.34)
        d2 = haversine_km(59.93, 30.34, 55.75, 37.62)
        assert abs(d1 - d2) < 0.001


class TestFindNearestMetro:
    def test_finds_closest(self):
        from bot.services.ranking import find_nearest_metro_by_coords

        s1 = _make_station("Тверская")
        s1.lat, s1.lon = 55.7650, 37.6050
        s2 = _make_station("Арбатская")
        s2.lat, s2.lon = 55.7520, 37.6010

        result = find_nearest_metro_by_coords(55.764, 37.605, [s1, s2])
        assert result is not None
        assert result.name == "Тверская"

    def test_no_stations_with_coords(self):
        from bot.services.ranking import find_nearest_metro_by_coords

        s1 = _make_station("Без координат")
        result = find_nearest_metro_by_coords(55.75, 37.62, [s1])
        assert result is None

    def test_empty_list(self):
        from bot.services.ranking import find_nearest_metro_by_coords

        assert find_nearest_metro_by_coords(55.75, 37.62, []) is None


class TestSvcCoversTime:
    def _make_service(self, open_t=None, close_t=None):
        from bot.domain.models import Service

        svc = Service()
        svc.open_time = open_t
        svc.close_time = close_t
        return svc

    def test_no_time_str(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, None) is True

    def test_no_open_close(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service()
        assert _svc_covers_time(svc, "14:00") is True

    def test_within_hours(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "14:00") is True

    def test_before_open(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "08:00") is False

    def test_at_open(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "09:00") is True

    def test_at_close(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "21:00") is False

    def test_after_close(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "22:00") is False

    def test_invalid_format(self):
        from bot.services.ranking import _svc_covers_time

        svc = self._make_service("09:00", "21:00")
        assert _svc_covers_time(svc, "invalid") is True


class TestFindNearestValidTime:
    def _make_service(self, open_t=None, close_t=None):
        from bot.domain.models import Service

        svc = Service()
        svc.open_time = open_t
        svc.close_time = close_t
        return svc

    def test_before_open(self):
        from bot.services.ranking import _find_nearest_valid_time

        svc = self._make_service("10:00", "20:00")
        assert _find_nearest_valid_time(svc, "08:00") == "10:00"

    def test_after_close(self):
        from bot.services.ranking import _find_nearest_valid_time

        svc = self._make_service("10:00", "20:00")
        assert _find_nearest_valid_time(svc, "22:00") == "19:00"

    def test_within_hours_returns_none(self):
        from bot.services.ranking import _find_nearest_valid_time

        svc = self._make_service("10:00", "20:00")
        assert _find_nearest_valid_time(svc, "14:00") is None

    def test_no_hours_returns_none(self):
        from bot.services.ranking import _find_nearest_valid_time

        svc = self._make_service()
        assert _find_nearest_valid_time(svc, "14:00") is None


class TestMetroProximityScore:
    def test_same_station(self):
        from bot.services.ranking import MetroProximityStrategy, RankingContext
        from bot.domain.models import Service

        strat = MetroProximityStrategy()
        svc = Service()
        svc.nearest_metro = "Тверская"
        ctx = RankingContext(service_type="repair", user_metro="Тверская")
        assert strat.score(svc, ctx) == 1.00

    def test_direct_transfer(self):
        from bot.services.ranking import MetroProximityStrategy, RankingContext
        from bot.domain.models import Service

        strat = MetroProximityStrategy()
        svc = Service()
        svc.nearest_metro = "Охотный Ряд"
        ctx = RankingContext(service_type="repair", user_metro="Театральная")
        assert strat.score(svc, ctx) == 0.85

    def test_two_transfers(self):
        from bot.services.ranking import MetroProximityStrategy, RankingContext
        from bot.domain.models import Service

        strat = MetroProximityStrategy()
        svc = Service()
        svc.nearest_metro = "Охотный Ряд"
        ctx = RankingContext(service_type="repair", user_metro="Площадь Революции")
        assert strat.score(svc, ctx) == 0.65

    def test_no_metro_data(self):
        from bot.services.ranking import (
            MetroProximityStrategy,
            RankingContext,
            SCORE_METRO_UNKNOWN,
        )
        from bot.domain.models import Service

        strat = MetroProximityStrategy()
        svc = Service()
        svc.nearest_metro = None
        ctx = RankingContext(service_type="repair", user_metro="Тверская")
        assert strat.score(svc, ctx) == SCORE_METRO_UNKNOWN

    def test_unconnected_stations(self):
        from bot.services.ranking import (
            MetroProximityStrategy,
            RankingContext,
            SCORE_METRO_FAR,
        )
        from bot.domain.models import Service

        strat = MetroProximityStrategy()
        svc = Service()
        svc.nearest_metro = "Митино"
        ctx = RankingContext(service_type="repair", user_metro="Бутово")
        assert strat.score(svc, ctx) == SCORE_METRO_FAR


# ═══════════════════════════════════════════════════════════════
# 6. bot/domain/schemas.py — Pydantic validators (parametrized)
# ═══════════════════════════════════════════════════════════════


class TestMetroTextInput:
    @pytest.mark.parametrize("val", ["Ар", "Арбатская", "А" * 100])
    def test_valid(self, val):
        from bot.domain.schemas import MetroTextInput

        assert MetroTextInput(text=val).text == val.strip()

    @pytest.mark.parametrize("val", ["", "А", "А" * 101])
    def test_invalid(self, val):
        from bot.domain.schemas import MetroTextInput

        with pytest.raises(ValidationError):
            MetroTextInput(text=val)


class TestProblemDescription:
    @pytest.mark.parametrize("val", ["Abc", "Не работает руль", "X" * 1000])
    def test_valid(self, val):
        from bot.domain.schemas import ProblemDescription

        assert ProblemDescription(text=val).text == val.strip()

    @pytest.mark.parametrize("val", ["ab", "", "X" * 1001])
    def test_invalid(self, val):
        from bot.domain.schemas import ProblemDescription

        with pytest.raises(ValidationError):
            ProblemDescription(text=val)


class TestModelNameInput:
    @pytest.mark.parametrize("val", ["Mi", "Xiaomi Mi 4 Pro", "A" * 150])
    def test_valid(self, val):
        from bot.domain.schemas import ModelNameInput

        assert ModelNameInput(text=val).text == val.strip()

    @pytest.mark.parametrize("val", ["A", "12345", "A" * 151])
    def test_invalid(self, val):
        from bot.domain.schemas import ModelNameInput

        with pytest.raises(ValidationError):
            ModelNameInput(text=val)


class TestBrandNameInput:
    @pytest.mark.parametrize("val", ["Mi", "Xiaomi", "A" * 100])
    def test_valid(self, val):
        from bot.domain.schemas import BrandNameInput

        assert BrandNameInput(text=val).text == val.strip()

    @pytest.mark.parametrize("val", ["A", "12345", "A" * 101])
    def test_invalid(self, val):
        from bot.domain.schemas import BrandNameInput

        with pytest.raises(ValidationError):
            BrandNameInput(text=val)


class TestServiceNameInput:
    @pytest.mark.parametrize("val", ["Abc", "Ремонт Pro", "A" * 200])
    def test_valid(self, val):
        from bot.domain.schemas import ServiceNameInput

        assert ServiceNameInput(text=val).text == val.strip()

    @pytest.mark.parametrize("val", ["РП", "12345", "A" * 201])
    def test_invalid(self, val):
        from bot.domain.schemas import ServiceNameInput

        with pytest.raises(ValidationError):
            ServiceNameInput(text=val)


class TestAddressInput:
    def test_valid(self):
        from bot.domain.schemas import AddressInput

        r = AddressInput(text="Москва, ул. Ленина, д. 10")
        assert "Ленина" in r.text

    @pytest.mark.parametrize("val", ["Москва", "12345678", "A" * 401])
    def test_invalid(self, val):
        from bot.domain.schemas import AddressInput

        with pytest.raises(ValidationError):
            AddressInput(text=val)


class TestPhoneInput:
    @pytest.mark.parametrize("val", ["+7 999 123-45-67", "89991234567", "+79991234567"])
    def test_valid(self, val):
        from bot.domain.schemas import PhoneInput

        assert PhoneInput(text=val).text

    @pytest.mark.parametrize("val", ["phone abc", "123", ""])
    def test_invalid(self, val):
        from bot.domain.schemas import PhoneInput

        with pytest.raises(ValidationError):
            PhoneInput(text=val)


class TestTelegramHandleInput:
    @pytest.mark.parametrize("val", ["@my_handle", "my_handle", "abcde"])
    def test_valid(self, val):
        from bot.domain.schemas import TelegramHandleInput

        r = TelegramHandleInput(text=val)
        assert not r.text.startswith("@")

    @pytest.mark.parametrize("val", ["ab", "my handle!", ""])
    def test_invalid(self, val):
        from bot.domain.schemas import TelegramHandleInput

        with pytest.raises(ValidationError):
            TelegramHandleInput(text=val)


class TestWorkHoursInput:
    @pytest.mark.parametrize("val", ["09:00-21:00", "09:00\u201321:00", "00:00-23:59"])
    def test_valid(self, val):
        from bot.domain.schemas import WorkHoursInput

        assert WorkHoursInput(text=val).text

    @pytest.mark.parametrize("val", ["9-21", "круглосуточно", ""])
    def test_invalid(self, val):
        from bot.domain.schemas import WorkHoursInput

        with pytest.raises(ValidationError):
            WorkHoursInput(text=val)


class TestDiagnosticsPriceInput:
    @pytest.mark.parametrize("val", ["0", "500", "10000"])
    def test_valid(self, val):
        from bot.domain.schemas import DiagnosticsPriceInput

        assert DiagnosticsPriceInput(text=val).text == val

    @pytest.mark.parametrize("val", ["-100", "бесплатно", ""])
    def test_invalid(self, val):
        from bot.domain.schemas import DiagnosticsPriceInput

        with pytest.raises(ValidationError):
            DiagnosticsPriceInput(text=val)


class TestRejectReasonInput:
    def test_valid(self):
        from bot.domain.schemas import RejectReasonInput

        r = RejectReasonInput(text="Нет запчастей на данную модель")
        assert "запчастей" in r.text

    @pytest.mark.parametrize("val", ["Не", "X" * 501])
    def test_invalid(self, val):
        from bot.domain.schemas import RejectReasonInput

        with pytest.raises(ValidationError):
            RejectReasonInput(text=val)


class TestBankAccountInput:
    def test_valid_20_digits(self):
        from bot.domain.schemas import BankAccountInput

        r = BankAccountInput(text="40702810938000012345")
        assert len(r.text) == 20

    @pytest.mark.parametrize("val", ["1234567890", "4070281093800001234a", ""])
    def test_invalid(self, val):
        from bot.domain.schemas import BankAccountInput

        with pytest.raises(ValidationError):
            BankAccountInput(text=val)


class TestBikInput:
    def test_valid(self):
        from bot.domain.schemas import BikInput

        assert BikInput(text="044525225").text == "044525225"

    @pytest.mark.parametrize("val", ["04452", "0445252251", "abcdefghi"])
    def test_invalid(self, val):
        from bot.domain.schemas import BikInput

        with pytest.raises(ValidationError):
            BikInput(text=val)


class TestCorrAccountInput:
    def test_valid(self):
        from bot.domain.schemas import CorrAccountInput

        assert len(CorrAccountInput(text="30101810400000000225").text) == 20

    def test_invalid(self):
        from bot.domain.schemas import CorrAccountInput

        with pytest.raises(ValidationError):
            CorrAccountInput(text="301018104")


class TestOrgNameInput:
    def test_valid(self):
        from bot.domain.schemas import OrgNameInput

        assert "Звездилин" in OrgNameInput(text="ИП Звездилин Сергей").text

    def test_too_short(self):
        from bot.domain.schemas import OrgNameInput

        with pytest.raises(ValidationError):
            OrgNameInput(text="ИП")


class TestInnInput:
    @pytest.mark.parametrize("val", ["7707083893", "770708389312"])
    def test_valid(self, val):
        from bot.domain.schemas import InnInput

        assert InnInput(text=val).text == val

    @pytest.mark.parametrize("val", ["77070838931", "770708389a", "123"])
    def test_invalid(self, val):
        from bot.domain.schemas import InnInput

        with pytest.raises(ValidationError):
            InnInput(text=val)


# ═══════════════════════════════════════════════════════════════
# 7. bot/domain/models.py — structural checks
# ═══════════════════════════════════════════════════════════════


class TestServiceModel:
    def test_has_pause_until(self):
        from bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        assert "pause_until" in cols

    def test_has_all_required_columns(self):
        from bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        expected = {
            "id",
            "name",
            "service_type",
            "is_available",
            "address",
            "yandex_rating",
            "nearest_metro",
            "phone",
            "telegram_handle",
            "open_time",
            "close_time",
            "has_hydroisolation",
            "hydroisolation_price",
            "diagnostics_price",
            "diagnostics_included",
            "upgrade_categories",
            "working_days",
            "pause_until",
        }
        assert expected.issubset(cols), f"Missing: {expected - cols}"


class TestOrderModel:
    def test_all_lifecycle_fields(self):
        from bot.domain.models import Order

        cols = {c.name for c in Order.__table__.columns}
        expected = {
            "id",
            "user_id",
            "service_id",
            "model_id",
            "status",
            "metro_station",
            "scheduled_date",
            "scheduled_time",
            "order_code",
            "total_cost",
            "estimate_cost",
            "estimate_items",
            "estimate_deadline",
            "estimate_description",
            "client_visited",
            "client_confirmed_estimate",
            "dispute_reason",
            "refusal_reason",
            "partner_comment",
            "reject_reason",
            "accepted_at",
            "completed_at",
        }
        assert expected.issubset(cols), f"Missing: {expected - cols}"


class TestServiceOwnerModel:
    def test_service_has_status_column(self):
        from bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        assert "status" in cols

    def test_draft_fields_complete(self):
        from bot.domain.models import Service

        cols = {c.name for c in Service.__table__.columns}
        draft_fields = {
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
        assert draft_fields.issubset(cols), f"Missing: {draft_fields - cols}"


class TestUserModel:
    def test_user_columns(self):
        from bot.domain.models import User

        cols = {c.name for c in User.__table__.columns}
        assert {"id", "username", "full_name", "created_at"}.issubset(cols)


class TestSheetsRetryQueueModel:
    def test_columns(self):
        from bot.domain.models import SheetsRetryQueue

        cols = {c.name for c in SheetsRetryQueue.__table__.columns}
        assert {"id", "operation", "payload_json", "attempts"}.issubset(cols)


# ═══════════════════════════════════════════════════════════════
# 8. bot/domain/states.py — FSM states
# ═══════════════════════════════════════════════════════════════


class TestOrderFSM:
    def test_all_states(self):
        from bot.domain.states import OrderFSM

        expected = [
            "service_type",
            "brand",
            "brand_custom",
            "model",
            "model_custom",
            "malfunction_type",
            "upgrade_category",
            "problem_description",
            "location_method",
            "metro_search",
            "metro_confirm",
            "calendar_date",
            "calendar_time",
            "confirm",
        ]
        for s in expected:
            assert hasattr(OrderFSM, s), f"OrderFSM missing state: {s}"

    def test_state_strings_not_none(self):
        from bot.domain.states import OrderFSM

        assert OrderFSM.service_type.state is not None
        assert OrderFSM.confirm.state is not None


class TestRegistrationFSM:
    def test_all_states(self):
        from bot.domain.states import RegistrationFSM

        expected = [
            "reg_name",
            "reg_service_type",
            "reg_upgrade_categories",
            "reg_hydroisolation",
            "reg_hydro_price",
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


class TestPartnerOrderFSM:
    def test_all_states(self):
        from bot.domain.states import PartnerOrderFSM

        expected = [
            "set_total_cost",
            "reject_reason",
            "client_refused_reason",
            "estimate_cost",
            "estimate_items",
            "estimate_deadline",
            "estimate_description",
            "estimate_confirm",
        ]
        for s in expected:
            assert hasattr(PartnerOrderFSM, s), f"Missing state: {s}"


class TestClientOrderFSM:
    def test_dispute_reason(self):
        from bot.domain.states import ClientOrderFSM

        assert hasattr(ClientOrderFSM, "dispute_reason")


class TestPartnerProfileFSM:
    def test_states(self):
        from bot.domain.states import PartnerProfileFSM

        assert hasattr(PartnerProfileFSM, "edit_field_select")
        assert hasattr(PartnerProfileFSM, "edit_field_value")


# ═══════════════════════════════════════════════════════════════
# 9. Keyboards — buttons match Btn constants
# ═══════════════════════════════════════════════════════════════


class TestClientKeyboards:
    def test_main_menu_uses_btn(self):
        from bot.ui.keyboards import main_menu_kb
        from bot.texts import Btn

        kb = main_menu_kb()
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert Btn.SUBMIT_ORDER in texts
        assert Btn.MY_ORDERS in texts
        assert Btn.SUPPORT in texts

    def test_service_type_kb(self):
        from bot.ui.keyboards import service_type_kb

        kb = service_type_kb()
        assert kb is not None
        assert len(kb.inline_keyboard) >= 1

    def test_confirm_kb_has_back(self):
        from bot.ui.keyboards import confirm_kb

        kb = confirm_kb()
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("Назад" in t or "◀" in t for t in texts)

    def test_calendar_kb_buttons(self):
        from bot.ui.keyboards import calendar_kb
        from bot.core.config import CALENDAR_DAYS

        kb = calendar_kb()
        total = sum(len(row) for row in kb.inline_keyboard)
        assert CALENDAR_DAYS + 1 <= total <= CALENDAR_DAYS + 2

    def test_time_slots_kb(self):
        from bot.ui.keyboards import time_slots_kb

        kb = time_slots_kb("01.01.2026")
        total = sum(len(row) for row in kb.inline_keyboard)
        assert total >= 5

    def test_support_kb(self):
        from bot.ui.keyboards import support_kb

        kb = support_kb("@testuser")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Техническая поддержка" in texts


class TestPartnerKeyboards:
    def test_main_menu(self):
        from partner_bot.ui.keyboards import partner_main_menu_kb
        from bot.texts import Btn

        kb = partner_main_menu_kb()
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert Btn.INCOMING_ORDERS in texts
        assert Btn.ORDER_HISTORY in texts
        assert Btn.EDIT_PROFILE in texts

    def test_pending_menu(self):
        from partner_bot.ui.keyboards import partner_pending_menu_kb
        from bot.texts import Btn

        kb = partner_pending_menu_kb(has_draft=False)
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert Btn.MY_DRAFT in texts
        assert Btn.SUPPORT in texts

    def test_pending_menu_with_draft(self):
        from partner_bot.ui.keyboards import partner_pending_menu_kb
        from bot.texts import Btn

        kb = partner_pending_menu_kb(has_draft=True)
        texts = [btn.text for row in kb.keyboard for btn in row]
        assert Btn.CONTINUE_DRAFT in texts

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

    def test_order_detail_awaiting(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "awaiting_payment", "client_user")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять" in texts
        assert "Отклонить" in texts

    def test_order_detail_accepted(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "accepted")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять в работу" in texts
        assert "Указать итоговую стоимость" in texts

    def test_order_detail_in_progress(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "in_progress")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Готов к выдаче" in texts

    def test_order_detail_completed(self):
        from partner_bot.ui.keyboards import partner_order_detail_kb

        kb = partner_order_detail_kb(42, "completed")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Принять" not in texts
        assert "К списку" in texts

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

    def test_draft_edit_kb_fields(self):
        from partner_bot.ui.keyboards import draft_edit_kb

        kb_upgrade = draft_edit_kb(service_type="upgrade")
        texts_upgrade = [btn.text for row in kb_upgrade.inline_keyboard for btn in row]
        assert "Категории апгрейда" in texts_upgrade

        kb_repair = draft_edit_kb(service_type="repair")
        texts_repair = [btn.text for row in kb_repair.inline_keyboard for btn in row]
        assert "Категория ремонта" in texts_repair

        for kb in (kb_upgrade, kb_repair):
            texts = [btn.text for row in kb.inline_keyboard for btn in row]
            assert "Рабочие дни" in texts
            assert "Банковские реквизиты" in texts


# ═══════════════════════════════════════════════════════════════
# 10. Handler imports — verify all routers importable
# ═══════════════════════════════════════════════════════════════


class TestAllHandlerImports:
    def test_client_common(self):
        from bot.handlers.common import router

        assert router is not None

    def test_client_order(self):
        from bot.handlers.order import router

        assert router is not None

    def test_client_admin(self):
        from bot.handlers.admin import router

        assert router is not None

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

    def test_partner_admin(self):
        from partner_bot.handlers.admin import router

        assert router is not None


# ═══════════════════════════════════════════════════════════════
# 11. FSM Reminder — _is_form_state logic
# ═══════════════════════════════════════════════════════════════


class TestFSMReminderPrefixes:
    def test_is_form_state_with_prefix(self):
        from bot.services.fsm_reminder import FSMActivityMiddleware

        mw = FSMActivityMiddleware("test", form_state_prefixes=["OrderFSM:"])
        assert mw._is_form_state("OrderFSM:brand") is True
        assert mw._is_form_state("RegistrationFSM:reg_name") is False

    def test_is_form_state_no_prefix(self):
        from bot.services.fsm_reminder import FSMActivityMiddleware

        mw = FSMActivityMiddleware("test", form_state_prefixes=[])
        assert mw._is_form_state("anything") is True

    def test_is_form_state_none_state(self):
        from bot.services.fsm_reminder import FSMActivityMiddleware

        mw = FSMActivityMiddleware("test", form_state_prefixes=["OrderFSM:"])
        # None state raises AttributeError — middleware guards against this
        with pytest.raises(AttributeError):
            mw._is_form_state(None)


# ═══════════════════════════════════════════════════════════════
# 12. sync_service — importable and structured
# ═══════════════════════════════════════════════════════════════


class TestSyncService:
    def test_import(self):
        from sync_service.__main__ import main

        assert callable(main)

    def test_sync_loop_import(self):
        from sync_service.__main__ import _sync_loop

        assert callable(_sync_loop)


# ═══════════════════════════════════════════════════════════════
# 13. Config validation
# ═══════════════════════════════════════════════════════════════


class TestConfigValidation:
    def test_sheets_columns_defined(self):
        from bot.core.config import SHEETS_COLUMNS

        assert isinstance(SHEETS_COLUMNS, list)
        assert len(SHEETS_COLUMNS) >= 10
        lower = [c.lower() for c in SHEETS_COLUMNS]
        assert "название" in lower
        assert "доступен" in lower

    def test_sheets_tab_constants(self):
        from bot.core.config import (
            SHEETS_TAB_SERVICES,
            SHEETS_TAB_ORDERS,
            SHEETS_TAB_CLIENTS,
        )

        assert SHEETS_TAB_SERVICES == "Сервисы"
        assert SHEETS_TAB_ORDERS == "Заявки"
        assert SHEETS_TAB_CLIENTS == "Клиенты"

    def test_admin_usernames_is_set(self):
        from bot.core.config import ADMIN_USERNAMES

        assert isinstance(ADMIN_USERNAMES, set)

    def test_redis_url(self):
        from bot.core.config import REDIS_URL

        assert REDIS_URL.startswith("redis://")


# ═══════════════════════════════════════════════════════════════
# 14. Sheets writer — structure checks
# ═══════════════════════════════════════════════════════════════


class TestSheetsWriter:
    def test_all_columns_have_getters(self):
        from bot.core.config import SHEETS_COLUMNS
        from bot.services.sheets_writer import _FIELD_GETTERS

        for col in SHEETS_COLUMNS:
            assert (
                col.strip().lower() in _FIELD_GETTERS
            ), f"No getter for column '{col}'"

    def test_type_map_rev(self):
        from bot.services.sheets_writer import _TYPE_MAP_REV

        assert _TYPE_MAP_REV == {
            "repair": "Ремонт",
            "upgrade": "Апгрейд",
            "complex": "Комплекс",
        }

    def test_functions_callable(self):
        from bot.services.sheets_writer import (
            add_service_row,
            update_service_row,
            set_service_available,
            sync_all_orders_to_sheet,
            sync_all_clients_to_sheet,
        )

        assert all(
            callable(f)
            for f in [
                add_service_row,
                update_service_row,
                set_service_available,
                sync_all_orders_to_sheet,
                sync_all_clients_to_sheet,
            ]
        )


# ═══════════════════════════════════════════════════════════════
# 15. DB integration — async CRUD
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestDBIntegration:
    async def test_init_db(self):
        from bot.services.seed import init_db

        await init_db()

    async def test_brands_seeded(self):
        from sqlalchemy import select
        from bot.core.database import async_session
        from bot.domain.models import Brand

        await __import__("bot.services.seed", fromlist=["init_db"]).init_db()
        async with async_session() as session:
            brands = (await session.execute(select(Brand))).scalars().all()
            assert len(brands) >= 5

    async def test_metro_stations_seeded(self):
        from sqlalchemy import select
        from bot.core.database import async_session
        from bot.domain.models import MetroStation

        async with async_session() as session:
            stations = (await session.execute(select(MetroStation))).scalars().all()
            assert len(stations) >= 200

    async def test_user_create_and_read(self):
        from sqlalchemy import select
        from bot.core.database import async_session
        from bot.domain.models import User

        async with async_session() as session:
            u = User(id=888888888, username="testuser", full_name="Тест")
            session.add(u)
            await session.flush()
            loaded = (
                await session.execute(select(User).where(User.id == 888888888))
            ).scalar_one()
            assert loaded.full_name == "Тест"
            await session.delete(u)
            await session.commit()

    async def test_order_lifecycle(self):
        from sqlalchemy import select
        from bot.core.database import async_session
        from bot.domain.models import Model, Order, Service, ServiceCategory, User

        async with async_session() as session:
            user = User(id=777777777, username="lifecycle", full_name="Life")
            session.add(user)

            svc = (await session.execute(select(Service).limit(1))).scalar_one_or_none()
            created_svc = False
            if svc is None:
                cat = (
                    await session.execute(select(ServiceCategory).limit(1))
                ).scalar_one()
                svc = Service(
                    name="Test LC Svc",
                    service_type="repair",
                    category_id=cat.id,
                    is_available=True,
                )
                session.add(svc)
                await session.flush()
                created_svc = True

            mdl = (await session.execute(select(Model).limit(1))).scalar_one()
            order = Order(
                user_id=user.id,
                service_id=svc.id,
                model_id=mdl.id,
                metro_station="Тверская",
                scheduled_date="10.04.2026",
                scheduled_time="14:00",
                status="awaiting_payment",
            )
            session.add(order)
            await session.flush()
            assert order.id is not None

            # Transition: awaiting_payment → accepted
            order.status = "accepted"
            await session.flush()
            loaded = (
                await session.execute(select(Order).where(Order.id == order.id))
            ).scalar_one()
            assert loaded.status == "accepted"

            # Transition: accepted → in_progress
            order.status = "in_progress"
            await session.flush()

            # Transition: in_progress → ready_for_pickup
            order.status = "ready_for_pickup"
            await session.flush()

            # Transition: ready_for_pickup → completed
            order.status = "completed"
            await session.flush()

            loaded = (
                await session.execute(select(Order).where(Order.id == order.id))
            ).scalar_one()
            assert loaded.status == "completed"

            # Cleanup
            await session.delete(order)
            if created_svc:
                await session.delete(svc)
            await session.delete(user)
            await session.commit()

    async def test_service_pause_until(self):
        import datetime
        from sqlalchemy import select
        from bot.core.database import async_session
        from bot.domain.models import Service, ServiceCategory

        async with async_session() as session:
            cat = (await session.execute(select(ServiceCategory).limit(1))).scalar_one()
            svc = Service(
                name="Pause Test Svc",
                service_type="repair",
                category_id=cat.id,
                is_available=False,
                pause_until=datetime.datetime.now(tz=datetime.timezone.utc)
                + datetime.timedelta(hours=1),
            )
            session.add(svc)
            await session.flush()
            assert svc.pause_until is not None
            assert svc.is_available is False

            # Simulate unpause
            svc.is_available = True
            svc.pause_until = None
            await session.flush()
            assert svc.is_available is True

            await session.delete(svc)
            await session.commit()


# ═══════════════════════════════════════════════════════════════
# 16. Ranking — async integration test
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestRankingIntegration:
    async def test_rank_services_empty_result(self):
        from bot.core.database import async_session
        from bot.services.ranking import RankingContext, rank_services
        from bot.services.seed import init_db

        await init_db()
        ctx = RankingContext(
            service_type="repair",
            user_metro="Тверская",
            scheduled_time="14:00",
        )
        async with async_session() as session:
            result = await rank_services(ctx, session)
            # May be empty or may have services from sheet sync
            assert result is not None
            assert isinstance(result.matches, list)
