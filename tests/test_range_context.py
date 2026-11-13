"""Every signed-in page gets the saved range rule and the range in effect, for the
header picker, without each route passing them.

All data here is synthetic.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_imported
from date_range import DEFAULT_RULE
from services import SESSION_COOKIE

RANGE_KEYS = {"range_rule", "range_start", "range_end", "range_today", "range_first_year"}


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.mark.parametrize(
    "path", ["/dashboard", "/review", "/ledger", "/statements", "/budget", "/accounts", "/settings"]
)
def test_every_signed_in_page_gets_the_range(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert RANGE_KEYS <= response.context.keys()
    assert "show_forecast_window_form" not in response.context
    assert "window_form_action" not in response.context


def test_the_header_shows_a_link_override(client):
    context = client.get("/dashboard?start=2026-02-01&end=2026-03-31").context

    assert (context["range_start"], context["range_end"]) == ("2026-02-01", "2026-03-31")


@pytest.mark.parametrize(
    "raw", ["{not json", '{"grain":"week","mode":"relative","start":0,"end":0}', '"just a string"']
)
def test_a_corrupt_saved_range_falls_back_to_the_default(client, db, user_id, raw):
    db.set_setting_value(user_id, db.FORECAST_WINDOW_SETTINGS_KEY, raw)

    assert client.get("/dashboard").status_code == 200
    assert db.load_range_rule(user_id) == DEFAULT_RULE


def test_first_year_reaches_back_to_the_earliest_transaction(db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2009, 6, 1), "ACME GROCER", -10.0)

    assert db.range_first_year(user_id, date(2026, 11, 13)) == 2009


def test_first_year_defaults_to_ten_years_back(db, user_id):
    assert db.range_first_year(user_id, date(2026, 11, 13)) == 2016


@pytest.mark.parametrize("path", ["/dashboard", "/statements", "/accounts", "/settings"])
def test_the_picker_renders_on_every_page(client, path):
    html = client.get(path).text

    assert 'id="range-button"' in html
    assert 'id="range-dialog"' in html
    assert "data-window-preset" not in html
    assert "calendar-nav" not in html


def test_the_header_button_shows_the_range_in_words(client, db, user_id):
    db.save_forecast_window(user_id, date(2026, 1, 1), date(2026, 12, 31))

    assert "Jan 1, 2026 – Dec 31, 2026" in client.get("/statements").text


def test_the_popup_carries_the_server_date(client):
    assert f'data-today="{date.today().isoformat()}"' in client.get("/dashboard").text
