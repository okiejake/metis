"""Importing happens from an account: choose a template, upload a file, and the
rows land on that account. Metis never creates an account or picks one from a
file's contents.

All data here is synthetic.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import import_templates as it
from conftest import add_imported
from services import SESSION_COOKIE

CSV = b"Date,Description,Amount\n2026-07-01,ACME GROCER,-25.00\n2026-07-02,EXAMPLE PAYROLL,100.00\n"


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
        "amount_layout": "signed", "amount_field": "Amount"}))


def upload(client, account_id, template_id, content=CSV):
    return client.post(f"/accounts/{account_id}/import", data={"template_id": str(template_id)},
                       files={"file": ("export.csv", content, "text/csv")}, follow_redirects=False)


def stored(db, user_id):
    return db.query_all("SELECT account_id, account, amount FROM imported_transactions WHERE user_id = ? "
                        "ORDER BY tx_date", [user_id])


def test_rows_land_on_the_account_the_import_was_started_from(client, db, user_id, accounts, template_id):
    checking = accounts["Example Checking"]

    response = upload(client, checking, template_id)

    assert response.status_code == 303
    assert "Example+Checking%3A+2+new" in response.headers["location"]
    assert stored(db, user_id) == [
        {"account_id": checking, "account": "Example Checking", "amount": -25.0},
        {"account_id": checking, "account": "Example Checking", "amount": 100.0},
    ]


def test_importing_the_same_file_again_adds_nothing(client, db, user_id, accounts, template_id):
    upload(client, accounts["Example Checking"], template_id)

    response = upload(client, accounts["Example Checking"], template_id)

    assert "0+new%2C+2+already+imported" in response.headers["location"]
    assert len(stored(db, user_id)) == 2


def test_a_file_without_the_template_s_columns_imports_nothing(client, db, user_id, accounts, template_id):
    response = upload(client, accounts["Example Checking"], template_id, b"Date,Memo,Total\n2026-07-01,ACME,-1\n")

    assert "err=1" in response.headers["location"]
    assert "Description" in response.headers["location"]
    assert stored(db, user_id) == []


def test_another_user_s_template_or_account_is_refused(client, db, user_id, accounts):
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
        their_account = conn.execute("INSERT INTO accounts (user_id, name, account_type) VALUES "
                                     "(?, 'Theirs', 'checking') RETURNING id", [other]).fetchone()[0]
    theirs = it.save_template(other, it.build_template({
        "name": "Theirs", "date_field": "Date", "description_field": "Description", "amount_field": "Amount"}))

    assert "err=1" in upload(client, accounts["Example Checking"], theirs).headers["location"]
    mine = it.save_template(user_id, it.build_template({
        "name": "Mine", "date_field": "Date", "description_field": "Description", "amount_field": "Amount"}))
    assert "err=1" in upload(client, their_account, mine).headers["location"]
    assert db.fetch_one("SELECT COUNT(*) FROM imported_transactions")[0] == 0


def test_the_accounts_page_never_creates_accounts_from_imported_rows(client, db, user_id):
    add_imported(db, user_id, None, "Checking", date(2026, 7, 1), "ACME", -1.0)

    client.get("/accounts")

    assert db.load_accounts(user_id) == []


def test_there_is_no_import_page(client):
    assert client.get("/import").status_code == 404
