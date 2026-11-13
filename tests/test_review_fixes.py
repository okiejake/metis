"""Findings from the whole-branch review, each pinned by a test.

All data here is synthetic.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import review
from conftest import add_imported
from merchants import merchant_key
from services import SESSION_COOKIE

TODAY = date.today()
WINDOW = (TODAY.replace(day=1), TODAY)


@pytest.fixture
def savings(db, user_id):
    with db.get_connection() as conn:
        return int(conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) VALUES (?, 'Example Savings', 'checking') RETURNING id",
            [user_id],
        ).fetchone()[0])


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    db.save_forecast_window(user_id, *WINDOW)
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def row(db, tx):
    return db.fetch_one("SELECT is_transfer, category_id FROM imported_transactions WHERE id = ?", [tx])


MIXED = merchant_key("EXAMPLE MOVE")


def mixed_merchant(db, user_id, accounts, savings):
    """Debits from checking that land in savings, plus one card charge."""
    out = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY, "EXAMPLE MOVE", -300.0)
    add_imported(db, user_id, savings, "Example Savings", TODAY, "TRANSFER IN", 300.0, flow="income")
    card = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "EXAMPLE MOVE", -20.0)
    return out, card


# Important 1: a merchant can have a transfer decision and a category decision.

def test_a_category_for_the_card_part_keeps_the_transfer_decision(db, user_id, accounts, savings):
    out, card = mixed_merchant(db, user_id, accounts, savings)
    fees = db.create_category(user_id, "Fees")
    transfer_action = review.confirm_transfer(user_id, MIXED)
    review.confirm_category(user_id, MIXED, fees)

    later = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY - timedelta(days=1), "EXAMPLE MOVE", -300.0)
    review.apply_review_decisions(user_id)
    assert row(db, later)[0] is True
    assert row(db, card) == (False, fees)

    review.undo(user_id, transfer_action)
    assert row(db, out)[0] is False and row(db, later)[0] is False
    assert row(db, card) == (False, fees)


def test_undoing_a_category_reaches_rows_later_marked_as_transfers(db, user_id, accounts, savings):
    out, card = mixed_merchant(db, user_id, accounts, savings)
    fees = db.create_category(user_id, "Fees")
    category_action = review.confirm_category(user_id, MIXED, fees)
    review.confirm_transfer(user_id, MIXED)

    review.undo(user_id, category_action)

    assert row(db, out)[1] is None and row(db, card)[1] is None


# Important 2: card rows are never marked as transfers.

def test_a_card_charge_cannot_be_marked_a_transfer(db, user_id, accounts):
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME CASH", -40.0)

    with pytest.raises(ValueError):
        review.set_charge(user_id, tx, "transfer")
    assert row(db, tx)[0] is False


def test_transfer_is_not_offered_for_card_charges(client, db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME CASH", -40.0)

    page = client.get("/review").text

    assert 'id="categories"' in page and 'value="transfer"' not in page


# Important 3: the meter stays within 0..100 and uncategorized refunds are listed.

def test_an_uncategorized_refund_is_listed_and_the_meter_stays_in_range(db, user_id, accounts):
    dining = db.create_category(user_id, "Dining")
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME DINER", -100.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])
    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME DINER REFUND", 30.0, flow="refund")

    lists = review.build_review(user_id, *WINDOW)

    assert 0 <= lists["meter"]["percent"] <= 100
    assert [r["total"] for r in lists["categories"]] == [-30.0]


# Important 4: a repeated budget Confirm records nothing.

def test_a_repeated_budget_confirm_records_nothing(db, user_id):
    dining = db.create_category(user_id, "Dining")
    first = review.confirm_budget(user_id, dining, 100.0)

    assert review.confirm_budget(user_id, dining, 100.0) is None
    review.undo(user_id, first)
    assert db.load_category_by_id(user_id, dining)["budget_amount"] is None


# Important 5: a repeated recurring Confirm creates nothing.

def test_a_repeated_recurring_confirm_creates_nothing(client, db, user_id, accounts):
    for months in (4, 3, 2, 1):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=30 * months),
                     "ACME STREAMING", -10.0)
    s = review.build_review(user_id, *WINDOW)["recurring"][0]
    form = {
        "name": s["name"], "kind": "expense", "amount": "10.00", "start_date": s["start_date"],
        "frequency_type": "monthly", "interval_months": "1", "day_of_month": str(s["day_of_month"]),
        "day_of_month_touched": "1", "suggestion_key": s["key"], "next_path": "/review#recurring",
    }
    client.post("/recurring", data=form, follow_redirects=False)

    again = client.post("/recurring", data=form, follow_redirects=False)

    assert db.fetch_one("SELECT COUNT(*) FROM recurring_items")[0] == 1
    assert "Nothing+left+to+change" in again.headers["location"]


# Important 6: "New category" is safe against a repeated or stale Confirm, and Undo removes it.

def test_new_category_is_safe_to_repeat_and_undo_removes_it(client, db, user_id, accounts):
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "EXAMPLE FUEL", -50.0)
    form = {"key": merchant_key("EXAMPLE FUEL"), "choice": "new", "new_category_name": "Fuel", "list": "categories"}
    first = client.post("/review/merchants/confirm", data=form, follow_redirects=False)

    again = client.post("/review/merchants/confirm", data=form, follow_redirects=False)
    assert "Nothing+left+to+change" in again.headers["location"]
    assert db.fetch_one("SELECT COUNT(*) FROM categories WHERE name = 'Fuel'")[0] == 1

    action = int(first.headers["location"].split("undo=")[1].split("#")[0])
    client.post(f"/review/undo/{action}", follow_redirects=False)
    assert db.fetch_one("SELECT COUNT(*) FROM categories WHERE name = 'Fuel'")[0] == 0
    assert row(db, tx)[1] is None


def test_a_stale_new_category_confirm_creates_nothing(client, db, user_id):
    client.post("/review/merchants/confirm",
                data={"key": "NOTHING HERE", "choice": "new", "new_category_name": "Fuel", "list": "categories"},
                follow_redirects=False)

    assert db.fetch_one("SELECT COUNT(*) FROM categories WHERE name = 'Fuel'")[0] == 0


def test_a_stale_new_category_on_a_missing_charge_creates_nothing(client, db, user_id):
    client.post("/review/charges/999999", data={"choice": "new", "new_category_name": "Fuel"}, follow_redirects=False)

    assert db.fetch_one("SELECT COUNT(*) FROM categories WHERE name = 'Fuel'")[0] == 0
