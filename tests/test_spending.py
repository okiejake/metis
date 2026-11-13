"""One definition of spending, read by Budget and Review: money going out that
is not a transfer, less card refunds. Money arriving in any other account is
income and never reduces spending. Split parts count in their own categories.

All data here is synthetic.
"""

from datetime import date

import pytest

from conftest import add_imported

START, END = date(2026, 6, 1), date(2026, 6, 30)


@pytest.fixture
def category(db, user_id):
    def make(name):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO categories (name, user_id) VALUES (?, ?)", [name, user_id])
            return int(conn.execute(
                "SELECT id FROM categories WHERE user_id = ? AND name = ?", [user_id, name]).fetchone()[0])
    return make


def categorize(db, tx_id, category_id):
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [category_id, tx_id])


def actual(summary, name):
    rows = [c for c in summary["categories"] if c["name"] == name]
    return rows[0]["actual"] if rows else 0.0


def test_an_uncategorized_deposit_does_not_hide_uncategorized_spending(db, user_id, accounts):
    checking = accounts["Example Checking"]
    add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 3), "ACME GROCER", -100.0)
    add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 4), "EXAMPLE PAYROLL", 500.0, flow="income")

    summary = db.build_budget_summary(user_id, START, END)

    assert actual(summary, "Uncategorized") == 100.0
    assert summary["income"]["actual"] == 500.0


def test_a_card_refund_reduces_its_category(db, user_id, accounts, category):
    card = accounts["Example Card"]
    household = category("Household")
    categorize(db, add_imported(db, user_id, card, "Example Card", date(2026, 6, 3), "ACME HARDWARE", -80.0), household)
    categorize(db, add_imported(db, user_id, card, "Example Card", date(2026, 6, 9), "ACME HARDWARE", 30.0, flow="refund"), household)

    summary = db.build_budget_summary(user_id, START, END)

    assert actual(summary, "Household") == 50.0
    assert summary["income"]["actual"] == 0.0


def test_transfers_are_neither_spending_nor_income(db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 3), "EXAMPLE CARD PMT", -200.0, is_transfer=True)
    add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 5), "PAYMENT THANK YOU", 200.0, is_transfer=True)

    assert db.spending_parts(user_id, START, END) == []
    assert db.income_total(user_id, START, END) == 0.0


def test_a_split_counts_each_part_in_its_category_and_the_rest_uncategorized(db, user_id, accounts, category):
    dining = category("Dining")
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 3), "ACME MARKET", -100.0)
    db.save_transaction_splits(user_id, tx, [{"category_id": dining, "amount": 40.0, "note": ""}])

    summary = db.build_budget_summary(user_id, START, END)

    assert actual(summary, "Dining") == 40.0
    assert actual(summary, "Uncategorized") == 60.0


def test_spending_parts_sum_to_budget_actuals(db, user_id, accounts, category):
    dining = category("Dining")
    checking, card = accounts["Example Checking"], accounts["Example Card"]
    add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 2), "ACME GROCER", -120.0)
    add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 2), "EXAMPLE PAYROLL", 900.0, flow="income")
    categorize(db, add_imported(db, user_id, card, "Example Card", date(2026, 6, 6), "ACME DINER", -45.0), dining)
    add_imported(db, user_id, card, "Example Card", date(2026, 6, 8), "ACME DINER", 5.0, flow="refund")

    parts = db.spending_parts(user_id, START, END)
    summary = db.build_budget_summary(user_id, START, END)

    assert round(sum(p["spent"] for p in parts), 2) == summary["totals"]["actual"] == 160.0
    assert {p["merchant"] for p in parts} == {"ACME GROCER", "ACME DINER"}
