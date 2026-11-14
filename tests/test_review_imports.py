"""Review flags an account that looks stale: the days since its last imported
transaction are more than 20% above the average gap between its transactions.
An account needs five transaction days for a baseline; one with nothing
imported is listed as having no imports yet. Dismiss lasts until the next import.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import review
from conftest import add_imported
from services import SESSION_COOKIE

TODAY = date(2026, 8, 1)


def every_five_days(db, user_id, account_id, last, days=5):
    for n in range(days):
        add_imported(db, user_id, account_id, "Example Checking", last - timedelta(days=5 * n), f"ACME {n}", -1.0)


def flagged(user_id, today=TODAY):
    return {row["account_id"]: row for row in review.stale_accounts(user_id, today)}


def test_an_account_past_its_usual_gap_by_more_than_a_fifth_is_flagged(db, user_id, accounts):
    checking = accounts["Example Checking"]
    every_five_days(db, user_id, checking, TODAY - timedelta(days=7))

    row = flagged(user_id)[checking]

    assert (row["days_since"], row["usual_gap"], row["last_date"]) == (7, 5, TODAY - timedelta(days=7))


def test_an_account_within_a_fifth_of_its_usual_gap_is_not(db, user_id, accounts):
    checking = accounts["Example Checking"]
    every_five_days(db, user_id, checking, TODAY - timedelta(days=6))

    assert checking not in flagged(user_id), "6 days is exactly 20% over a 5-day gap"


def test_an_account_needs_five_transaction_days_for_a_baseline(db, user_id, accounts):
    checking = accounts["Example Checking"]
    every_five_days(db, user_id, checking, TODAY - timedelta(days=60), days=4)

    assert checking not in flagged(user_id)


def test_same_day_transactions_count_as_one_day(db, user_id, accounts):
    checking = accounts["Example Checking"]
    every_five_days(db, user_id, checking, TODAY - timedelta(days=7))
    for n in range(10):
        add_imported(db, user_id, checking, "Example Checking", TODAY - timedelta(days=7), f"ACME EXTRA {n}", -1.0)

    assert flagged(user_id)[checking]["usual_gap"] == 5


def test_an_account_with_nothing_imported_is_listed(db, user_id, accounts):
    row = flagged(user_id)[accounts["Example Card"]]

    assert row["last_date"] is None


def test_dismiss_lasts_until_the_next_import(db, user_id, accounts):
    checking = accounts["Example Checking"]
    every_five_days(db, user_id, checking, TODAY - timedelta(days=30))
    review.dismiss(user_id, flagged(user_id)[checking]["key"])
    assert checking not in flagged(user_id)

    add_imported(db, user_id, checking, "Example Checking", TODAY - timedelta(days=20), "ACME LATER", -1.0)

    assert checking in flagged(user_id)


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def test_the_imports_list_comes_first_with_an_import_button(client, db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date.today(), "ACME GROCER", -5.0)

    page = client.get("/inbox").text

    assert page.index('id="imports"') < page.index('id="categories"')
    assert re.search(rf'data-import-account="{accounts["Example Card"]}"', page)
    assert "no imports yet" in page
    assert page.count('id="import-dialog"') == 1


def test_dismissing_from_review_hides_the_account(client, db, user_id, accounts):
    key = flagged(user_id, date.today())[accounts["Example Card"]]["key"]

    response = client.post("/inbox/imports/dismiss", data={"key": key}, follow_redirects=False)

    assert response.headers["location"].endswith("#imports")
    assert accounts["Example Card"] not in flagged(user_id, date.today())


def test_stale_accounts_count_toward_the_review_badge(db, user_id, accounts):
    assert review.build_review(user_id, date(2026, 7, 1), date(2026, 7, 31))["waiting"] == 2
