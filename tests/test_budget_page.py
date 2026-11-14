"""The budget page (#104, #102): one section, the chips without today, a status
filter over every category's bills, and the section a background action
replaces.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_bill, add_imported, add_one_time
from services import SESSION_COOKIE

TODAY = date.today()


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def home(db, user_id):
    return db.create_category(user_id, "Home")


def range_query():
    start = TODAY.replace(day=1)
    return f"start={start.isoformat()}&end={(start + timedelta(days=40)).replace(day=1) - timedelta(days=1)}"


def test_budget_is_one_section_named_budget(client, home):
    html = client.get("/budget").text

    assert re.findall(r"<h2>([^<]+)</h2>", html) == ["budget"]
    assert 'id="budget-section"' in html


def test_the_today_chip_is_gone_and_the_others_stay(client, db, user_id, accounts, home):
    add_bill(db, user_id, "Rent", 1000.0, TODAY.replace(day=1), accounts["Example Checking"], category_id=home)
    add_one_time(db, user_id, "Repair", 80.0, TODAY, accounts["Example Checking"], category_id=home)
    bar = re.search(r'<div class="page-bar">(.*?)\n</div>', client.get("/budget").text, re.S).group(1)

    assert "today" not in bar
    assert "1 bill" in bar and "1 one-time" in bar


def test_no_hand_set_budget_is_left(client, home):
    html = client.get("/budget").text

    assert 'name="budget_amount"' not in html and "prorated" not in html
    assert client.post(f"/categories/{home}/budget", data={"budget_amount": "10"}).status_code in (404, 405)


def test_every_category_offers_add_bill_add_one_time_and_edit(client, home):
    html = client.get("/budget").text

    assert f"/bills/new?category_id={home}" in html
    assert f"/one-time/new?category_id={home}" in html
    assert f"/categories/{home}/edit" in html


def test_the_status_filter_covers_every_categorys_bills(client, db, user_id, accounts, home):
    add_bill(db, user_id, "Rent", 1000.0, TODAY.replace(day=1), accounts["Example Checking"], category_id=home)
    paused = add_bill(db, user_id, "Storage", 50.0, TODAY.replace(day=1), accounts["Example Checking"])
    db.execute("UPDATE bills SET active = FALSE WHERE id = ?", [paused])

    html = client.get("/budget?status=paused").text

    assert re.search(r'data-status="active"[^>]*class="[^"]*hidden', html) or re.search(
        r'class="[^"]*hidden[^"]*"[^>]*data-status="active"', html)
    assert 'data-status-filter="paused"' in html


def test_the_old_panels_are_gone(client):
    html = client.get("/budget").text

    assert "<h2>bills</h2>" not in html and "<h2>one-time</h2>" not in html and "<h2>categories</h2>" not in html


def test_the_section_alone_is_what_a_background_action_replaces(client, db, user_id, accounts, home):
    for day in range(1, 13):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY.replace(day=1) + timedelta(days=day % 9),
                     f"ACME SHOP {day}", -5.0)

    section = client.get(f"/budget/section?{range_query()}&open=uncategorized&page=uncategorized:2").text

    assert not section.lstrip().startswith("<!doctype") and 'id="budget-section"' in section
    assert "2 / 2" in section


def test_a_settle_action_can_land_on_the_section(client, db, user_id, accounts, home):
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME HARDWARE", -40.0)

    response = client.post(f"/transactions/{tx}/category",
                           data={"choice": str(home), "next": f"/budget/section?{range_query()}&open=uncategorized"})

    assert response.status_code == 200
    assert 'id="budget-section"' in response.text and "<html" not in response.text
    assert 'data-rule-prompt' in response.text


def test_without_script_the_forms_reload_the_page(client, db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "ACME HARDWARE", -40.0)

    html = client.get(f"/budget?{range_query()}&open=uncategorized").text

    assert re.search(r'name="next" value="/budget\?[^"]*open=uncategorized', html)
    assert "/static/partial.js" in html
