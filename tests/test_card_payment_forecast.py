"""A projected card row stands for the balance a statement carried when it closed.
Recording a payment against that statement should reduce the projection by what was
paid — not cancel it outright. Cancelling on the mere presence of a payment hides
the remainder of any partially paid statement, and cards that take several payments
per cycle are partially paid most of the time.

The balance is what carries between cycles, not the cycle's charges in isolation. A
card paid down continuously overpays its open statement for most of the month; that
surplus belongs to the next statement, and billing each cycle on its own charges
alone would re-bill spend the surplus already settled.

All data here is synthetic. Dates are fixed rather than relative to today: this path
takes its window, cutover and "today" as arguments, so the clock never decides which
statements count as closed.
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
CARRIED = 100.00


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


def charge_the_previous_statement(db, user_id, accounts, amount=-CARRIED):
    """One charge in the cycle that closed on PREV_CLOSE, so this statement has a
    balance carried into it. That statement is due before the cutover, so the
    forecast never bills it directly — only what it left behind."""
    add_imported(
        db,
        user_id,
        accounts["Example Card"],
        "Example Card",
        date(2026, 6, 1),
        "Acme Supply",
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


# The statement closing on CLOSE has closed by now, but the one after it has not.
TODAY = date(2026, 7, 10)


def forecast(db, user_id, window_start=WINDOW_START, today=TODAY):
    return db.forecast_card_payments(user_id, window_start, WINDOW_END, CUTOVER, today=today)


def reconciles(statement):
    """A statement's summary adds up, the way a printed one does."""
    built = (
        statement["previous_balance"] + statement["charges"] - statement["credits"]
        - statement["payments"] + statement["expected_charges"] - statement["assumed_payments"]
    )
    return (
        round(built, 2) == statement["computed_balance"]
        and round(statement["balance"] - statement["paid_since_close"], 2) == statement["remaining"]
    )


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
    assert projected[0]["delta"] == -STATEMENT_TOTAL


def test_several_partial_payments_are_summed(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 100.00, when=date(2026, 7, 6))
    pay_the_card(db, user_id, accounts, 50.00, when=date(2026, 7, 12))

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["delta"] == -250.00


def test_a_closed_statement_is_named_as_one(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)

    projected = rows_on(forecast(db, user_id), DUE)[0]

    assert projected["description"] == "Example Card statement"
    assert projected["statement"]["closed"] is True


def test_a_statement_not_closed_yet_is_named_as_an_estimate(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)

    projected = rows_on(forecast(db, user_id, today=date(2026, 7, 1)), DUE)[0]

    assert projected["description"] == "Example Card estimated statement"
    assert projected["statement"]["closed"] is False


def test_payments_applied_are_part_of_the_statement_behind_the_row(db, user_id, accounts):
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 100.00)

    projected = rows_on(forecast(db, user_id), DUE)[0]
    statement = projected["statement"]

    assert statement["paid_since_close"] == 100.00, "it must account for the payment already made"
    assert [item["delta"] for item in statement["paid_after"]] == [100.00]
    assert reconciles(statement)
    assert -statement["remaining"] == projected["delta"], "the row is what is left to pay"


def test_an_unpaid_previous_statement_carries_into_this_one(db, user_id, accounts):
    charge_the_previous_statement(db, user_id, accounts)
    charge_the_statement(db, user_id, accounts)

    projected = rows_on(forecast(db, user_id), DUE)[0]

    assert projected["delta"] == -(STATEMENT_TOTAL + CARRIED)
    statement = projected["statement"]
    assert statement["previous_balance"] == CARRIED, "it must show where the extra balance came from"
    assert reconciles(statement)


def test_a_payment_inside_the_cycle_settles_the_previous_statement_first(db, user_id, accounts):
    charge_the_previous_statement(db, user_id, accounts)
    charge_the_statement(db, user_id, accounts)
    # Paid before this statement closed, and only as much as the last one owed.
    pay_the_card(db, user_id, accounts, CARRIED, when=date(2026, 7, 1))

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["delta"] == -STATEMENT_TOTAL


def test_overpaying_inside_the_cycle_carries_the_surplus_forward(db, user_id, accounts):
    charge_the_previous_statement(db, user_id, accounts)
    charge_the_statement(db, user_id, accounts)
    # 250 against a 100 statement: the extra 150 has already paid down this cycle.
    pay_the_card(db, user_id, accounts, 250.00, when=date(2026, 7, 1))

    projected = rows_on(forecast(db, user_id), DUE)

    assert len(projected) == 1
    assert projected[0]["delta"] == -250.00, "the surplus must not be re-billed"


def test_in_cycle_payments_are_itemized_in_the_breakdown(db, user_id, accounts):
    charge_the_previous_statement(db, user_id, accounts)
    charge_the_statement(db, user_id, accounts)
    pay_the_card(db, user_id, accounts, 60.00, when=date(2026, 6, 20))
    pay_the_card(db, user_id, accounts, 40.00, when=date(2026, 7, 1))

    projected = rows_on(forecast(db, user_id), DUE)[0]

    statement = projected["statement"]
    paid = [item for item in statement["activity"] if item["kind"] == "payment"]
    assert [item["delta"] for item in paid] == [60.00, 40.00]
    assert statement["payments"] == 100.00
    assert reconciles(statement)


def test_a_later_window_bills_the_same_statement_as_a_wide_one(db, user_id, accounts):
    """A window that opens after an unpaid statement's due date must not roll that
    statement into the next one: cycles before the window are still simulated."""
    charge_the_statement(db, user_id, accounts)
    add_imported(
        db,
        user_id,
        accounts["Example Card"],
        "Example Card",
        date(2026, 7, 20),
        "Acme Freight",
        -75.00,
    )
    next_due = date(2026, 8, 25)

    wide = rows_on(forecast(db, user_id), next_due)
    narrow = rows_on(forecast(db, user_id, window_start=date(2026, 8, 1)), next_due)

    assert len(wide) == 1 and len(narrow) == 1
    assert narrow[0]["delta"] == wide[0]["delta"] == -75.00
