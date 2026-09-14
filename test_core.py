"""Tests for core.py. No pydantic needed -- run these before anything else.

    python -m pytest tests/ -v
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (  # noqa: E402
    collapsed_search_requests,
    departure_window,
    last_possible_departure,
    open_jaw_routes,
    outbound_routes,
    return_offset,
    round_trip_routes,
    search_requests,
)

MAR3, MAR31 = date(2026, 3, 3), date(2026, 3, 31)
UAE = ["DXB", "AUH", "SHJ"]
ITA = ["FCO", "MXP", "VCE"]


class TestDurationMode:
    def test_nights_adds_full_duration(self):
        assert return_offset(7, "nights") == 7

    def test_days_is_inclusive_so_one_less(self):
        assert return_offset(7, "days") == 6

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError):
            return_offset(0, "nights")


class TestDepartureWindow:
    def test_last_departure_is_end_minus_duration(self):
        assert last_possible_departure(MAR31, 7) == date(2026, 3, 24)

    def test_worked_example_gives_22_pairs(self):
        assert len(departure_window(MAR3, MAR31, 7)) == 22

    def test_first_and_last_pair(self):
        pairs = departure_window(MAR3, MAR31, 7)
        assert pairs[0].depart == MAR3 and pairs[0].back == date(2026, 3, 10)
        assert pairs[-1].depart == date(2026, 3, 24) and pairs[-1].back == MAR31

    def test_no_return_ever_exceeds_holiday_end(self):
        assert all(p.back <= MAR31 for p in departure_window(MAR3, MAR31, 7))

    def test_no_departure_before_holiday_start(self):
        assert all(p.depart >= MAR3 for p in departure_window(MAR3, MAR31, 7))

    def test_duration_exactly_fills_window(self):
        pairs = departure_window(MAR3, date(2026, 3, 10), 7)
        assert len(pairs) == 1 and pairs[0].depart == MAR3

    def test_duration_longer_than_window_raises(self):
        with pytest.raises(ValueError, match="does not fit"):
            departure_window(MAR3, date(2026, 3, 8), 30)

    def test_reversed_window_raises(self):
        with pytest.raises(ValueError):
            departure_window(MAR31, MAR3, 7)

    def test_exclusions_remove_exactly_those_dates(self):
        pairs = departure_window(MAR3, MAR31, 7,
                                 excluded=[date(2026, 3, 5), date(2026, 3, 6)])
        assert len(pairs) == 20
        assert date(2026, 3, 5) not in {p.depart for p in pairs}

    def test_excluding_an_out_of_range_date_is_harmless(self):
        assert len(departure_window(MAR3, MAR31, 7,
                                    excluded=[date(2030, 1, 1)])) == 22

    def test_exclusions_are_idempotent(self):
        a = departure_window(MAR3, MAR31, 7, excluded=[date(2026, 3, 5)])
        b = departure_window(MAR3, MAR31, 7,
                             excluded=[date(2026, 3, 5), date(2026, 3, 5)])
        assert a == b


class TestRoutes:
    def test_cartesian_product_is_nine(self):
        assert len(outbound_routes(UAE, ITA)) == 9

    def test_order_is_stable(self):
        assert outbound_routes(UAE, ITA)[:3] == [
            ("DXB", "FCO"), ("DXB", "MXP"), ("DXB", "VCE")]

    def test_round_trip_returns_to_origin(self):
        for r in round_trip_routes(UAE, ITA):
            assert r["back_from"] == r["out_to"]
            assert r["back_to"] == r["out_from"]

    def test_open_jaw_multiplies_not_adds(self):
        assert len(open_jaw_routes(UAE, ITA)) == 81

    def test_removing_a_city_drops_three_routes(self):
        assert len(outbound_routes(UAE, ["FCO", "MXP"])) == 6

    def test_empty_side_yields_no_routes(self):
        assert outbound_routes([], ITA) == []


class TestFanOut:
    def test_expanded_is_pairs_times_routes(self):
        pairs = departure_window(MAR3, MAR31, 7)
        assert len(search_requests(pairs, round_trip_routes(UAE, ITA))) == 198

    def test_collapsed_is_a_single_request(self):
        pairs = departure_window(MAR3, MAR31, 7)
        assert len(collapsed_search_requests(pairs, UAE, ITA, 7)) == 1

    def test_collapsed_covers_the_whole_window(self):
        pairs = departure_window(MAR3, MAR31, 7)
        req = collapsed_search_requests(pairs, UAE, ITA, 7)[0]
        assert req["date_from"] == "03/03/2026"
        assert req["date_to"] == "24/03/2026"
        assert req["fly_from"] == "DXB,AUH,SHJ"
        assert req["nights_in_dst_from"] == req["nights_in_dst_to"] == "7"

    def test_dates_are_ddmmyyyy_not_iso(self):
        pairs = departure_window(MAR3, MAR31, 7)
        req = search_requests(pairs, round_trip_routes(UAE, ITA))[0]
        assert req["date_from"] == "03/03/2026"

    def test_no_pairs_means_no_requests(self):
        assert collapsed_search_requests([], UAE, ITA, 7) == []
