"""Repeat rules expand into dates, and a household's dates can move to end today."""

from datetime import date
from decimal import Decimal

from tools.households.schedules import occurrences, shift


def test_every_other_friday():
    days = occurrences("FREQ=WEEKLY;INTERVAL=2", date(2025, 11, 7), date(2026, 10, 31))
    assert len(days) == 26 and all(d.weekday() == 4 for d in days)


def test_first_and_fifteenth():
    days = occurrences("FREQ=MONTHLY;BYMONTHDAY=1,15", date(2026, 5, 1), date(2026, 10, 31))
    assert len(days) == 12 and days[:2] == [date(2026, 5, 1), date(2026, 5, 15)]


def test_the_31st_lands_on_each_months_last_day():                    # focus 1 (see the plan)
    days = occurrences("FREQ=MONTHLY", date(2026, 1, 31), date(2026, 12, 31))
    assert len(days) == 12 and date(2026, 2, 28) in days and date(2026, 4, 30) in days


def test_end_date_is_respected():
    assert occurrences("FREQ=MONTHLY", date(2026, 1, 5), date(2026, 12, 31), ends=date(2026, 3, 5))[-1] == date(2026, 3, 5)


def test_shift_moves_every_date_and_nothing_else():
    rows = {"transactions": [{"id": 1, "posted_on": date(2026, 2, 28), "amount": Decimal("-5.00")}],
            "assignments": [{"id": 1, "assigned_on": date(2026, 2, 1), "amount": Decimal("5.00")}],
            "budgets": [{"id": 1, "target_date": None}]}
    out = shift(rows, end=date(2026, 2, 28), today=date(2026, 11, 14))
    assert out["transactions"][0]["posted_on"] == date(2026, 11, 14)
    assert out["assignments"][0]["assigned_on"] == date(2026, 10, 18)
    assert out["transactions"][0]["amount"] == Decimal("-5.00") and out["budgets"][0]["target_date"] is None
    assert rows["transactions"][0]["posted_on"] == date(2026, 2, 28)
