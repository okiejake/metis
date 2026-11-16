"""The households catalog: account types, budgets, bills and income, with no amounts."""

import subprocess
import sys

import pytest

from tools.households import HouseholdError, load_catalog


def test_catalog_loads_every_section():
    c = load_catalog()
    assert c.account_types["credit-card"] == {
        "key": "credit-card", "name": "credit card", "class": "liability", "on_budget": True}
    assert c.budgets["taxes"]["fill"] == {"basis": "income_percent"}
    assert c.bills["car-insurance"]["repeats"] == "FREQ=MONTHLY;INTERVAL=6"
    assert c.incomes["pay-semimonthly"]["type"] == "pay"


def test_catalog_names_are_lower_case_and_bills_use_known_budgets():
    c = load_catalog()
    for section in (c.account_types, c.budgets, c.bills, c.incomes):
        for item in section.values():
            assert item["name"] == item["name"].lower()
    assert {b["budget"] for b in c.bills.values()} <= set(c.budgets)


def test_catalog_holds_no_amounts():
    c = load_catalog()
    for section in (c.account_types, c.budgets, c.bills, c.incomes):
        for item in section.values():
            assert "amount" not in item and "value" not in item.get("fill", {})


def test_duplicate_key_names_the_item(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[budget]]\nkey = "gas"\nname = "gas"\n[[budget]]\nkey = "gas"\nname = "gas"\n')
    with pytest.raises(HouseholdError, match="catalog budget gas: appears twice"):
        load_catalog(p)


def test_package_stands_alone():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, tools.households; "
         "print(any(m in sys.modules for m in ('services', 'app', 'routes', 'web')))"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
