"""Budget as one section (#104): a category's budget is exactly its expected
transactions, every category is listed, parents roll up their children, and
each category lists what it has not settled (#103, #105).

All data here is synthetic.
"""

from datetime import date

import pytest

from conftest import add_bill, add_imported, add_one_time, reconcile

START, END = date(2026, 6, 1), date(2026, 6, 30)


@pytest.fixture
def checking(accounts):
    return accounts["Example Checking"]


@pytest.fixture
def card(accounts):
    return accounts["Example Card"]


def spend(db, user_id, account, day, description, amount, category_id=None, name=None):
    tx = add_imported(db, user_id, account, name or ("Example Card" if account else ""), date(2026, 6, day),
                      description, amount, "income" if amount > 0 else "expense")
    if category_id:
        db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [category_id, tx])
    return tx


def row(summary, name):
    return next(r for r in summary["categories"] if r["name"] == name)


def test_a_categorys_budget_is_exactly_its_expected_transactions(db, user_id, checking):
    home = db.create_category(user_id, "Home")
    add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking, category_id=home)
    add_one_time(db, user_id, "Repair", 80.0, date(2026, 6, 20), checking, category_id=home)
    add_one_time(db, user_id, "Later repair", 50.0, date(2026, 7, 2), checking, category_id=home)

    assert row(db.build_budget_summary(user_id, START, END), "Home")["budgeted"] == 1080.0


def test_nothing_is_budgeted_by_hand(db):
    assert "budget_amount" not in [r[1] for r in db.fetch_all("PRAGMA table_info('categories')")]
    assert not hasattr(db, "set_category_budget")


def test_every_category_is_listed_even_an_empty_one(db, user_id):
    db.create_category(user_id, "Gifts")

    summary = db.build_budget_summary(user_id, START, END)

    assert [r["name"] for r in summary["rows"]] == ["Gifts"]
    assert summary["income"]["key"] == "income" and summary["uncategorized"]["key"] == "uncategorized"


def test_a_parent_rolls_up_its_children_and_the_totals_add_up(db, user_id, checking, card):
    food = db.create_category(user_id, "Food")
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    add_bill(db, user_id, "Market box", 100.0, date(2026, 1, 5), checking, category_id=groceries)
    add_bill(db, user_id, "Food club", 20.0, date(2026, 1, 9), checking, category_id=food)
    spend(db, user_id, card, 4, "ACME GROCER", -90.0, groceries)
    spend(db, user_id, card, 6, "ACME DINER", -15.0, food)
    spend(db, user_id, card, 7, "EXAMPLE FUEL", -10.0)

    summary = db.build_budget_summary(user_id, START, END)
    parent = summary["rows"][0]

    assert parent["name"] == "Food" and [c["name"] for c in parent["children"]] == ["Food › Groceries"]
    assert (parent["budgeted"], parent["actual"]) == (120.0, 105.0)
    assert (parent["children"][0]["budgeted"], parent["children"][0]["actual"]) == (100.0, 90.0)
    assert summary["totals"]["budgeted"] == round(sum(r["budgeted"] for r in summary["rows"])
                                                  + summary["uncategorized"]["budgeted"], 2)
    assert summary["totals"]["actual"] == 115.0


def test_income_is_its_own_row_with_scheduled_and_one_time_income(db, user_id, checking):
    add_bill(db, user_id, "Paycheck", 2000.0, date(2026, 1, 3), checking, kind="income")
    add_one_time(db, user_id, "Bonus", 500.0, date(2026, 6, 15), checking, kind="income")
    spend(db, user_id, checking, 3, "EXAMPLE PAYROLL", 2000.0, name="Example Checking")

    income = db.build_budget_summary(user_id, START, END)["income"]

    assert (income["budgeted"], income["actual"]) == (2500.0, 2000.0)
    assert [b["name"] for b in income["bills"]] == ["Paycheck"]
    assert [o["name"] for o in income["one_time"]] == ["Bonus"]


