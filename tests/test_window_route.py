"""POST /window saves the date range from the header picker and returns to the
page it came from, dropping any one-off dates from a link.

All data here is synthetic.
"""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from date_range import RangeRule
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def post(client, **data):
    return client.post("/window", data=data, follow_redirects=False)


def test_apply_saves_the_rule_and_returns(client, db, user_id):
    response = post(client, grain="year", mode="relative", start="-1", end="0", next="/budget")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/budget")
    assert db.load_range_rule(user_id) == RangeRule("year", "relative", -1, 0)


def test_apply_from_an_override_link_lands_on_the_new_range(client):
    response = post(
        client,
        grain="month",
        mode="relative",
        start="0",
        end="0",
        next="/ledger?start=2026-02-01&end=2026-03-31&msg=x&err=1&tab=a",
    )

    assert response.headers["location"].endswith("/ledger?tab=a")


def test_off_site_next_goes_to_the_dashboard(client):
    response = post(client, grain="month", mode="relative", start="0", end="0", next="//evil.example")

    assert response.headers["location"].endswith("/dashboard")


@pytest.mark.parametrize(
    "data",
    [
        {"grain": "week", "mode": "relative", "start": "0", "end": "0"},
        {"grain": "month", "mode": "relative", "start": "3", "end": "1"},
        {"grain": "day", "mode": "absolute", "start": "1900-01-01", "end": "2100-12-31"},
    ],
)
def test_bad_input_leaves_the_rule_and_shows_an_error(client, db, user_id, data):
    before = db.load_range_rule(user_id)

    response = post(client, **data, next="/budget")

    assert "err=1" in response.headers["location"]
    assert db.load_range_rule(user_id) == before
