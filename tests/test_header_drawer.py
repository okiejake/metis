"""The header is a menu button, metis, the date range and hide amounts. The menu
button opens a side panel: the user block first, then the pages with inbox at
the top. Review is called inbox and lives at /inbox.

All data here is synthetic.
"""

import re

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_imported, quiet_imports
from services import SESSION_COOKIE

PAGES = ["/inbox", "/dashboard", "/ledger", "/statements", "/budget", "/accounts"]


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def header(html):
    return re.search(r'<header class="site-header">(.*?)</header>', html, re.S).group(1)


def drawer(html):
    return re.search(r'<dialog id="nav-drawer"[^>]*>(.*?)</dialog>', html, re.S).group(1)


def test_the_header_holds_the_menu_button_before_metis_and_no_page_links(client):
    top = header(client.get("/dashboard").text)

    assert re.search(r'id="nav-toggle"[^>]*aria-label="open menu"', top)
    assert top.index('id="nav-toggle"') < top.index("<h1>")
    assert "user-menu-trigger" not in top
    assert 'class="site-nav"' not in top
    assert not any(f'href="{page}"' in top for page in PAGES if page != "/dashboard")  # metis links home


def test_the_drawer_opens_on_close_then_pages_then_settings_sign_out_and_who(client):
    panel = drawer(client.get("/dashboard").text)
    links = re.findall(r'<a href="(/[a-z]+)"[^>]*>', re.search(r'<nav class="site-nav".*?</nav>', panel, re.S).group(0))
    footer = re.search(r'<div class="site-nav nav-drawer-footer">(.*?)\n      </div>', panel, re.S)

    assert links == PAGES
    assert panel.index('id="nav-close"') < panel.index('<nav class="site-nav"') < panel.index("nav-drawer-footer")
    assert footer and "/settings" in footer.group(1) and "/logout" in footer.group(1)
    assert panel.index("/logout") < panel.index('class="nav-drawer-who"')
    assert re.search(r'<p class="nav-drawer-who">\s*Test User - test@example.com\s*</p>', panel)
    assert "user-avatar" not in panel
    assert "theme" not in panel


def test_metis_links_to_the_dashboard(client):
    top = header(client.get("/ledger").text)

    assert re.search(r'<h1><a href="/dashboard"[^>]*>metis</a></h1>', top)


def test_the_theme_toggle_sits_right_of_hide_amounts(client):
    top = header(client.get("/dashboard").text)

    assert re.search(r'id="theme-toggle"[^>]*aria-label="theme: system"', top)
    assert top.index('id="amount-toggle-button"') < top.index('id="theme-toggle"')
    assert 'data-lucide="sun-moon"' in top


def test_the_current_page_is_marked_in_the_drawer(client):
    panel = drawer(client.get("/ledger").text)

    assert re.search(r'<a href="/ledger" class="active"', panel)


def test_inbox_shows_its_waiting_count(client, db, user_id, accounts):
    quiet_imports(user_id)
    db.set_setting_value(user_id, "starting_balance", "1000")  # no negative-balance alert
    add_imported(db, user_id, accounts["Example Card"], "Example Card", db.date.today(), "EXAMPLE FUEL", -50.0)

    panel = drawer(client.get("/budget").text)

    assert re.search(r'<a href="/inbox"[^>]*>\s*inbox\s*<span class="nav-badge">1</span>', panel)


def test_review_moved_to_inbox(client):
    assert client.get("/inbox").status_code == 200
    moved = client.get("/review?x=1", follow_redirects=False)
    assert moved.status_code in (301, 307, 308)
    assert moved.headers["location"].endswith("/inbox?x=1")


@pytest.mark.parametrize("path", ["/dashboard", "/ledger", "/statements", "/budget", "/accounts", "/settings", "/accounts/new"])
def test_no_page_has_a_page_intro(client, path):
    assert 'class="page-intro"' not in client.get(path).text