def test_a_category_holds_its_bills_and_its_one_time_items_in_the_range(db, user_id, checking):
    home = db.create_category(user_id, "Home")
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking, category_id=home)
    add_one_time(db, user_id, "Repair", 80.0, date(2026, 6, 20), checking, category_id=home)
    add_one_time(db, user_id, "Old repair", 80.0, date(2026, 3, 20), checking, category_id=home)
    reconcile(db, user_id, "bill", rent, spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0, name="Example Checking"))

    home_row = row(db.build_budget_summary(user_id, START, END), "Home")

    assert [(b["name"], b["budgeted"], b["actual"], b["status"]) for b in home_row["bills"]] == [
        ("Rent", 1000.0, 1000.0, "active")]
    assert [o["name"] for o in home_row["one_time"]] == ["Repair"]
    assert home_row["bills"][0]["next_due"] == date(2026, 7, 1) or home_row["bills"][0]["next_due"] >= date.today()


def test_a_paused_bill_is_listed_but_budgets_nothing(db, user_id, checking):
    home = db.create_category(user_id, "Home")
    paused = add_bill(db, user_id, "Storage", 50.0, date(2026, 1, 1), checking, category_id=home)
    db.execute("UPDATE bills SET active = FALSE WHERE id = ?", [paused])

    home_row = row(db.build_budget_summary(user_id, START, END), "Home")

    assert home_row["budgeted"] == 0.0 and [b["status"] for b in home_row["bills"]] == ["paused"]


def test_expected_transactions_without_a_category_sit_under_uncategorized(db, user_id, checking):
    add_bill(db, user_id, "Mystery fee", 10.0, date(2026, 1, 2), checking)

    uncategorized = db.build_budget_summary(user_id, START, END)["uncategorized"]

    assert uncategorized["budgeted"] == 10.0 and [b["name"] for b in uncategorized["bills"]] == ["Mystery fee"]


# --- What a category has not settled -----------------------------------------


def test_unbudgeted_is_categorized_spending_with_no_link_and_no_mark(db, user_id, checking, card):
    home = db.create_category(user_id, "Home")
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking, category_id=home)
    loose = spend(db, user_id, card, 2, "ACME HARDWARE", -40.0, home)
    linked = spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0, home, name="Example Checking")
    reconcile(db, user_id, "bill", rent, linked)
    marked = spend(db, user_id, card, 3, "ACME LAMPS", -25.0, home)
    db.execute("UPDATE imported_transactions SET unexpected = TRUE WHERE id = ?", [marked])
    spend(db, user_id, card, 4, "ACME HARDWARE", 15.0, home)  # a refund
    spend(db, user_id, card, 5, "ACME GROCER", -30.0)  # uncategorized

    lists = db.unsettled_transactions(user_id, START, END)

    assert [tx["id"] for tx in lists[home]] == [loose]
    assert lists[home][0]["amount"] == 40.0


def test_income_lists_unlinked_deposits_outside_cards(db, user_id, checking, card):
    deposit = spend(db, user_id, checking, 3, "EXAMPLE PAYROLL", 2000.0, name="Example Checking")
    spend(db, user_id, card, 4, "ACME HARDWARE", 15.0)  # a card refund is not income
    spend(db, user_id, checking, 5, "TRANSFER FROM SAVINGS", 300.0, name="Example Checking")
    db.execute("UPDATE imported_transactions SET is_transfer = TRUE WHERE description = 'TRANSFER FROM SAVINGS'")

    assert [tx["id"] for tx in db.unsettled_transactions(user_id, START, END)["income"]] == [deposit]


def test_uncategorized_lists_spending_with_no_category(db, user_id, card):
    groceries = db.create_category(user_id, "Groceries")
    loose = spend(db, user_id, card, 5, "ACME GROCER", -30.0)
    spend(db, user_id, card, 6, "ACME GROCER", -20.0, groceries)

    assert [tx["id"] for tx in db.unsettled_transactions(user_id, START, END)["uncategorized"]] == [loose]


def test_lists_page_ten_at_a_time(db, user_id, card):
    for day in range(1, 24):
        spend(db, user_id, card, day, f"ACME SHOP {day}", -5.0)

    listed = db.unsettled_transactions(user_id, START, END)["uncategorized"]
    page = db.page_of(listed, 3)

    assert (page["page"], page["pages"], len(page["rows"])) == (3, 3, 3)
    assert db.page_of(listed, 99)["page"] == 3 and db.page_of(listed, 0)["page"] == 1
