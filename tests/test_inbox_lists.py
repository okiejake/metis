"""The inbox lists everything it cannot yet account for, in four lists, largest
amounts first, and measures how much spending has a category. Nothing appears
in two lists, and the inbox's uncategorized total is Budget's.

All data here is synthetic.
"""

from datetime import date, timedelta

import pytest

import inbox
from conftest import add_imported, quiet_imports
from merchants import merchant_key

TODAY = date.today()
THIS_MONTH = (TODAY.replace(day=1), TODAY)


@pytest.fixture
def savings(db, user_id):
    with db.get_connection() as conn:
        return int(conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) VALUES (?, 'Example Savings', 'checking') RETURNING id",
            [user_id],
        ).fetchone()[0])


def spend(db, user_id, accounts, description, amount, day=TODAY):
    return add_imported(db, user_id, accounts["Example Card"], "Example Card", day, description, amount)


def keys(rows):
    return [row["key"] for row in rows]


def test_nothing_appears_in_two_lists(db, user_id, accounts, savings):
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY, "EXAMPLE SAVINGS XFER", -300.0)
    add_imported(db, user_id, savings, "Example Savings", TODAY, "TRANSFER IN", 300.0, flow="income")

    lists = inbox.build_inbox(user_id, *THIS_MONTH)

    assert keys(lists["transfers"]) == [merchant_key("EXAMPLE SAVINGS XFER")]
    assert lists["transfers"][0]["arrived_in"] == "Example Savings"
    assert merchant_key("EXAMPLE SAVINGS XFER") not in keys(lists["categories"])


def test_categories_are_largest_first_with_a_suggestion_from_the_same_merchant(db, user_id, accounts):
    groceries = db.create_category(user_id, "Groceries")
    old = spend(db, user_id, accounts, "ACME GROCER #1", -10.0, day=TODAY - timedelta(days=400))
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [groceries, old])
    spend(db, user_id, accounts, "ACME GROCER #2", -30.0)
    spend(db, user_id, accounts, "ACME GROCER #3", -30.0)
    spend(db, user_id, accounts, "EXAMPLE FUEL", -50.0)

    rows = inbox.build_inbox(user_id, *THIS_MONTH)["categories"]

    assert keys(rows) == [merchant_key("ACME GROCER #2"), merchant_key("EXAMPLE FUEL")]
    assert (rows[0]["count"], rows[0]["total"], rows[0]["suggested_category_id"]) == (2, 60.0, groceries)
    assert rows[1]["suggested_category_id"] is None


@pytest.mark.parametrize("categorized, expected", [(0, 0), (1, 50), (2, 100)])
def test_the_meter_for_none_some_and_all(db, user_id, accounts, categorized, expected):
    dining = db.create_category(user_id, "Dining")
    for index, name in enumerate(["ACME DINER", "EXAMPLE CAFE"]):
        tx = spend(db, user_id, accounts, name, -20.0)
        if index < categorized:
            db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])

    assert inbox.build_inbox(user_id, *THIS_MONTH)["meter"]["percent"] == expected


def test_no_spending_means_no_meter_and_nothing_waiting(db, user_id):
    lists = inbox.build_inbox(user_id, *THIS_MONTH)

    assert lists["meter"]["percent"] is None
    assert lists["waiting"] == 0


def test_inbox_uncategorized_equals_budget_uncategorized(db, user_id, accounts):
    dining = db.create_category(user_id, "Dining")
    tx = spend(db, user_id, accounts, "ACME MARKET", -100.0)
    db.save_transaction_splits(user_id, tx, [{"category_id": dining, "amount": 40.0, "note": ""}])
    spend(db, user_id, accounts, "EXAMPLE FUEL", -25.0)

    meter = inbox.build_inbox(user_id, *THIS_MONTH)["meter"]
    budget = db.build_budget_summary(user_id, *THIS_MONTH)
    uncategorized = [c["actual"] for c in budget["categories"] if c["name"] == "Uncategorized"][0]

    assert meter["uncategorized"] == uncategorized == 85.0


def test_a_dismissed_transfer_moves_to_categories(db, user_id, accounts, savings):
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY, "EXAMPLE SAVINGS XFER", -300.0)
    add_imported(db, user_id, savings, "Example Savings", TODAY, "TRANSFER IN", 300.0, flow="income")

    inbox.dismiss(user_id, "transfer:" + merchant_key("EXAMPLE SAVINGS XFER"))
    lists = inbox.build_inbox(user_id, *THIS_MONTH)

    assert lists["transfers"] == []
    assert merchant_key("EXAMPLE SAVINGS XFER") in keys(lists["categories"])


def test_there_is_no_budgets_list(db, user_id, accounts):
    """A category's budget is its expected transactions (#104)."""
    dining = db.create_category(user_id, "Dining")
    tx = spend(db, user_id, accounts, "ACME DINER", -30.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])

    assert "budgets" not in inbox.build_inbox(user_id, *THIS_MONTH)


def test_the_waiting_count_follows_each_action(db, user_id, accounts):
    """Categorizing a merchant clears it, and the remembered count follows."""
    quiet_imports(user_id)
    groceries = db.create_category(user_id, "Groceries")
    spend(db, user_id, accounts, "ACME GROCER #2", -30.0)
    db.save_forecast_window(user_id, *THIS_MONTH)
    assert inbox.inbox_waiting_count(user_id) == 1

    action = inbox.confirm_category(user_id, merchant_key("ACME GROCER #2"), groceries)
    assert inbox.inbox_waiting_count(user_id) == 0

    inbox.undo(user_id, action)
    assert inbox.inbox_waiting_count(user_id) == 1
