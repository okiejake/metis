"""The Accounts page is where importing starts: an Import button per account
opening one pop-up, the user's templates, and any rows that belong to no account.

All data here is synthetic.
"""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import import_templates as it
from conftest import add_imported
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


def test_each_account_has_an_import_button_with_its_default_template(client, user_id, accounts, template_id):
    it.set_default_template(user_id, accounts["Example Checking"], template_id)

    page = client.get("/accounts").text

    assert re.search(rf'data-import-account="{accounts["Example Checking"]}"[^>]*data-import-template="{template_id}"',
                     page, re.S)
    assert re.search(rf'data-import-account="{accounts["Example Card"]}"[^>]*data-import-template=""', page, re.S)
    assert page.count('id="import-dialog"') == 1
    assert re.search(rf'<option value="{template_id}">example export</option>', page)


def test_the_page_starts_the_add_account_flow(client):
    assert re.search(r'href="/accounts/new"', client.get("/accounts").text)


def test_templates_are_listed_with_their_date_and_accounts(client, user_id, accounts, template_id):
    it.set_default_template(user_id, accounts["Example Card"], template_id)

    page = client.get("/accounts").text
    section = page[page.index('id="templates"'):]

    assert "Example export" in section and date.today().isoformat() in section and "Example Card" in section
    assert f'/templates/{template_id}/delete' in section


def test_rows_with_no_account_are_offered_for_placing_only_while_they_exist(client, db, user_id, accounts):
    assert "unplaced" not in client.get("/accounts").text
    add_imported(db, user_id, None, "Checking", date(2026, 7, 1), "ACME", -1.0)

    assert 'id="unplaced"' in client.get("/accounts").text
    client.post("/accounts/unplaced", data={"label": "Checking", "account_id": accounts["Example Checking"]},
                follow_redirects=False)

    assert db.fetch_one("SELECT account_id FROM imported_transactions")[0] == accounts["Example Checking"]
    assert 'id="unplaced"' not in client.get("/accounts").text


def test_the_edit_page_sets_the_default_template(client, db, user_id, accounts, template_id):
    checking = accounts["Example Checking"]
    page = client.get(f"/accounts/{checking}/edit").text
    assert 'name="default_template_id"' in page

    client.post(f"/accounts/{checking}/edit", data={"name": "Example Checking", "account_type": "checking",
                                                   "default_template_id": str(template_id)}, follow_redirects=False)
    assert db.load_account_by_id(user_id, checking)["default_template_id"] == template_id

    client.post(f"/accounts/{checking}/edit", data={"name": "Example Checking", "account_type": "checking"},
                follow_redirects=False)
    assert db.load_account_by_id(user_id, checking)["default_template_id"] == template_id, "left alone when absent"
