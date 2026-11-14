"""Metis can be served under a path such as `/metis_prod`, behind a proxy that
may or may not strip that path before passing a request on. Routes inside the
app keep their plain names. Everything the browser sees has to carry the path:
every link and form, every redirect, and the sign-in cookie. Two environments
on one hostname must not share a sign-in.

All data here is synthetic.
"""

import re
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE

BASE = "/metis_qa"
PASSWORD = "synthetic-password-for-tests"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
URL_ATTRIBUTE = re.compile(r'(?:href|action|src|formaction)="([^"]*)"')
PAGES = ["/dashboard", "/ledger", "/statements", "/budget", "/inbox", "/accounts", "/accounts/new", "/templates/new", "/settings"]


@pytest.fixture
def under_base(monkeypatch):
    monkeypatch.setenv("METIS_BASE_PATH", BASE)


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, PASSWORD)
    with TestClient(app_module.app) as test_client:
        yield test_client


def sign_in(client, db, user_id):
    client.cookies.set(SESSION_COOKIE, db.create_session(user_id))


def root_relative_urls(html):
    return [url for url in URL_ATTRIBUTE.findall(html) if url.startswith("/")]


def test_a_page_requested_with_the_base_path_is_served(under_base, client, db, user_id):
    sign_in(client, db, user_id)

    assert client.get(f"{BASE}/budget").status_code == 200


def test_a_page_the_proxy_already_stripped_is_served(under_base, client, db, user_id):
    sign_in(client, db, user_id)

    assert client.get("/budget").status_code == 200


@pytest.mark.parametrize("page", PAGES)
def test_every_link_and_form_on_a_page_carries_the_base_path(under_base, client, db, user_id, page):
    sign_in(client, db, user_id)

    urls = root_relative_urls(client.get(f"{BASE}{page}").text)

    assert urls, "the page should link somewhere"
    assert [url for url in urls if not url.startswith(f"{BASE}/")] == []


def test_the_sign_in_page_carries_the_base_path(under_base, client):
    urls = root_relative_urls(client.get(f"{BASE}/login").text)

    assert urls
    assert [url for url in urls if not url.startswith(f"{BASE}/")] == []


def test_a_redirect_carries_the_base_path(under_base, client, db, user_id):
    sign_in(client, db, user_id)

    response = client.get(f"{BASE}/", follow_redirects=False)

    assert response.headers["location"] == f"{BASE}/dashboard"


def test_signing_in_returns_to_the_page_asked_for_under_the_base_path(under_base, client):
    asked = client.get(f"{BASE}/budget", follow_redirects=False)
    assert asked.headers["location"] == f"{BASE}/login?next=%2Fbudget"

    signed_in = client.post(
        f"{BASE}/login",
        data={"identifier": "testuser", "password": PASSWORD, "next_path": "/budget"},
        follow_redirects=False,
    )

    assert signed_in.headers["location"] == f"{BASE}/budget"


def test_the_sign_in_cookie_belongs_to_one_environment(under_base):
    """A cookie scoped to `/metis_qa` is never sent to `/metis_prod`, so signing
    in to one environment cannot sign you out of the other."""
    response = Response()

    import services

    services.attach_session_cookie(response, "token")

    assert f"Path={BASE}" in response.headers["set-cookie"]


def test_signing_out_clears_the_cookie_it_set(under_base):
    import services

    response = Response()
    services.clear_session_cookie(response)

    assert f"Path={BASE}" in response.headers["set-cookie"]


def test_without_a_base_path_everything_stays_at_the_root(client):
    urls = root_relative_urls(client.get("/login").text)

    assert urls
    assert [url for url in urls if url.startswith("/metis")] == []


def test_no_template_writes_a_root_relative_url_by_hand():
    """A literal `href="/..."` would skip the base path. Every URL in a template
    is written after `{{ base_path() }}`, so this stays true for new pages."""
    offenders = []
    for template in sorted(TEMPLATES.glob("*.html")):
        for number, line in enumerate(template.read_text().splitlines(), start=1):
            if re.search(r'(?:href|action|src|formaction)="/', line):
                offenders.append(f"{template.name}:{number}")

    assert offenders == []
