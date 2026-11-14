"""Adding an account is one flow: name, type, import method, template. Plaid
is shown but not offered yet. Choosing "Create new" goes on to make a template.

All data here is synthetic.
"""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import import_templates as it
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def template_id(user_id):
    return it.save_template(user_id, it.build_template({
        "name": "Example export", "date_field": "Date", "description_field": "Description",
        "amount_field": "Amount"}))


def add(client, **fields):
    form = {"name": "Example Savings", "account_type": "checking", "method": "manual", "template": "new", **fields}
    return client.post("/accounts/new", data=form, follow_redirects=False)


def test_the_flow_offers_types_methods_and_templates(client, template_id):
    page = client.get("/accounts/new").text

    assert 'name="name"' in page
    assert re.search(r'value="checking"', page) and re.search(r'value="credit_card"', page)
    assert re.search(r'<input[^>]*value="plaid"[^>]*disabled', page)
    assert "coming soon" in page
    assert "Example export" in page and date.today().isoformat() in page
    assert re.search(r'name="template" value="new"', page)


def test_create_new_goes_on_to_make_a_template_for_the_account(client, db, user_id):
    response = add(client)

    [account] = db.load_accounts(user_id)
    assert account["name"] == "Example Savings" and account["account_type"] == "checking"
    assert response.headers["location"] == f"/templates/new?account_id={account['id']}"


def test_an_existing_template_becomes_the_account_s_default(client, db, user_id, template_id):
    response = add(client, template=str(template_id))

    [account] = db.load_accounts(user_id)
    assert account["default_template_id"] == template_id
    assert response.headers["location"].startswith("/accounts?")


def test_a_card_keeps_its_cycle_days(client, db, user_id, template_id):
    add(client, name="Example Card", account_type="credit_card", statement_day="5", due_day="25",
        template=str(template_id))

    [account] = db.load_accounts(user_id)
    assert (account["account_type"], account["statement_day"], account["due_day"]) == ("credit_card", 5, 25)


def test_plaid_is_not_accepted_yet(client, db, user_id):
    response = add(client, method="plaid")

    assert "err=1" in response.headers["location"]
    assert db.load_accounts(user_id) == []


def test_a_duplicate_name_is_refused(client, db, user_id, accounts):
    response = add(client, name="example checking")

    assert response.headers["location"].startswith("/accounts/new?") and "err=1" in response.headers["location"]
    assert len(db.load_accounts(user_id)) == 2
