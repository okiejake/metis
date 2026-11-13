"""Review is reached from the navigation, with a count of what is waiting, and
from one line on the dashboard. Recurring suggestions moved from Budget to
Review; Budget's chip points there.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_imported
from services import SESSION_COOKIE

TODAY = date.today()
NAV_REVIEW = re.compile(r'<a href="/review"[^>]*>\s*Review\s*(<span class="nav-badge">(\d+)</span>)?\s*</a>')


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    db.save_forecast_window(user_id, TODAY.replace(day=1), TODAY)
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def test_nav_shows_review_with_a_count_only_when_something_waits(client, db, user_id, accounts):
    quiet = NAV_REVIEW.search(client.get("/budget").text)
    assert quiet and quiet.group(1) is None

    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "EXAMPLE FUEL", -50.0)

    busy = NAV_REVIEW.search(client.get("/budget").text)
    assert busy and busy.group(2) == "1"


def test_dashboard_line_links_to_review_and_hides_when_nothing_waits(client, db, user_id, accounts):
    assert "Categorized" not in client.get("/dashboard").text

    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, "EXAMPLE FUEL", -50.0)
    page = client.get("/dashboard").text

    assert re.search(r'<a href="/review"[^>]*>\s*Categorized 0%\s*</a>', page)


def test_budget_no_longer_lists_suggestions_and_links_its_chip_to_review(client, db, user_id, accounts):
    for months in (4, 3, 2, 1):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=30 * months),
                     "ACME STREAMING", -10.0)

    page = client.get("/budget").text

    assert "Suggested Recurring" not in page
    assert re.search(r'<a [^>]*href="/review#recurring"[^>]*>\s*1 suggested\s*</a>', page)
