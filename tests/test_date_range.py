"""The saved date range is a rule (grain, mode, start, end) that resolves to dates
for a given today. Relative rules slide forward with the calendar.

All data here is synthetic.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from date_range import RangeRule, resolve_rule, rule_from_form, rule_from_saved, rule_to_saved

CASES = json.loads((Path(__file__).parent / "fixtures" / "date_range_cases.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_resolve_rule_matches_the_shared_cases(case):
    rule = rule_from_saved(case["rule"])
    assert resolve_rule(rule, date.fromisoformat(case["today"])) == (
        date.fromisoformat(case["start"]),
        date.fromisoformat(case["end"]),
    )


def test_a_relative_rule_slides_forward_a_month_later():
    rule = RangeRule("month", "relative", -3, 6)
    assert resolve_rule(rule, date(2026, 11, 13))[0] == date(2026, 8, 1)
    assert resolve_rule(rule, date(2026, 12, 2))[0] == date(2026, 9, 1)


def test_legacy_saved_dates_read_as_day_absolute():
    assert rule_from_saved({"start": "2026-02-01", "end": "2026-03-31"}) == RangeRule(
        "day", "absolute", "2026-02-01", "2026-03-31"
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "x",
        [],
        {"grain": "week", "mode": "relative", "start": 0, "end": 0},
        {"grain": "month", "mode": "relative", "start": "a", "end": 0},
        {"grain": "month", "mode": "absolute", "start": "2026-13", "end": "2026-12"},
        {"start": "nope"},
    ],
)
def test_malformed_saved_values_read_as_none(value):
    assert rule_from_saved(value) is None


def test_saved_round_trip():
    rule = RangeRule("year", "absolute", "2024", "2026")
    assert rule_from_saved(rule_to_saved(rule)) == rule


@pytest.mark.parametrize(
    "args, message",
    [
        (("week", "relative", "0", "0"), "grain"),
        (("month", "sideways", "0", "0"), "mode"),
        (("month", "relative", "x", "0"), "number"),
        (("month", "relative", "-241", "0"), "too far"),
        (("month", "absolute", "2026-13", "2026-12"), "month"),
        (("decade", "absolute", "2025", "2030"), "decade"),
        (("month", "relative", "2", "1"), "after"),
        (("day", "absolute", "2027-01-20", "2026-11-10"), "after"),
    ],
)
def test_rule_from_form_rejects(args, message):
    with pytest.raises(ValueError, match=message):
        rule_from_form(*args)


def test_rule_from_form_accepts_and_types_values():
    assert rule_from_form("month", "relative", "-3", "6") == RangeRule("month", "relative", -3, 6)
    assert rule_from_form("decade", "absolute", "2020", "2030") == RangeRule("decade", "absolute", "2020", "2030")
