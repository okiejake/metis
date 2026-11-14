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

TOP_LEVEL = ["/dashboard", "/ledger", "/statements", "/budget", "/accounts", "/settings"]


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.mark.parametrize("path", TOP_LEVEL)
def test_a_top_level_page_opens_on_its_badges_alone(client, path):
    html = client.get(path).text
    bar = re.search(r'<div class="page-bar">(.*?)\n</div>', html, re.S)

    assert 'class="page-intro"' not in html
    if bar:
        assert "<h2" not in bar.group(1) and "page-kicker" not in bar.group(1) and "<p" not in bar.group(1)


def test_the_sign_in_page_has_no_tagline(client):
    client.cookies.clear()
    html = client.get("/login").text

    assert "Financial Clarity OS" not in html
    assert "auth-lede" not in html
