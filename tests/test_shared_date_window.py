"""Dashboard, Ledger, Budget and Review share one saved date range: what is saved
is what each of them opens on. A link carrying ?start=&end= shows those dates on
that view only and never replaces the saved range.

All data here is synthetic.
"""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from date_range import RangeRule
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    """Signed in as the test user. A password has to exist, or every route
    redirects to first-run setup."""
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.mark.parametrize("read_on", ["/dashboard", "/ledger", "/budget", "/inbox"])
def test_every_page_opens_on_the_saved_range(client, db, user_id, read_on):
    db.save_range_rule(user_id, RangeRule("day", "absolute", "2026-02-01", "2026-03-31"))

    response = client.get(read_on)

    assert response.status_code == 200
    assert (response.context["start"], response.context["end"]) == ("2026-02-01", "2026-03-31")


def test_a_start_end_link_shows_that_range_without_saving_it(client, db, user_id):
    db.save_range_rule(user_id, RangeRule("month", "relative", -3, 3))

    response = client.get("/ledger?start=2026-02-01&end=2026-03-31")

    assert (response.context["start"], response.context["end"]) == ("2026-02-01", "2026-03-31")
    assert db.load_range_rule(user_id) == RangeRule("month", "relative", -3, 3)
