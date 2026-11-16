"""A split line can carry its own bill (#123). The bill counts that line: its
due date is settled in the forecast, its budget actual holds the line's amount,
and the reconcile page lists the line among its linked actuals.

All data here is synthetic.
"""

from datetime import date, timedelta

import pytest

from conftest import add_bill, add_imported, reconcile


def checking(db, user_id, accounts, when, description, amount):
    return add_imported(db, user_id, accounts["Example Checking"], "Example Checking", when, description, amount)


def columns(db, table):
    return [row[1] for row in db.fetch_all(f"PRAGMA table_info('{table}')")]


# --- The column and the upgrade ------------------------------------------------


def test_a_new_database_gives_split_lines_a_bill(db):
    assert "bill_id" in columns(db, "transaction_splits")


def test_the_upgrade_keeps_every_split_line_and_gives_none_a_bill(v040):
    query = "SELECT id, imported_transaction_id, category_id, amount, note FROM transaction_splits ORDER BY id"
    before = v040.fetch_all(query)
    assert before
    v040.init_db()
    assert v040.fetch_all(query) == before
    assert v040.fetch_all("SELECT DISTINCT bill_id FROM transaction_splits") == [(None,)]


# --- Saving ------------------------------------------------------------------------


def test_a_split_line_is_saved_with_its_bill(db, user_id, accounts):
    home = db.create_category(user_id, "home")
    phone = add_bill(db, user_id, "Example Phone", 40.00, date(2026, 7, 10), account_id=accounts["Example Checking"])
    tx = checking(db, user_id, accounts, date(2026, 7, 2), "ACME STORE 12", -100.00)

    db.save_transaction_splits(user_id, tx, [{"category_id": home, "amount": 40.00, "note": "", "bill_id": phone}])

    assert db.load_transaction_splits(user_id, tx)[0]["bill_id"] == phone


def test_a_split_line_cannot_take_another_users_bill(db, user_id, accounts):
    home = db.create_category(user_id, "home")
    tx = checking(db, user_id, accounts, date(2026, 7, 2), "ACME STORE 12", -100.00)
    with pytest.raises(ValueError):
        db.save_transaction_splits(user_id, tx, [{"category_id": home, "amount": 40.00, "note": "", "bill_id": 999999}])


# --- What the bill counts -------------------------------------------------------------


@pytest.fixture
def paid_on_a_split_line(db, user_id, accounts):
    """A $100 store run on the latest import date, $40 of it the phone bill, which
    falls due three days later."""
    today = date.today()
    home = db.create_category(user_id, "home")
    phone = add_bill(db, user_id, "Example Phone", 40.00, today + timedelta(days=3),
                     account_id=accounts["Example Checking"])
    tx = checking(db, user_id, accounts, today, "ACME STORE 12", -100.00)
    db.save_transaction_splits(user_id, tx, [{"category_id": home, "amount": 40.00, "note": "", "bill_id": phone}])
    return {"phone": phone, "tx": tx, "today": today}


def test_a_bill_paid_on_a_split_line_leaves_the_forecast(db, user_id, accounts, paid_on_a_split_line):
    today = paid_on_a_split_line["today"]
    rows = db.collect_account_transactions(user_id, db.load_account_by_id(user_id, accounts["Example Checking"]),
                                           today - timedelta(days=5), today + timedelta(days=20))
    forecast = [row for row in rows if row["type"] == "bill" and row["id"] == paid_on_a_split_line["phone"]]
    assert forecast == []


def test_a_bills_budget_actual_counts_its_split_line(db, user_id, accounts, paid_on_a_split_line):
    today = paid_on_a_split_line["today"]
    summary = db.build_budget_summary(user_id, today.replace(day=1), today + timedelta(days=20))
    bills = [bill for row in summary["categories"] for bill in row["bills"]]
    phone = [bill for bill in bills if bill["id"] == paid_on_a_split_line["phone"]][0]
    assert phone["actual"] == 40.00


def test_the_whole_transactions_bill_counts_only_what_is_left_after_its_lines(db, user_id, accounts):
    today = date.today()
    home = db.create_category(user_id, "home")
    store = add_bill(db, user_id, "Example Store Card", 60.00, today, account_id=accounts["Example Checking"])
    phone = add_bill(db, user_id, "Example Phone", 40.00, today, account_id=accounts["Example Checking"])
    tx = checking(db, user_id, accounts, today, "ACME STORE 12", -100.00)
    reconcile(db, user_id, "bill", store, tx)
    db.save_transaction_splits(user_id, tx, [{"category_id": home, "amount": 40.00, "note": "", "bill_id": phone}])

    summary = db.build_budget_summary(user_id, today.replace(day=1), today + timedelta(days=20))
    actuals = {bill["id"]: bill["actual"] for row in summary["categories"] for bill in row["bills"]}
    assert (actuals[store], actuals[phone]) == (60.00, 40.00)


def test_the_reconcile_list_shows_a_split_line_at_its_own_amount(db, user_id, accounts, paid_on_a_split_line):
    linked = db.load_reconciliations_for_item(user_id, "bill", paid_on_a_split_line["phone"])
    assert [(row["imported_transaction_id"], row["amount"], row["matched_via"]) for row in linked] == [
        (paid_on_a_split_line["tx"], -40.00, "split")]


def test_a_ledger_row_names_each_split_lines_bill(db, user_id, accounts, paid_on_a_split_line):
    today = paid_on_a_split_line["today"]
    rows = db.collect_account_transactions(user_id, db.load_account_by_id(user_id, accounts["Example Checking"]),
                                           today - timedelta(days=5), today + timedelta(days=20))
    row = [r for r in rows if r.get("id") == paid_on_a_split_line["tx"] and r["type"] == "actual"][0]
    assert [part["bill"] for part in row["splits"]] == ["Example Phone"]


def test_the_reconcile_page_lists_a_split_line_without_an_unlink(db, user_id, accounts, paid_on_a_split_line):
    from fastapi.testclient import TestClient

    import app as app_module

    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as client:
        client.cookies.set(db.SESSION_COOKIE, db.create_session(user_id))
        html = client.get(f"/bills/{paid_on_a_split_line['phone']}/reconcile").text
    assert "ACME STORE 12" in html
    assert "/expected/reconcile/-" not in html
