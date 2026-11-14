"""Pages open on a short title and their data, not on descriptive copy (#73).
Taglines, kickers that repeat the nav, and paragraphs describing a page or a
panel are gone; chips, back links and lines that carry data stay.

All data here is synthetic.
"""

import re

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE

TOP_LEVEL = {
    "/dashboard": "Dashboard",
    "/ledger": "Ledger",
    "/statements": "Statements",
    "/budget": "Budget",
    "/accounts": "Accounts",
    "/settings": "Settings",
}


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.mark.parametrize("path, title", TOP_LEVEL.items())
def test_a_page_intro_is_a_short_title_and_data_only(client, path, title):
    html = client.get(path).text
    intro = re.search(r'<section class="page-intro">(.*?)</section>', html, re.S).group(1)

    assert re.search(rf"<h2>\s*{title}\s*</h2>", intro)
    assert "page-kicker" not in intro
    assert "<p" not in intro


def test_the_sign_in_page_has_no_tagline(client):
    client.cookies.clear()
    html = client.get("/login").text

    assert "Financial Clarity OS" not in html
    assert "auth-lede" not in html
