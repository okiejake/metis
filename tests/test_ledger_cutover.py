"""The ledger splits its timeline at the actuals cutover: the latest imported
checking date. Everything on or before it came from imports, everything after is
forecast. The cutover date itself is the seam — an import taken that day may not
yet include a bill that posts later the same day, so an expected occurrence due
exactly on the cutover must still surface, flagged as due-but-not-yet-posted.

All data here is synthetic.
"""

from datetime import date, timedelta

from conftest import add_bill, add_imported, due_months_before, reconcile, rows_on

TODAY = date.today()
WINDOW_START = TODAY - timedelta(days=30)
WINDOW_END = TODAY + timedelta(days=60)


def seed_cutover_today(db, user_id, accounts):
    """An unrelated checking import dated today, which sets the actuals cutover
    to today without settling anything else."""
    return add_imported(
        db,
        user_id,
        accounts["Example Checking"],
        "Example Checking",
        TODAY,
        "Example Grocer",
        -50.00,
    )


def test_expected_item_due_on_cutover_date_still_appears(db, user_id, accounts):
    seed_cutover_today(db, user_id, accounts)
    add_bill(
        db,
        user_id,
        "Acme Utilities",
        2000.00,
        first_due_date=due_months_before(TODAY, 4),
        account_id=accounts["Example Checking"],
    )

    transactions = db.collect_blended_transactions(user_id, WINDOW_START, WINDOW_END)

    due = rows_on(transactions, TODAY, "Acme Utilities")
    assert len(due) == 1, "expected occurrence due on the cutover date was dropped"
    assert due[0]["due_not_posted"] is True


def test_due_on_cutover_row_moves_the_running_balance(db, user_id, accounts):
    seed_cutover_today(db, user_id, accounts)
    add_bill(
        db,
        user_id,
        "Acme Utilities",
        2000.00,
        first_due_date=due_months_before(TODAY, 4),
        account_id=accounts["Example Checking"],
    )

    transactions = db.collect_blended_transactions(user_id, WINDOW_START, WINDOW_END)
    due = rows_on(transactions, TODAY, "Acme Utilities")[0]

    assert due["delta"] == -2000.00
    assert due["expense"] == 2000.00

    _, _, ending_balance = db.build_ledger_rows(transactions, 10000.00, WINDOW_START)
    without_due = sum(tx["delta"] for tx in transactions if tx is not due)
    assert ending_balance == 10000.00 + without_due - 2000.00


def test_reconciled_occurrence_on_cutover_date_is_not_double_counted(db, user_id, accounts):
    item_id = add_bill(
        db,
        user_id,
        "Acme Utilities",
        2000.00,
        first_due_date=due_months_before(TODAY, 4),
        account_id=accounts["Example Checking"],
    )
    tx_id = add_imported(
        db,
        user_id,
        accounts["Example Checking"],
        "Example Checking",
        TODAY,
        "Acme Utilities Co",
        -2000.00,
    )
    reconcile(db, user_id, "bill", item_id, tx_id)

    transactions = db.collect_blended_transactions(user_id, WINDOW_START, WINDOW_END)

    todays_rows = rows_on(transactions, TODAY)
    assert len(todays_rows) == 1, "the settled occurrence was counted twice"
    assert todays_rows[0]["type"] == "actual"
    assert not todays_rows[0].get("due_not_posted")


def test_unposted_occurrence_before_the_cutover_date_stays_hidden(db, user_id, accounts):
    seed_cutover_today(db, user_id, accounts)
    earlier = TODAY - timedelta(days=10)
    add_bill(
        db,
        user_id,
        "Example Insurance",
        75.00,
        first_due_date=due_months_before(earlier, 4),
        account_id=accounts["Example Checking"],
    )

    transactions = db.collect_blended_transactions(user_id, WINDOW_START, WINDOW_END)

    assert rows_on(transactions, earlier, "Example Insurance") == [], (
        "only the cutover date is treated as still-settling; earlier dates were "
        "fully covered by the same import"
    )


def test_forecast_after_the_cutover_is_not_flagged_as_due(db, user_id, accounts):
    seed_cutover_today(db, user_id, accounts)
    add_bill(
        db,
        user_id,
        "Acme Utilities",
        2000.00,
        first_due_date=due_months_before(TODAY, 4),
        account_id=accounts["Example Checking"],
    )

    transactions = db.collect_blended_transactions(user_id, WINDOW_START, WINDOW_END)

    later = [
        tx for tx in transactions if tx["description"] == "Acme Utilities" and tx["date"] > TODAY
    ]
    assert later, "future occurrences should still forecast normally"
    assert all(not tx.get("due_not_posted") for tx in later)
