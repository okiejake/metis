"""A projected negative checking balance is a red inbox alert, not a chip on
every page.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import review
from conftest import add_recurring, quiet_imports
from date_range import RangeRule
from services import SESSION_COOKIE

TODAY = date.today()


@pytest.fixture
def client(db, user_id, accounts):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    db.set_setting_value(user_id, "starting_balance", "100")
    db.save_range_rule(user_id, RangeRule("month", "relative", 0, 3))
    quiet_imports(user_id)
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def a_big_bill(db, user_id, accounts):
    """A monthly bill larger than the balance, starting next week."""
    starts = TODAY + timedelta(days=7)
    add_recurring(db, user_id, "Acme Rent", 500.0, starts.day, starts, account_id=accounts["Example Checking"])
    return starts


def test_a_projected_negative_day_is_a_red_inbox_alert(client, db, user_id, accounts):
    starts = a_big_bill(db, user_id, accounts)

    html = client.get("/inbox").text

    alert = re.search(r'<div class="inbox-alert danger"[^>]*>(.*?)</div>', html, re.S)
    assert alert, "no red alert"
    assert starts.isoformat() in alert.group(1)
    assert 'href="/ledger"' in alert.group(1)


def test_the_alert_counts_toward_the_inbox_badge(client, db, user_id, accounts):
    before = review.review_waiting_count(user_id)
    a_big_bill(db, user_id, accounts)

    assert review.review_waiting_count(user_id) == before + 1


def test_no_alert_while_the_balance_stays_positive(client):
    assert "inbox-alert" not in client.get("/inbox").text


@pytest.mark.parametrize("path", ["/dashboard", "/ledger"])
@pytest.mark.parametrize("negative", [False, True])
def test_pages_no_longer_carry_the_negative_day(client, db, user_id, accounts, path, negative):
    if negative:
        a_big_bill(db, user_id, accounts)

    html = client.get(path).text.lower()

    assert "projected negative day" not in html
    assert "first negative" not in html
    assert "cash-flow alert" not in html
