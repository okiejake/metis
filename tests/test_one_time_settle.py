"""A one-time item settles itself (#101): when the actual that pays it is
imported, Metis links the two and the item stops counting in the forecast. It
links only when it is certain, and lists the item when it is not.

All data here is synthetic.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import import_templates as it
from conftest import add_imported, add_one_time
from services import SESSION_COOKIE

DUE = date(2026, 3, 14)


@pytest.fixture
def checking(accounts):
    return accounts["Example Checking"]


@pytest.fixture
def card(accounts):
    return accounts["Example Card"]


def actual(db, user_id, account_id, when, amount, description="ACME AUTO REPAIR", **extra):
    name = "Example Checking" if extra.pop("on_checking", True) else "Example Card"
    return add_imported(db, user_id, account_id, name, when, description, amount, **extra)


def links(db):
    return db.fetch_all(
        "SELECT imported_transaction_id, source_type, source_id, matched_via FROM expected_reconciliations ORDER BY id"
    )


def test_one_actual_for_the_same_amount_near_the_date_is_linked(db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    tx = actual(db, user_id, checking, DUE + timedelta(days=3), -400.0)

    result = db.settle_one_time_items(user_id)

    assert links(db) == [(tx, "one_time", item, "auto")]
    assert result == {"linked": 1, "uncertain": []}


def test_a_linked_item_stops_counting_in_the_forecast(db, user_id, checking):
    add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, checking, DUE - timedelta(days=2), -400.0)
    window = (DUE - timedelta(days=10), DUE + timedelta(days=10))
    before = [row for row in db.collect_blended_transactions(user_id, *window) if row["description"] == "Car repair"]

    db.settle_one_time_items(user_id)

    after = db.collect_blended_transactions(user_id, *window)
    assert len(before) == 1
    # The actual keeps the bank's text and names the item it settles; the forecast row is gone.
    assert [(row["type"], row["description"]) for row in after if row["bill"] == "Car repair"] == [
        ("actual", "ACME AUTO REPAIR")]
    assert [row for row in after if row["description"] == "Car repair"] == []
    assert sum(row["expense"] for row in after) == 400.0


def test_income_matches_a_deposit(db, user_id, checking):
    item = add_one_time(db, user_id, "Bonus", 1000.0, DUE, account_id=checking, kind="income")
    actual(db, user_id, checking, DUE, -1000.0, "ACME WITHDRAWAL")
    deposit = actual(db, user_id, checking, DUE, 1000.0, "EXAMPLE EMPLOYER BONUS")

    db.settle_one_time_items(user_id)

    assert links(db) == [(deposit, "one_time", item, "auto")]


@pytest.mark.parametrize(
    "why, when, amount, extra",
    [
        ("eleven days late", DUE + timedelta(days=11), -400.0, {}),
        ("eleven days early", DUE - timedelta(days=11), -400.0, {}),
        ("money in, not out", DUE, 400.0, {}),
        ("a transfer", DUE, -400.0, {"is_transfer": True}),
        ("more than 2% off", DUE, -409.0, {}),
    ],
)
def test_an_actual_that_does_not_fit_is_left_alone(db, user_id, checking, why, when, amount, extra):
    add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, checking, when, amount, **extra)

    result = db.settle_one_time_items(user_id)

    assert links(db) == [] and result == {"linked": 0, "uncertain": []}, why


def test_an_item_with_an_account_only_matches_that_account(db, user_id, checking, card):
    add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, card, DUE, -400.0, on_checking=False)

    assert db.settle_one_time_items(user_id) == {"linked": 0, "uncertain": []}


def test_an_item_with_no_account_matches_any_account(db, user_id, card):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE)
    tx = actual(db, user_id, card, DUE, -400.0, on_checking=False)

    db.settle_one_time_items(user_id)

    assert links(db) == [(tx, "one_time", item, "auto")]


def test_an_actual_already_linked_elsewhere_is_not_taken(db, user_id, checking):
    first = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    tx = actual(db, user_id, checking, DUE, -400.0)
    db.confirm_reconciliation(user_id, "one_time", first, tx)
    add_one_time(db, user_id, "Other repair", 400.0, DUE + timedelta(days=30), account_id=checking)

    db.settle_one_time_items(user_id)

    assert links(db) == [(tx, "one_time", first, "confirm")]


def test_two_exact_candidates_are_uncertain_and_nothing_is_linked(db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, checking, DUE, -400.0, "ACME AUTO REPAIR")
    actual(db, user_id, checking, DUE + timedelta(days=1), -400.0, "ACME TIRES")

    result = db.settle_one_time_items(user_id)

    assert links(db) == []
    assert result == {"linked": 0, "uncertain": [item]}
    assert [row["id"] for row in db.uncertain_one_time_matches(user_id)] == [item]


def test_a_near_amount_is_uncertain(db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, checking, DUE, -404.0)

    assert db.settle_one_time_items(user_id) == {"linked": 0, "uncertain": [item]}
    assert links(db) == []


def test_one_exact_candidate_wins_over_near_ones(db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    exact = actual(db, user_id, checking, DUE, -400.0, "ACME AUTO REPAIR")
    actual(db, user_id, checking, DUE, -402.0, "ACME TIRES")

    db.settle_one_time_items(user_id)

    assert links(db) == [(exact, "one_time", item, "auto")]


def test_an_actual_that_exactly_matches_two_items_is_uncertain_for_both(db, user_id, checking):
    first = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    second = add_one_time(db, user_id, "Tires", 400.0, DUE + timedelta(days=2), account_id=checking)
    actual(db, user_id, checking, DUE + timedelta(days=1), -400.0)

    result = db.settle_one_time_items(user_id)

    assert links(db) == []
    assert sorted(result["uncertain"]) == [first, second]


def test_a_settled_item_is_not_listed_as_uncertain(db, user_id, checking):
    add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    actual(db, user_id, checking, DUE, -400.0)
    db.settle_one_time_items(user_id)

    assert db.uncertain_one_time_matches(user_id) == []
    assert db.settle_one_time_items(user_id) == {"linked": 0, "uncertain": []}


def test_another_users_actual_is_never_a_candidate(db, user_id, checking):
    add_one_time(db, user_id, "Car repair", 400.0, DUE)
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
    add_imported(db, other, None, "Their Checking", DUE, "ACME AUTO REPAIR", -400.0)

    assert db.settle_one_time_items(user_id) == {"linked": 0, "uncertain": []}


# --- When it runs ---------------------------------------------------------------

TEMPLATE = {"name": "Example export", "date_field": "Date", "description_field": "Description",
            "amount_field": "Amount", "amount_layout": "signed", "amount_sign": "standard"}


def test_an_import_settles_the_item_it_pays(db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 400.0, DUE, account_id=checking)
    template = it.load_template(user_id, it.save_template(user_id, it.build_template(TEMPLATE)))
    account = db.load_account_by_id(user_id, checking)
    csv = f"Date,Description,Amount\n{DUE.isoformat()},ACME AUTO REPAIR,-400.00\n".encode()

    it.import_into_account(user_id, account, template, csv, "march.csv")

    assert [(row[1], row[2], row[3]) for row in links(db)] == [("one_time", item, "auto")]


@pytest.fixture
def client(db, user_id, accounts):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def test_adding_an_item_settles_it_against_what_is_already_imported(client, db, user_id, checking):
    tx = actual(db, user_id, checking, DUE, -400.0)

    client.post("/one-time", data={"name": "Car repair", "kind": "expense", "amount": "400.00", "repeats": "one-time",
                                   "first_due_date": DUE.isoformat(), "account_id": str(checking)})

    item = db.fetch_one("SELECT id FROM one_time_items")[0]
    assert links(db) == [(tx, "one_time", item, "auto")]


def test_editing_an_item_settles_it_too(client, db, user_id, checking):
    item = add_one_time(db, user_id, "Car repair", 350.0, DUE, account_id=checking)
    tx = actual(db, user_id, checking, DUE, -400.0)

    client.post(f"/one-time/{item}/edit", data={"name": "Car repair", "kind": "expense", "amount": "400.00",
                                                "repeats": "one-time", "first_due_date": DUE.isoformat(),
                                                "account_id": str(checking)})

    assert links(db) == [(tx, "one_time", item, "auto")]
