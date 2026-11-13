"""A card's statement and due days can each be "last day of month", stored as
0. That is a set day, so the Accounts list must show the card's cycle rather
than asking for days that are already saved.

All data here is synthetic.
"""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import LAST_DAY_SENTINEL, SESSION_COOKIE

UNSET_PROMPT = "set statement &amp; due days"


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def set_cycle(db, account_id, statement_day, due_day):
    db.execute(
        "UPDATE accounts SET statement_day = ?, due_day = ? WHERE id = ?",
        [statement_day, due_day, account_id],
    )


@pytest.mark.parametrize(
    "statement_day, due_day",
    [
        (LAST_DAY_SENTINEL, 25),
        (5, LAST_DAY_SENTINEL),
        (LAST_DAY_SENTINEL, LAST_DAY_SENTINEL),
    ],
)
def test_a_last_day_of_month_cycle_is_shown(client, db, accounts, statement_day, due_day):
    set_cycle(db, accounts["Example Card"], statement_day, due_day)

    page = client.get("/accounts").text

    assert UNSET_PROMPT not in page
    assert "closes " in page


def test_a_card_without_days_still_asks_for_them(client, db, accounts):
    set_cycle(db, accounts["Example Card"], None, None)

    assert UNSET_PROMPT in client.get("/accounts").text
