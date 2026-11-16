"""The three households load, generate, and show what each is there to show."""

from datetime import date
from decimal import Decimal

import pytest

from households_support import fund_balances, key_id, lines_of, lines_to, monthly_income
from tools.households.generate import generate
from tools.households.load import load_all


@pytest.fixture(scope="module")
def rows():
    return {h.key: (h, generate(h)) for h in load_all()}


def test_all_three_load_and_generate(rows):
    assert sorted(rows) == ["dana-demo", "example-family", "sam-sample"]


def test_family_split_refund_and_covered_repair(rows):
    h, r = rows["example-family"]
    assert any(len(lines_of(r, t)) == 2 for t in r["transactions"])
    assert any(t["amount"] == Decimal("45.00") for t in r["transactions"])
    assert any(a["note"] == "story" and a["amount"] == Decimal("900.00") for a in r["assignments"])
    assert max(fund_balances(r, "kids-fund")) == Decimal("500.00")


def test_sam_starts_out(rows):
    h, r = rows["sam-sample"]
    assert h.end == date(2026, 10, 31)
    assert lines_to(r, "emergency-fund", negative=True) == [Decimal("400.00")]
    assert lines_to(r, "to-assign") == [Decimal("1000.00")]


def test_dana_income_is_uneven_and_taxes_take_a_quarter(rows):
    h, r = rows["dana-demo"]
    income = monthly_income(r)
    assert income.get("2026-05", Decimal("0")) == 0 and income["2026-06"] == Decimal("8000.00")
    taxes = key_id(r, "budgets", "taxes")
    assert sum(a["amount"] for a in r["assignments"] if a["to_budget_id"] == taxes) == Decimal("16375.00")


def test_households_are_repeatable(rows):
    assert all(generate(h) == r for h, r in rows.values())
