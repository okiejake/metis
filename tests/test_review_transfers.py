"""Money moved between the owner's own accounts is not spending. Review finds
debits matched by an equal amount arriving in another account, and confirming
one marks every charge from that merchant as a transfer, past and future,
together with its arrival when that landed outside a card. Balances and the
cash forecast do not change.

All data here is synthetic.
"""

from datetime import date

import pytest

import review
from conftest import add_imported
from merchants import merchant_key

JUNE = (date(2026, 6, 1), date(2026, 6, 30))
SAVINGS_KEY = merchant_key("EXAMPLE SAVINGS XFER")


@pytest.fixture
def savings(db, user_id):
    with db.get_connection() as conn:
        return int(conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) VALUES (?, 'Example Savings', 'checking') RETURNING id",
            [user_id],
        ).fetchone()[0])


def transfer_flags(db, *tx_ids):
    return [bool(db.fetch_one("SELECT is_transfer FROM imported_transactions WHERE id = ?", [tx])[0]) for tx in tx_ids]


def move_to_savings(db, user_id, accounts, savings, day, month=6):
    out = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, month, day), "EXAMPLE SAVINGS XFER", -300.0)
    arrival = add_imported(db, user_id, savings, "Example Savings", date(2026, month, day + 1), "TRANSFER FROM CHECKING", 300.0, flow="income")
    return out, arrival


def test_a_card_payment_from_checking_pairs_with_the_card_s_payment_row(db, user_id, accounts):
    out = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 3), "EXAMPLE CARD PMT", -500.0)
    add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 5), "PAYMENT THANK YOU", 500.0, is_transfer=True)

    paired = review.paired_outflows(user_id, *JUNE)

    assert paired[out]["account_id"] == accounts["Example Card"]
    assert paired[out]["account_type"] == "credit_card"


def test_confirming_a_transfer_removes_it_from_spending_and_income(db, user_id, accounts, savings):
    out, arrival = move_to_savings(db, user_id, accounts, savings, 3)

    assert review.confirm_transfer(user_id, SAVINGS_KEY) is not None

    assert transfer_flags(db, out, arrival) == [True, True]
    assert db.spending_parts(user_id, *JUNE) == []
    assert db.income_total(user_id, *JUNE) == 0.0


def test_an_arrival_on_a_card_is_left_as_imported(db, user_id, accounts):
    out = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 3), "EXAMPLE CARD PMT", -500.0)
    arrival = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 5), "ACME CREDIT", 500.0, flow="refund")

    review.confirm_transfer(user_id, merchant_key("EXAMPLE CARD PMT"))

    assert transfer_flags(db, out, arrival) == [True, False]


def test_balances_and_the_forecast_do_not_change(db, user_id, accounts, savings):
    move_to_savings(db, user_id, accounts, savings, 3)

    def figures():
        start = db.checking_actual_balance_before(user_id, JUNE[0], 1000.0)
        rows = db.collect_blended_transactions(user_id, *JUNE)
        return start, db.build_daily_balance_series(*JUNE, start, rows)

    before = figures()
    review.confirm_transfer(user_id, SAVINGS_KEY)

    assert figures() == before


def test_a_later_import_and_its_late_arrival_are_marked(db, user_id, accounts, savings):
    move_to_savings(db, user_id, accounts, savings, 3)
    review.confirm_transfer(user_id, SAVINGS_KEY)

    later = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 7, 3), "EXAMPLE SAVINGS XFER", -300.0)
    review.apply_review_decisions(user_id)
    late_arrival = add_imported(db, user_id, savings, "Example Savings", date(2026, 7, 5), "TRANSFER FROM CHECKING", 300.0, flow="income")
    review.apply_review_decisions(user_id)

    assert transfer_flags(db, later, late_arrival) == [True, True]


def test_undo_counts_it_as_spending_again(db, user_id, accounts, savings):
    out, arrival = move_to_savings(db, user_id, accounts, savings, 3)
    action = review.confirm_transfer(user_id, SAVINGS_KEY)

    review.undo(user_id, action)
    later = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 7, 3), "EXAMPLE SAVINGS XFER", -300.0)
    review.apply_review_decisions(user_id)

    assert transfer_flags(db, out, arrival, later) == [False, False, False]


def test_card_charges_are_never_transfer_candidates(db, user_id, accounts):
    charge = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 3), "ACME CASH ADVANCE", -40.0)
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 4), "ACME DEPOSIT", 40.0, flow="income")

    assert charge not in review.paired_outflows(user_id, *JUNE)
    assert review.confirm_transfer(user_id, merchant_key("ACME CASH ADVANCE")) is None


def test_one_charge_can_be_marked_a_transfer_by_hand(db, user_id, accounts):
    tx = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 3), "ACME ATM", -60.0)

    action = review.set_charge(user_id, tx, "transfer")
    assert transfer_flags(db, tx) == [True]

    review.undo(user_id, action)
    assert transfer_flags(db, tx) == [False]
