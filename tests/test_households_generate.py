"""Generated rows for bills, income, cards and story moments."""

from datetime import date
from decimal import Decimal

from households_support import MINIMAL, MOMENT, household_from, key_id, lines_of
from tools.households.generate import generate


def test_bills_post_up_to_two_days_late_with_a_line_to_the_expected(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    rent_id = key_id(rows, "expected_transactions", "rent")
    rent = [t for t in rows["transactions"] if lines_of(rows, t)[0]["expected_transaction_id"] == rent_id]
    assert [t["amount"] for t in rent] == [Decimal("-500.00")] * 2
    assert all(0 <= t["posted_on"].day - 1 <= 2 for t in rent)


def test_income_is_recorded_on_the_own_account_as_money_in(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    pay = [t for t in rows["transactions"] if t["amount"] == Decimal("1000.00")]
    assert len(pay) == 4 and {t["account_id"] for t in pay} == {key_id(rows, "accounts", "checking")}


def test_loan_payment_is_a_line_not_a_bare_transfer(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    loan = key_id(rows, "accounts", "loan")
    payments = [t for t in rows["transactions"] if t["counterparty_account_id"] == loan]
    assert payments and all(len(lines_of(rows, t)) == 1 for t in payments)


def test_card_is_paid_for_what_was_charged(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + MOMENT.format(lines='{ budget = "groceries", amount = 50 }')))
    card = key_id(rows, "accounts", "card")
    pay = [t for t in rows["transactions"] if t["counterparty_account_id"] == card]
    assert [(t["posted_on"], t["amount"]) for t in pay] == [(date(2026, 1, 25), Decimal("-50.00"))]
    assert lines_of(rows, pay[0]) == []


def test_to_assign_is_the_first_budget(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    assert rows["budgets"][0]["key"] == "to-assign" and rows["budgets"][0]["name"] == "to assign"


def test_same_household_same_rows(tmp_path):
    h = household_from(tmp_path, MINIMAL)
    assert generate(h) == generate(h)


def test_nothing_is_dated_before_the_household_starts(tmp_path):
    early = MINIMAL.replace('amount = 500.00\nstarts = 2026-01-01', 'amount = 500.00\nstarts = 2025-10-01')
    rows = generate(household_from(tmp_path, early))
    assert min(t["posted_on"] for t in rows["transactions"]) >= date(2026, 1, 1)
    rent_id = key_id(rows, "expected_transactions", "rent")
    assert sum(1 for line in rows["transaction_lines"] if line["expected_transaction_id"] == rent_id) == 2
