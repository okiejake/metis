"""Saved date filters: a named copy of a range rule, exactly as set, with the
date it was created. Listed in the picker; saved and deleted without leaving it.

All data here is synthetic.
"""

from datetime import date

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


def other_user(db):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO users (slug, display_name, email) VALUES ('other', 'Other User', 'other@example.com')")
        return int(conn.execute("SELECT id FROM users WHERE slug = 'other'").fetchone()[0])


# ---- services ---------------------------------------------------------------


def test_saved_ranges_keep_the_rule_and_list_newest_first(db, user_id):
    first = db.save_saved_range(user_id, "Quarter", RangeRule("month", "relative", -1, 1))
    second = db.save_saved_range(user_id, "Tax year", RangeRule("year", "absolute", "2025", "2025"))

    listed = db.load_saved_ranges(user_id)

    assert [row["name"] for row in listed] == ["Tax year", "Quarter"]
    assert listed[1]["rule"] == {"grain": "month", "mode": "relative", "start": -1, "end": 1}
    assert listed[0]["rule"] == {"grain": "year", "mode": "absolute", "start": "2025", "end": "2025"}
    assert first["created"] == second["created"] == date.today().isoformat()


def test_delete_removes_only_your_own(db, user_id):
    mine = db.save_saved_range(user_id, "Mine", RangeRule("month", "relative", 0, 0))
    theirs_user = other_user(db)
    theirs = db.save_saved_range(theirs_user, "Theirs", RangeRule("month", "relative", 0, 0))

    assert db.delete_saved_range(user_id, theirs["id"]) is False
    assert db.delete_saved_range(user_id, mine["id"]) is True
    assert db.load_saved_ranges(user_id) == []
    assert [row["name"] for row in db.load_saved_ranges(theirs_user)] == ["Theirs"]


def test_an_unreadable_saved_rule_is_skipped(db, user_id):
    db.save_saved_range(user_id, "Good", RangeRule("month", "relative", 0, 0))
    db.execute("INSERT INTO saved_ranges (user_id, name, rule) VALUES (?, 'Broken', '{not json')", [user_id])

    assert [row["name"] for row in db.load_saved_ranges(user_id)] == ["Good"]


def test_deleting_a_user_deletes_their_saved_ranges(db, user_id):
    db.save_saved_range(user_id, "Quarter", RangeRule("month", "relative", -1, 1))

    db.delete_user_and_data(user_id)

    assert db.fetch_one("SELECT COUNT(*) FROM saved_ranges")[0] == 0


# ---- endpoints --------------------------------------------------------------


@pytest.mark.parametrize(
    "form, rule",
    [
        ({"grain": "month", "mode": "relative", "start": "-3", "end": "3"}, {"grain": "month", "mode": "relative", "start": -3, "end": 3}),
        ({"grain": "day", "mode": "absolute", "start": "2026-02-01", "end": "2026-03-31"}, {"grain": "day", "mode": "absolute", "start": "2026-02-01", "end": "2026-03-31"}),
    ],
)
def test_save_returns_the_filter_exactly_as_set(client, db, user_id, form, rule):
    response = client.post("/window/saved", data={"name": "  Planning  ", **form})

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["rule"], body["created"]) == ("Planning", rule, date.today().isoformat())
    assert db.load_saved_ranges(user_id)[0]["id"] == body["id"]


@pytest.mark.parametrize(
    "name, form, message",
    [
        ("   ", {"grain": "month", "mode": "relative", "start": "0", "end": "0"}, "name"),
        ("x" * 61, {"grain": "month", "mode": "relative", "start": "0", "end": "0"}, "60"),
        ("quarter", {"grain": "month", "mode": "relative", "start": "0", "end": "0"}, "already"),
        ("Bad", {"grain": "week", "mode": "relative", "start": "0", "end": "0"}, "grain"),
        ("Long", {"grain": "day", "mode": "absolute", "start": "1900-01-01", "end": "2100-12-31"}, "too long"),
    ],
)
def test_save_rejects_bad_input(client, db, user_id, name, form, message):
    db.save_saved_range(user_id, "Quarter", RangeRule("month", "relative", -1, 1))

    response = client.post("/window/saved", data={"name": name, **form})

    assert response.status_code == 400
    assert message in response.json()["error"]
    assert len(db.load_saved_ranges(user_id)) == 1


def test_a_duplicate_name_message_keeps_the_typed_case(client, db, user_id):
    db.save_saved_range(user_id, "Quarter", RangeRule("month", "relative", -1, 1))

    response = client.post("/window/saved", data={"name": "QUARTER", "grain": "month", "mode": "relative", "start": "0", "end": "0"})

    assert response.json()["error"] == 'a filter named "QUARTER" already exists'


def test_delete_endpoint(client, db, user_id):
    mine = db.save_saved_range(user_id, "Mine", RangeRule("month", "relative", 0, 0))
    theirs = db.save_saved_range(other_user(db), "Theirs", RangeRule("month", "relative", 0, 0))

    assert client.post(f"/window/saved/{theirs['id']}/delete").status_code == 404
    assert client.post(f"/window/saved/{mine['id']}/delete").status_code == 204
    assert db.load_saved_ranges(user_id) == []


# ---- page -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/dashboard", "/statements", "/settings"])
def test_every_page_carries_the_saved_filters(client, db, user_id, path):
    db.save_saved_range(user_id, "Quarter", RangeRule("month", "relative", -1, 1))

    response = client.get(path)

    assert [row["name"] for row in response.context["range_saved"]] == ["Quarter"]
    assert "data-saved='[{" in response.text
