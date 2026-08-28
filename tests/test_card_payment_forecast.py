"""A projected card payment stands for the balance still owed on a closed
statement. Recording a payment against that statement should reduce the
projection by what was paid — not cancel it outright. Cancelling on the mere
presence of a payment hides the remainder of any partially paid statement, and
cards that take several payments per cycle are partially paid most of the time.

All data here is synthetic. Dates are fixed rather than relative to today: this
path takes its window and cutover as arguments and never consults the clock.
"""

from datetime import date

from conftest import add_imported, rows_on

# The fixture card closes on the 5th and is due on the 25th of the same month.
PREV_CLOSE = date(2026, 6, 5)
CLOSE = date(2026, 7, 5)
DUE = date(2026, 7, 25)

CUTOVER = date(2026, 7, 6)
WINDOW_START = date(2026, 7, 7)
WINDOW_END = date(2026, 8, 31)

STATEMENT_TOTAL = 400.00


def charge_the_statement(db, user_id, accounts):
    """Two charges inside the cycle that closes on CLOSE, totalling 400.00."""
    for day, amount, name in ((10, -300.00, "Acme Hardware"), (28, -100.00, "Acme Books")):
        add_imported(
            db,
            user_id,
            accounts["Example Card"],
            "Example Card",
            date(2026, 6, day),
            name,
            amount,
        )


def pay_the_card(db, user_id, accounts, amount, when=date(2026, 7, 6)):
    add_imported(
        db,
        user_id,
        accounts["Example Card"],
        "Example Card",
        when,
        "Example Card Payment",
        amount,
        flow="transfer",
        is_transfer=True,
    )


def forecast(db, user_id):
    return db.forecast_card_payments(user_id, WINDOW_START, WINDOW_END, CUTOVER)


def test_partial_payment_leaves_the_remainder_projected(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 100.00)

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1, "a partially paid statement still owes the remainder"
    assert projected[0]["delta"] == -300.00
    assert projected[0]["expense"] == 300.00


def test_fully_paid_statement_projects_nothing(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, STATEMENT_TOTAL)

    assert rows_on(forecast(db, user_id), DUE) == []


def test_overpaid_statement_projects_nothing(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 500.00)

    projected = rows_on(forecast(db, user_id), DUE)
    assert projected == [], "an overpaid statement must not project a payment"


def test_unpaid_statement_projects_the_whole_balance(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["description"] == "Example Card statement"
    assert projected[0]["delta"] == -STATEMENT_TOTAL


def test_several_partial_payments_are_summed(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 100.00, when=date(2026, 7, 6))
    pay_the_card(db, user_id, accounts, 50.00, when=date(2026, 7, 12))

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["delta"] == -250.00


def test_payments_applied_appear_in_the_charge_breakdown(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 100.00)

    projected = rows_on(forecast(db, user_id), DUE)[0]

    applied = [item for item in projected["card_charges"] if item["kind"] == "payment"]
    assert len(applied) == 1, "the breakdown must account for the payment already made"
    assert applied[0]["delta"] == 100.00
    total = sum(item["delta"] for item in projected["card_charges"])
    assert round(total, 2) == projected["delta"], "breakdown must reconcile to the total"


def test_payment_outside_the_settle_window_does_not_reduce_the_projection(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    # Paid before the statement even closed: that money belongs to the prior cycle.
    pay_the_card(db, user_id, accounts, 100.00, when=date(2026, 7, 1))

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["delta"] == -STATEMENT_TOTAL
