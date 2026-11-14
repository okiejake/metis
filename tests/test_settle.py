"""Settling one transaction where it is shown (#103, #105, #107): give it a
category, link it to a bill, make it a one-time item, or mark it unexpected.
Every choice can be undone, and a category or a bill link offers a rule.

All data here is synthetic.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import inbox
import settle
from conftest import add_bill, add_imported
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def card(accounts):
    return accounts["Example Card"]


@pytest.fixture
def checking(accounts):
    return accounts["Example Checking"]


@pytest.fixture
def home(db, user_id):
    return db.create_category(user_id, "Home")


def spend(db, user_id, account, day, description, amount=-40.0, category_id=None):
    tx = add_imported(db, user_id, account, "Example Card", date(2026, 6, day), description, amount)
    if category_id:
        db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [category_id, tx])
    return tx


def link_of(db, tx_id):
    return db.fetch_one("SELECT source_type, source_id, matched_via FROM expected_reconciliations "
                        "WHERE imported_transaction_id = ?", [tx_id])


# --- Category ------------------------------------------------------------------


def test_picking_a_category_assigns_it_by_hand_and_can_be_undone(client, db, user_id, card, home):
    tx = spend(db, user_id, card, 2, "ACME HARDWARE")

    response = client.post(f"/transactions/{tx}/category", data={"choice": str(home), "next": "/budget"},
                           follow_redirects=False)

    assert db.fetch_one("SELECT category_id, category_via FROM imported_transactions WHERE id = ?", [tx]) == (home, None)
    location = response.headers["location"]
    assert "undo=" in location and f"rule_tx={tx}" in location
    inbox.undo(user_id, int(location.split("undo=")[1].split("&")[0]))
    assert db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [tx])[0] is None


def test_typing_a_new_category_creates_and_assigns_it(client, db, user_id, card):
    tx = spend(db, user_id, card, 2, "ACME HARDWARE")

    client.post(f"/transactions/{tx}/category", data={"choice": "new", "new_category_name": "Garden", "next": "/budget"})

    garden = db.fetch_one("SELECT id FROM categories WHERE name = 'Garden'")[0]
    assert db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [tx])[0] == garden


def test_someone_elses_transaction_is_out_of_reach(client, db, user_id, card, home):
    db.execute("INSERT INTO users (slug, display_name) VALUES ('other', 'Other')")
    other = db.fetch_one("SELECT id FROM users WHERE slug = 'other'")[0]
    theirs = add_imported(db, other, None, "Theirs", date(2026, 6, 1), "THEIR SHOP", -10.0)

    client.post(f"/transactions/{theirs}/category", data={"choice": str(home), "next": "/budget"})

    assert db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [theirs])[0] is None


# --- Bill ----------------------------------------------------------------------


def test_matching_a_bill_links_it_takes_its_category_and_offers_an_auto_match_rule(client, db, user_id, checking, home):
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking, category_id=home)
    tx = spend(db, user_id, checking, 1, "ACH ACME PROPERTY TYPE: RENT", -1000.0)

    response = client.post(f"/transactions/{tx}/match", data={"bill_id": str(rent), "next": "/budget"},
                           follow_redirects=False)

    assert link_of(db, tx) == ("bill", rent, "confirm")
    assert db.load_imported_transaction_by_id(user_id, tx)["category_name"] == "Home"
    assert "rule_kind=bill" in response.headers["location"]


def test_matching_by_hand_clears_an_unexpected_mark(db, user_id, checking):
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking)
    tx = spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0)
    settle.mark_unexpected(user_id, tx)

    settle.match(user_id, tx, rent)

    assert db.fetch_one("SELECT unexpected FROM imported_transactions WHERE id = ?", [tx])[0] is False


def test_undoing_a_match_unlinks_it(db, user_id, checking):
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking)
    tx = spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0)

    inbox.undo(user_id, settle.match(user_id, tx, rent))

    assert link_of(db, tx) is None


def test_bills_to_match_are_the_same_kind_nearest_amount_first(db, user_id, checking):
    add_bill(db, user_id, "Big", 900.0, date(2026, 1, 1), checking)
    add_bill(db, user_id, "Close", 45.0, date(2026, 1, 1), checking)
    add_bill(db, user_id, "Paycheck", 40.0, date(2026, 1, 1), checking, kind="income")
    tx = spend(db, user_id, checking, 1, "ACME", -40.0)

    assert [b["name"] for b in settle.bills_to_match(user_id, db.load_imported_transaction_by_id(user_id, tx))] == [
        "Close", "Big"]


def test_an_unexpected_transaction_is_skipped_by_auto_match_rules(db, user_id, checking):
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking)
    tx = spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0)
    settle.mark_unexpected(user_id, tx)

    db.create_match_rule(user_id, "bill", rent, "ACME PROPERTY")

    assert link_of(db, tx) is None


# --- One-time and unexpected -----------------------------------------------------


def test_one_time_makes_a_matching_item_and_links_it(db, user_id, card, home):
    tx = spend(db, user_id, card, 12, "ACME LAMPS", -55.0, home)

    action = settle.make_one_time(user_id, tx)

    item = db.fetch_one("SELECT id, name, kind, amount, expected_date, category_id, account_id FROM one_time_items")
    assert item[1:] == ("ACME LAMPS", "expense", 55.0, date(2026, 6, 12), home, card)
    assert link_of(db, tx) == ("one_time", item[0], "confirm")
    inbox.undo(user_id, action)
    assert db.fetch_one("SELECT COUNT(*) FROM one_time_items")[0] == 0 and link_of(db, tx) is None


def test_unexpected_marks_it_and_undo_clears_the_mark(client, db, user_id, card, home):
    tx = spend(db, user_id, card, 3, "ACME LAMPS", -25.0, home)

    response = client.post(f"/transactions/{tx}/unexpected", data={"next": "/budget"}, follow_redirects=False)

    assert db.fetch_one("SELECT unexpected FROM imported_transactions WHERE id = ?", [tx])[0] is True
    inbox.undo(user_id, int(response.headers["location"].split("undo=")[1].split("&")[0]))
    assert db.fetch_one("SELECT unexpected FROM imported_transactions WHERE id = ?", [tx])[0] is False


def test_every_settle_route_returns_to_next_on_this_site_only(client, db, user_id, card, home):
    tx = spend(db, user_id, card, 3, "ACME LAMPS", -25.0, home)

    response = client.post(f"/transactions/{tx}/unexpected", data={"next": "https://example.com/x"},
                           follow_redirects=False)

    assert response.headers["location"].startswith("/dashboard")


# --- The rule prompt -------------------------------------------------------------


def test_the_category_rule_prompt_defaults(db, user_id, card, home):
    tx = spend(db, user_id, card, 3, "ACME HARDWARE 0042 SPRINGFIELD", -40.0, home)

    prompt = settle.rule_prompt(user_id, tx, "category", date(2026, 6, 1))

    assert prompt["pattern"] == "ACME HARDWARE"
    assert (prompt["amount_min"], prompt["amount_max"]) == (36.0, 44.0)
    assert (prompt["date_from"], prompt["date_to"]) == (date(2026, 6, 1), None)
    assert prompt["target_id"] == home


def test_the_bill_rule_prompt_takes_any_amount(db, user_id, checking):
    rent = add_bill(db, user_id, "Rent", 1000.0, date(2026, 1, 1), checking)
    tx = spend(db, user_id, checking, 1, "ACME PROPERTY", -1000.0)
    settle.match(user_id, tx, rent)

    prompt = settle.rule_prompt(user_id, tx, "bill", date(2026, 6, 1))

    assert prompt["target_id"] == rent and prompt["amount_min"] is None and prompt["date_from"] is None


def test_the_preview_counts_exactly_what_a_saved_rule_categorizes(client, db, user_id, card, home):
    for day, amount in ((2, -40.0), (3, -42.0), (4, -400.0), (5, -38.0)):
        spend(db, user_id, card, day, "ACME HARDWARE", amount)
    picked = spend(db, user_id, card, 6, "ACME HARDWARE", -40.0, home)
    fields = {"kind": "category", "tx_id": str(picked), "pattern": "ACME HARDWARE", "amount_min": "36",
              "amount_max": "44", "date_from": "2026-06-01", "date_to": ""}

    count = client.post("/rules/preview", data=fields).json()["count"]
    db.create_category_rule(user_id, home, "ACME HARDWARE", amount_min=36.0, amount_max=44.0,
                            date_from=date(2026, 6, 1))
    categorized = db.fetch_one("SELECT COUNT(*) FROM imported_transactions WHERE category_via = 'rule'")[0]

    assert count == categorized == 3


def test_saving_the_prompt_returns_to_next(client, db, user_id, card, home):
    tx = spend(db, user_id, card, 6, "ACME HARDWARE", -40.0, home)

    response = client.post(f"/categories/{home}/rules", data={"pattern": "ACME", "next": "/budget"},
                           follow_redirects=False)

    assert response.headers["location"].startswith("/budget")
    assert db.load_category_rules(user_id, category_id=home)[0]["pattern"] == "ACME"
    assert tx
