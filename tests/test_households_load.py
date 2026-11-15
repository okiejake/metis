"""Loading household files: catalog items by key, and checks that name the broken item."""

from datetime import date
from decimal import Decimal

import pytest

from households_support import MINIMAL, MOMENT, TRANSFER, household_from
from tools.households import HouseholdError
from tools.households.model import Fill, month_day


def test_minimal_household_loads(tmp_path):
    h = household_from(tmp_path, MINIMAL)
    assert h.end == date(2026, 2, 28)
    assert h.expected["rent"].budget == "housing" and h.expected["rent"].repeats == "FREQ=MONTHLY"
    assert h.expected["pay-semimonthly"].budget is None
    assert h.accounts["card"].cls == "liability" and h.accounts["card"].on_budget is True
    assert h.accounts["checking"].opening_date == date(2025, 12, 31)
    assert h.budgets["groceries"].fill == Fill("amount", Decimal("300.00"), "FREQ=MONTHLY")


def test_month_counts_across_a_year_boundary():                    # Review Focus 2
    assert month_day(date(2025, 11, 1), 3, 14) == date(2026, 1, 14)


def test_unknown_account_names_the_item(tmp_path):
    bad = MINIMAL.replace('to = "landlord"', 'to = "nobody"')
    with pytest.raises(HouseholdError, match=r"mini: expected rent: unknown account 'nobody'"):
        household_from(tmp_path, bad)


def test_line_with_two_targets_fails(tmp_path):
    with pytest.raises(HouseholdError, match="exactly one"):
        household_from(tmp_path, MINIMAL + MOMENT.format(
            lines='{ budget = "groceries", expected = "rent", amount = 50 }'))


def test_lines_must_add_up(tmp_path):
    with pytest.raises(HouseholdError, match="add up"):
        household_from(tmp_path, MINIMAL + MOMENT.format(lines='{ budget = "groceries", amount = 40 }'))


def test_card_payment_moment_with_lines_fails(tmp_path):
    with pytest.raises(HouseholdError, match="on-budget accounts"):
        household_from(tmp_path, MINIMAL + TRANSFER.format(
            to="card", lines='[{ budget = "groceries", amount = 50 }]'))


def test_loan_payment_moment_needs_its_line(tmp_path):
    h = household_from(tmp_path, MINIMAL + TRANSFER.format(to="loan", lines='[{ budget = "loans", amount = 50 }]'))
    assert h.moments[0].lines[0].budget == "loans"


def test_private_looking_text_fails(tmp_path):
    with pytest.raises(HouseholdError, match="privacy"):
        household_from(tmp_path, MINIMAL.replace('name = "Example Employer"', 'name = "Employer 10.0.0.7"'))
