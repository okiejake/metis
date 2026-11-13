"""A category chosen in Review covers a merchant: every uncategorized charge from
it, past and future. It never overwrites a category picked by hand for one
charge, a category rule never takes its rows, and Undo reverses it completely,
including rows imported after the decision.

All data here is synthetic.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import review
from conftest import add_imported
from merchants import merchant_key
from services import SESSION_COOKIE


@pytest.fixture
def card(accounts):
    return accounts["Example Card"]


@pytest.fixture
def groceries(db, user_id):
    return db.create_category(user_id, "Groceries")


@pytest.fixture
def dining(db, user_id):
    return db.create_category(user_id, "Dining")


def charge(db, user_id, card, day, description, amount=-25.0):
    return add_imported(db, user_id, card, "Example Card", date(2026, 6, day), description, amount)


def category_of(db, tx_id):
    return db.fetch_one("SELECT category_id, category_via FROM imported_transactions WHERE id = ?", [tx_id])


ACME = merchant_key("ACME GROCER #1")


def test_create_category_refuses_a_blank_or_duplicate_name(db, user_id, groceries):
    with pytest.raises(ValueError):
        db.create_category(user_id, "  ")
    with pytest.raises(ValueError):
        db.create_category(user_id, "groceries")


def test_confirming_a_merchant_categorizes_exactly_its_uncategorized_charges(db, user_id, card, groceries, dining):
    acme = [charge(db, user_id, card, day, f"ACME GROCER #{day}") for day in (1, 2, 3)]
    hand_picked = charge(db, user_id, card, 4, "ACME GROCER #4")
    review.set_charge(user_id, hand_picked, dining)
    fuel = charge(db, user_id, card, 5, "EXAMPLE FUEL")

    review.confirm_category(user_id, ACME, groceries)

    assert [category_of(db, tx) for tx in acme] == [(groceries, "merchant")] * 3
    assert category_of(db, hand_picked) == (dining, None)
    assert category_of(db, fuel) == (None, None)


def test_a_later_import_from_that_merchant_arrives_categorized(db, user_id, card, groceries):
    charge(db, user_id, card, 1, "ACME GROCER #1")
    review.confirm_category(user_id, ACME, groceries)

    later = charge(db, user_id, card, 20, "ACME GROCER #9")
    review.apply_review_decisions(user_id)

    assert category_of(db, later) == (groceries, "merchant")


def test_undo_reverts_rows_imported_after_the_decision(db, user_id, card, groceries):
    first = charge(db, user_id, card, 1, "ACME GROCER #1")
    action = review.confirm_category(user_id, ACME, groceries)
    later = charge(db, user_id, card, 20, "ACME GROCER #9")
    review.apply_review_decisions(user_id)

    review.undo(user_id, action)
    next_import = charge(db, user_id, card, 25, "ACME GROCER #10")
    review.apply_review_decisions(user_id)

    assert [category_of(db, tx) for tx in (first, later, next_import)] == [(None, None)] * 3


def test_a_rule_resync_never_takes_a_row_the_decision_set(db, user_id, card, groceries):
    household = db.create_category(user_id, "Household")
    tx = charge(db, user_id, card, 1, "ACME GROCER #1")
    review.confirm_category(user_id, ACME, groceries)

    db.create_category_rule(user_id, household, "ACME")
    db.resync_category_rules(user_id)

    assert category_of(db, tx) == (groceries, "merchant")


def test_deleting_the_category_removes_its_decisions(db, user_id, card, groceries):
    charge(db, user_id, card, 1, "ACME GROCER #1")
    review.confirm_category(user_id, ACME, groceries)
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as client:
        client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        client.post(f"/categories/{groceries}/delete", follow_redirects=False)

    later = charge(db, user_id, card, 20, "ACME GROCER #9")
    review.apply_review_decisions(user_id)

    assert category_of(db, later) == (None, None)
    assert db.fetch_one("SELECT COUNT(*) FROM merchant_decisions")[0] == 0


def test_confirming_again_with_nothing_left_records_nothing(db, user_id, card, groceries):
    charge(db, user_id, card, 1, "ACME GROCER #1")
    review.confirm_category(user_id, ACME, groceries)
    actions = db.fetch_one("SELECT COUNT(*) FROM review_actions")[0]

    assert review.confirm_category(user_id, ACME, groceries) is None
    assert db.fetch_one("SELECT COUNT(*) FROM review_actions")[0] == actions


def test_set_charge_is_a_hand_pick_and_undo_restores_it(db, user_id, card, groceries, dining):
    tx = charge(db, user_id, card, 1, "ACME GROCER #1")
    action = review.set_charge(user_id, tx, dining)
    review.confirm_category(user_id, ACME, groceries)
    assert category_of(db, tx) == (dining, None)

    review.undo(user_id, action)

    assert category_of(db, tx) == (None, None)
