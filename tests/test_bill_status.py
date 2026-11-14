"""A bill's status comes from two fields. Pausing is a choice; an end
date in the past means the bill is over. Budget shows Active only for bills
that will still occur.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE, bill_status
from conftest import add_bill

TODAY = date(2026, 6, 15)


@pytest.mark.parametrize(
    "active, end_date, expected",
    [
        (True, None, "active"),
        (True, TODAY, "active"),
        (True, TODAY + timedelta(days=1), "active"),
        (True, TODAY - timedelta(days=1), "completed"),
        (False, None, "paused"),
        (False, TODAY + timedelta(days=30), "paused"),
        (False, TODAY - timedelta(days=1), "completed"),
    ],
)
def test_status_comes_from_the_pause_flag_and_the_end_date(active, end_date, expected):
    assert bill_status({"active": active, "end_date": end_date}, TODAY) == expected


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def row_for(page, name):
    match = re.search(r"<li class=\"budget-item[^>]*>(?:(?!</li>).)*?" + re.escape(name) + r".*?</li>", page, re.S)
    assert match, f"no row for {name}"
    return match.group(0)


def test_an_ended_item_shows_as_completed_without_pause(client, db, user_id):
    item_id = add_bill(db, user_id, "Acme Streaming", 10.0, date.today() - timedelta(days=400))
    db.execute(
        "UPDATE bills SET end_date = ? WHERE id = ?",
        [date.today() - timedelta(days=10), item_id],
    )

    row = row_for(client.get("/budget").text, "Acme Streaming")

    assert 'data-status="completed"' in row
    assert re.search(r'class="table-pill[^"]*">completed</span>', row)
    assert not re.search(r">\s*(pause|resume)\s*<", row)


def test_the_bill_count_counts_only_active_bills(client, db, user_id):
    add_bill(db, user_id, "Acme Gym", 30.0, date.today() - timedelta(days=60))
    ended = add_bill(db, user_id, "Acme Streaming", 10.0, date.today() - timedelta(days=400))
    db.execute(
        "UPDATE bills SET end_date = ? WHERE id = ?",
        [date.today() - timedelta(days=10), ended],
    )

    assert re.search(r'stat-chip">1 bill</span>', client.get("/budget").text)
