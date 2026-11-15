"""Everyday spending, and assigning money the way Metis would propose."""

from datetime import date
from decimal import Decimal

import pytest

from households_support import (BIG_GROCERY, BIG_PURCHASE, DRAW, EXTRA_BILL, KIDS_FUND, LONG_MINIMAL, MINIMAL,
                                PAYMENTS, SPENDING, TAXES_25, TWO_FUNDS, fund_balances, fund_total,
                                household_from, key_id, month_spend, transactions_by_source)
from tools.households import HouseholdError
from tools.households.generate import generate


def test_purchases_track_the_budget(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + SPENDING))
    jan = month_spend(rows, "groceries", 2026, 1)
    assert Decimal("255.00") <= jan <= Decimal("315.00")


def test_opening_cash_fills_spending_budgets_on_day_one(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    first = [a for a in rows["assignments"] if a["assigned_on"] == date(2026, 1, 1) and a["note"] == "month start"]
    assert {a["amount"] for a in first} == {Decimal("300.00"), Decimal("500.00"), Decimal("100.00")}


def test_leftover_returns_on_the_first(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    assert any(a["note"] == "leftover" and a["assigned_on"] == date(2026, 2, 1) for a in rows["assignments"])


def test_shortfall_is_topped_up_from_to_assign(tmp_path):                     # Review Focus 3
    rows = generate(household_from(tmp_path, MINIMAL + BIG_GROCERY))
    assert any(a["note"] == "top up" and a["amount"] == Decimal("50.00") for a in rows["assignments"])


def test_overspending_past_to_assign_names_the_budget(tmp_path):              # Review Focus 3
    broke = MINIMAL.replace("opening_balance = 1000", "opening_balance = 0") + BIG_PURCHASE
    with pytest.raises(HouseholdError, match=r"mini: budget groceries: goes below zero on 2026-01-02"):
        generate(household_from(tmp_path, broke))


def test_percent_fill_takes_exactly_its_share(tmp_path):                       # Review Focus 4
    rows = generate(household_from(tmp_path, MINIMAL + TAXES_25 + PAYMENTS))
    taxes = key_id(rows, "budgets", "taxes")
    got = sorted(a["amount"] for a in rows["assignments"] if a["to_budget_id"] == taxes and a["note"] == "income")
    assert Decimal("83.33") in got and Decimal("250.00") in got


def test_fund_stops_at_its_target_and_refills(tmp_path):                        # Review Focus 5
    rows = generate(household_from(tmp_path, LONG_MINIMAL + KIDS_FUND + DRAW))
    balances = fund_balances(rows, "kids-fund")
    assert max(balances) == Decimal("500.00") and balances[-1] == Decimal("450.00")


def test_priority_one_fills_first(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + TWO_FUNDS))
    assert fund_total(rows, "first") > fund_total(rows, "second")


def test_adding_an_item_leaves_other_items_rows_alone(tmp_path):
    a = generate(household_from(tmp_path, MINIMAL + SPENDING))
    b = generate(household_from(tmp_path, MINIMAL + SPENDING + EXTRA_BILL))
    assert transactions_by_source(a) == {k: v for k, v in transactions_by_source(b).items() if k != "extra"}
