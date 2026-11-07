"""Dashboard, Ledger and Budget share one saved date range: setting it on any of
them is what the others open on next.

All data here is synthetic.
"""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    """Signed in as the test user. A password has to exist, or every route
    redirects to first-run setup."""
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.mark.parametrize(
    "set_on, read_on",
    [
        ("/dashboard", "/budget"),
        ("/budget", "/dashboard"),
        ("/budget", "/ledger"),
    ],
)
def test_date_range_set_on_one_page_carries_to_the_next(client, set_on, read_on):
    client.get(f"{set_on}?start=2026-02-01&end=2026-03-31")

    response = client.get(read_on)

    assert response.status_code == 200
    assert (response.context["start"], response.context["end"]) == ("2026-02-01", "2026-03-31")
