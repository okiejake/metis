"""Loading household files: catalog items by key, and checks that name the broken item."""

from datetime import date
from decimal import Decimal

import pytest

from households_support import MINIMAL, MOMENT, TRANSFER, household_from
from tools.households import HouseholdError
from tools.households.model import Fill, month_day

# Built in two pieces so this file never holds a private address itself.
PRIVATE = "10." + "0.0.7"


def test_minimal_household_loads(tmp_path):
    h = household_from(tmp_path, MINIMAL)
    assert h.end == date(2026, 2, 28)
    assert h.expected["rent"].budget == "housing" and h.expected["rent"].repeats == "FREQ=MONTHLY"
    assert h.expected["pay-semimonthly"].budget is None
    assert h.accounts["card"].cls == "liability" and h.accounts["card"].on_budget is True
    assert h.accounts["checking"].opening_date == date(2025, 12, 31)
    assert h.budgets["groceries"].fill == Fill("amount", Decimal("300.00"), "FREQ=MONTHLY")


def test_month_counts_across_a_year_boundary():                    # focus 2 (see the plan)
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
        household_from(tmp_path, MINIMAL.replace('name = "Example Employer"', f'name = "Employer {PRIVATE}"'))


def test_money_going_out_needs_a_budget(tmp_path):
    bill = ('\n[[expected]]\nkey = "gift"\nname = "gift"\ntype = "gift"\nfrom = "checking"\nto = "landlord"\n'
            'amount = 400\nstarts = 2026-01-02\nrepeats = "FREQ=MONTHLY"\n')
    with pytest.raises(HouseholdError, match=r"mini: expected gift: money going out needs a budget"):
        household_from(tmp_path, MINIMAL + bill)


def test_a_key_used_twice_names_the_item(tmp_path):
    twice = MINIMAL + '\n[[expected]]\nuse = "rent"\nfrom = "checking"\nto = "landlord"\namount = 600\nstarts = 2026-01-01\n'
    with pytest.raises(HouseholdError, match=r"mini: expected rent: appears twice"):
        household_from(tmp_path, twice)


def test_to_assign_is_a_reserved_budget_key(tmp_path):
    with pytest.raises(HouseholdError, match=r"mini: budget to-assign: is reserved"):
        household_from(tmp_path, MINIMAL + '\n[[budget]]\nkey = "to-assign"\nname = "to assign"\n')


def test_line_amounts_are_positive(tmp_path):
    with pytest.raises(HouseholdError, match="positive"):
        household_from(tmp_path, MINIMAL + MOMENT.format(
            lines='{ budget = "groceries", amount = 100 }, { budget = "housing", amount = -50 }'))


def test_one_spending_entry_per_budget(tmp_path):
    from households_support import SPENDING
    two = MINIMAL + SPENDING + SPENDING.replace('on = "card"', 'on = "checking"')
    with pytest.raises(HouseholdError, match=r"mini: spending groceries: appears twice"):
        household_from(tmp_path, two)


def test_a_household_starts_on_the_first(tmp_path):
    with pytest.raises(HouseholdError, match="starts on the 1st"):
        household_from(tmp_path, MINIMAL.replace("start = 2026-01-01", "start = 2026-01-15"))
