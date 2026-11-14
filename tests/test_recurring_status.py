"""A recurring item's status comes from two fields. Pausing is a choice; an end
date in the past means the item is over. Budget shows Active only for items
that will still occur.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE, recurring_status
from conftest import add_recurring

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
    assert recurring_status({"active": active, "end_date": end_date}, TODAY) == expected


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def row_for(page, name):
    match = re.search(r"<tr[^>]*>(?:(?!</tr>).)*?" + re.escape(name) + r".*?</tr>", page, re.S)
    assert match, f"no row for {name}"
    return match.group(0)


def test_an_ended_item_shows_as_completed_without_pause(client, db, user_id):
    item_id = add_recurring(db, user_id, "Acme Streaming", 10.0, 1, date.today() - timedelta(days=400))
    db.execute(
        "UPDATE recurring_items SET end_date = ? WHERE id = ?",
        [date.today() - timedelta(days=10), item_id],
    )

    row = row_for(client.get("/budget").text, "Acme Streaming")

    assert 'data-status="completed"' in row
    assert re.search(r'class="table-pill[^"]*">completed</span>', row)
    assert not re.search(r">\s*(pause|resume)\s*<", row)


def test_the_recurring_count_counts_only_active_items(client, db, user_id):
    add_recurring(db, user_id, "Acme Gym", 30.0, 1, date.today() - timedelta(days=60))
    ended = add_recurring(db, user_id, "Acme Streaming", 10.0, 1, date.today() - timedelta(days=400))
    db.execute(
        "UPDATE recurring_items SET end_date = ? WHERE id = ?",
        [date.today() - timedelta(days=10), ended],
    )

    assert "1 recurring" in client.get("/budget").text
