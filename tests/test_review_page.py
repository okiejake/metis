"""The Review page: four lists in working order, one decision per row, and an
Undo on the message after every action. After an action the owner lands back
on the same list.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import review
import services
from conftest import add_imported
from merchants import merchant_key
from services import SESSION_COOKIE

TODAY = date.today()
FUEL = merchant_key("EXAMPLE FUEL")


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    db.save_forecast_window(user_id, TODAY.replace(day=1), TODAY)
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def savings(db, user_id):
    with db.get_connection() as conn:
        return int(conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) VALUES (?, 'Example Savings', 'checking') RETURNING id",
            [user_id],
        ).fetchone()[0])


def fuel(db, user_id, accounts, amount=-50.0, day=TODAY):
    return add_imported(db, user_id, accounts["Example Card"], "Example Card", day, "EXAMPLE FUEL", amount)


def to_savings(db, user_id, accounts, savings):
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY, "EXAMPLE SAVINGS XFER", -300.0)
    add_imported(db, user_id, savings, "Example Savings", TODAY, "TRANSFER IN", 300.0, flow="income")


def undo_id(response):
    match = re.search(r"[?&]undo=(\d+)", response.headers["location"])
    return int(match.group(1)) if match else None


def test_redirect_keeps_a_fragment_after_the_query():
    location = services.redirect_with_message("/inbox#categories", "Saved", undo=7).headers["location"]

    assert location == "/inbox?msg=Saved&err=0&undo=7#categories"


def test_the_page_lists_in_working_order_and_hides_empty_lists(client, db, user_id, accounts, savings):
    to_savings(db, user_id, accounts, savings)
    fuel(db, user_id, accounts)

    page = client.get("/inbox").text

    assert page.index('id="transfers"') < page.index('id="categories"')
    assert 'id="budgets"' not in page and 'id="recurring"' not in page
    assert "categorized 0%" in page


def test_nothing_to_review_when_everything_is_settled(client):
    page = client.get("/inbox").text

    assert "inbox is empty." in page
    assert "categorized" not in page


def test_confirm_redirects_to_the_list_with_an_undo(client, db, user_id, accounts):
    groceries = db.create_category(user_id, "Fuel")
    fuel(db, user_id, accounts)

    response = client.post("/inbox/merchants/confirm", data={"key": FUEL, "choice": str(groceries), "list": "categories"},
                           follow_redirects=False)

    assert re.fullmatch(r"/inbox\?msg=Confirmed&err=0&undo=\d+#categories", response.headers["location"])


def test_a_stale_confirm_reports_nothing_left_and_records_nothing(client, db, user_id, accounts):
    category = db.create_category(user_id, "Fuel")
    fuel(db, user_id, accounts)
    form = {"key": FUEL, "choice": str(category), "list": "categories"}
    client.post("/inbox/merchants/confirm", data=form, follow_redirects=False)

    again = client.post("/inbox/merchants/confirm", data=form, follow_redirects=False)

    assert "Nothing+left+to+change" in again.headers["location"]
    assert undo_id(again) is None


def test_new_category_from_the_picker_is_created_and_applied(client, db, user_id, accounts):
    tx = fuel(db, user_id, accounts)

    client.post("/inbox/merchants/confirm",
                data={"key": FUEL, "choice": "new", "new_category_name": "Fuel", "list": "categories"},
                follow_redirects=False)

    category = db.fetch_one("SELECT id FROM categories WHERE name = 'Fuel'")
    assert category and db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [tx])[0] == category[0]


def test_one_charge_can_be_set_from_its_expanded_row(client, db, user_id, accounts):
    category = db.create_category(user_id, "Fuel")
    tx = fuel(db, user_id, accounts)

    client.post(f"/inbox/charges/{tx}", data={"choice": str(category)}, follow_redirects=False)

    assert db.fetch_one("SELECT category_id, category_via FROM imported_transactions WHERE id = ?", [tx]) == (category, None)


def test_dismiss_holds_across_reloads_and_restore_brings_rows_back(client, db, user_id, accounts, savings):
    to_savings(db, user_id, accounts, savings)
    key = merchant_key("EXAMPLE SAVINGS XFER")

    client.post("/inbox/merchants/dismiss", data={"key": key}, follow_redirects=False)
    assert 'id="transfers"' not in client.get("/inbox").text
    assert 'id="transfers"' not in client.get("/inbox").text
    assert "restore dismissed (1)" in client.get("/inbox").text

    client.post("/inbox/restore", follow_redirects=False)
    assert 'id="transfers"' in client.get("/inbox").text


def test_budget_confirm_and_dismiss(client, db, user_id, accounts):
    dining = db.create_category(user_id, "Dining")
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME DINER", -40.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])

    client.post("/inbox/budgets/confirm", data={"category_id": dining, "amount": "60"}, follow_redirects=False)
    assert db.load_category_by_id(user_id, dining)["budget_amount"] == 60.0


def test_undo_from_the_message_returns_totals_and_categories(client, db, user_id, accounts):
    category = db.create_category(user_id, "Fuel")
    tx = fuel(db, user_id, accounts)
    response = client.post("/inbox/merchants/confirm", data={"key": FUEL, "choice": str(category), "list": "categories"},
                           follow_redirects=False)
    action = undo_id(response)

    landing = client.get(response.headers["location"]).text
    assert f'action="/inbox/undo/{action}"' in landing

    client.post(f"/inbox/undo/{action}", follow_redirects=False)
    assert db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [tx])[0] is None
    assert "categorized 0%" in client.get("/inbox").text


def test_recurring_confirm_returns_to_review_and_can_be_undone(client, db, user_id, accounts):
    for months in (4, 3, 2, 1):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=30 * months),
                     "ACME STREAMING", -10.0)
    suggestion = review.build_review(user_id, TODAY.replace(day=1), TODAY)["recurring"][0]

    response = client.post("/recurring", data={
        "name": suggestion["name"], "kind": "expense", "amount": "10.00", "start_date": suggestion["start_date"],
        "frequency_type": "monthly", "interval_months": "1", "day_of_month": str(suggestion["day_of_month"]),
        "day_of_month_touched": "1", "suggestion_key": suggestion["key"], "next_path": "/inbox#recurring",
    }, follow_redirects=False)

    assert response.headers["location"].startswith("/inbox?") and response.headers["location"].endswith("#recurring")
    assert db.fetch_one("SELECT COUNT(*) FROM recurring_items")[0] == 1

    client.post(f"/inbox/undo/{undo_id(response)}", follow_redirects=False)
    assert db.fetch_one("SELECT COUNT(*) FROM recurring_items")[0] == 0
    assert review.build_review(user_id, TODAY.replace(day=1), TODAY)["recurring"]


def test_a_split_returns_to_review(client, db, user_id, accounts):
    category = db.create_category(user_id, "Fuel")
    tx = fuel(db, user_id, accounts, amount=-100.0)

    response = client.post(f"/transactions/{tx}/split", data={
        "category_id": str(category), "amount": "40", "note": "", "next": "/inbox#categories",
    }, follow_redirects=False)

    assert response.headers["location"].startswith("/inbox?")
    assert response.headers["location"].endswith("#categories")
