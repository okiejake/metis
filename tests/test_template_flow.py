"""Making a template: upload a file, see a sample of it with its column names,
map the columns, check the preview, save. Saved from an account, the template
becomes that account's default and the file is imported into it.

All data here is synthetic.
"""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
import import_templates as it
from services import SESSION_COOKIE

CSV = (b"Posting Date,Details,Amount\n"
       b"07/01/2026,ACME GROCER,-25.00\n"
       b"07/02/2026,EXAMPLE PAYROLL,100.00\n")
MAPPING = {"name": "Example export", "date_field": "Posting Date", "description_field": "Details",
           "amount_layout": "signed", "amount_field": "Amount", "amount_sign": "standard",
           "date_format": "%m/%d/%Y"}


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def upload(client, content=CSV, account_id=None):
    data = {"account_id": str(account_id)} if account_id else {}
    return client.post("/templates/upload", data=data, files={"file": ("export.csv", content, "text/csv")},
                       follow_redirects=False)


def upload_id(response):
    return int(re.search(r"/templates/map/(\d+)", response.headers["location"]).group(1))


def test_the_upload_page_carries_the_account(client, accounts):
    page = client.get(f"/templates/new?account_id={accounts['Example Checking']}").text

    assert 'type="file"' in page
    assert f'name="account_id" value="{accounts["Example Checking"]}"' in page


def test_an_empty_file_is_refused(client):
    response = upload(client, b"")

    assert "err=1" in response.headers["location"]


def test_the_mapping_page_shows_the_sample_and_suggests_columns(client, accounts):
    response = upload(client, account_id=accounts["Example Checking"])
    page = client.get(response.headers["location"]).text

    for text in ("Posting Date", "Details", "ACME GROCER", "EXAMPLE PAYROLL"):
        assert text in page
    assert re.search(r'<option value="Posting Date" selected>', page), "date column suggested"
    assert re.search(r'<option value="%m/%d/%Y" selected>', page), "date format read from the sample"


def test_another_user_s_upload_cannot_be_opened(client, db, user_id):
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
    theirs = it.save_upload(other, "theirs.csv", CSV)

    response = client.get(f"/templates/map/{theirs}", follow_redirects=False)

    assert response.status_code == 303 and "err=1" in response.headers["location"]


def test_the_preview_shows_rows_as_metis_will_record_them(client):
    upload_id_ = upload_id(upload(client))

    fragment = client.post("/templates/preview", data={**MAPPING, "upload_id": upload_id_}).text

    assert "ACME GROCER" in fragment and "-$25.00" in fragment and "+$100.00" in fragment
    assert "2026-07-01" in fragment


def test_the_preview_names_what_is_wrong(client):
    upload_id_ = upload_id(upload(client))

    fragment = client.post("/templates/preview", data={**MAPPING, "amount_field": "Total",
                                                       "upload_id": upload_id_}).text

    assert "Total" in fragment and "preview-error" in fragment


def test_saving_from_an_account_sets_its_default_and_imports_the_file(client, db, user_id, accounts):
    checking = accounts["Example Checking"]
    upload_id_ = upload_id(upload(client, account_id=checking))

    response = client.post("/templates", data={**MAPPING, "upload_id": upload_id_, "account_id": checking},
                           follow_redirects=False)

    [saved] = it.load_templates(user_id)
    assert saved["created"] == date.today().isoformat()
    assert db.load_account_by_id(user_id, checking)["default_template_id"] == saved["id"]
    assert db.fetch_one("SELECT COUNT(*) FROM imported_transactions WHERE account_id = ?", [checking])[0] == 2
    assert it.load_upload(user_id, upload_id_) is None
    assert response.headers["location"].startswith("/accounts?") and "2+new" in response.headers["location"]


def test_saving_without_an_account_only_saves_the_template(client, db, user_id):
    upload_id_ = upload_id(upload(client))

    response = client.post("/templates", data={**MAPPING, "upload_id": upload_id_}, follow_redirects=False)

    assert [t["name"] for t in it.load_templates(user_id)] == ["Example export"]
    assert db.fetch_one("SELECT COUNT(*) FROM imported_transactions")[0] == 0
    assert response.headers["location"].startswith("/accounts?")


def test_an_incomplete_mapping_keeps_what_was_chosen(client, user_id):
    upload_id_ = upload_id(upload(client))

    page = client.post("/templates", data={**MAPPING, "date_field": "", "upload_id": upload_id_}).text

    assert "Choose the date column" in page
    assert 'value="Example export"' in page, "the name typed is kept"
    assert it.load_templates(user_id) == []


def test_a_template_can_be_deleted(client, user_id):
    template_id = it.save_template(user_id, it.build_template(MAPPING))

    client.post(f"/templates/{template_id}/delete", follow_redirects=False)

    assert it.load_templates(user_id) == []
