"""The edit form (#123): one place to settle an imported transaction. It holds
the category, the bill (a bill, one-time, unexpected or none), the note, the
transfer mark on checking, and the split lines, each with its own category and
bill. Saving a category or a bill match offers the rule as step 2. The ledger
opens it over the table; without script it is a page of its own.

All data here is synthetic.
"""

import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_bill, add_imported, reconcile
from services import SESSION_COOKIE

TODAY = date.today()
NEXT = "/ledger/table?account=1&q=acme"


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def store(db, user_id, accounts):
    """A $100 store run on checking, a phone bill and a category."""
    return {
        "tx": add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY - timedelta(days=2),
                           "ACME STORE 12", -100.00),
        "phone": add_bill(db, user_id, "Example Phone", 40.00, TODAY + timedelta(days=5),
                          account_id=accounts["Example Checking"]),
        "home": db.create_category(user_id, "home"),
    }


def form(client, tx, **params):
    """The form as the ledger's dialog gets it, without the blank lines it keeps
    in <template> for the script to copy."""
    html = client.get(f"/transactions/{tx}/edit", params={"part": "1", **params}).text
    return re.sub(r"<template.*?</template>", "", html, flags=re.S)


def save(client, tx, **fields):
    data = {"next": NEXT, "category": "", "bill": "", "note": "", **fields}
    return client.post(f"/transactions/{tx}/edit", data=data, follow_redirects=False)


def tx_row(db, tx):
    return db.query_all("SELECT category_id, unexpected, note, is_transfer FROM imported_transactions WHERE id = ?",
                        [tx])[0]


def linked(db, tx):
    return db.fetch_one("SELECT source_type, source_id FROM expected_reconciliations "
                        "WHERE imported_transaction_id = ?", [tx])


# --- The form ------------------------------------------------------------------------


def test_the_form_comes_alone_for_the_ledger_and_as_a_page_without_script(client, store):
    part = form(client, store["tx"])
    assert "<html" not in part and f'action="/transactions/{store["tx"]}/edit"' in part
    page = client.get(f"/transactions/{store['tx']}/edit", params={"next": "/ledger"}).text
    assert "<html" in page and f'action="/transactions/{store["tx"]}/edit"' in page


def test_the_form_holds_category_bill_note_and_transfer(client, store):
    html = form(client, store["tx"])
    assert re.search(r'<select name="category"[^>]*data-category-picker', html)
    bill = re.search(r'<select name="bill".*?</select>', html, re.S).group(0)
    assert f'value="{store["phone"]}"' in bill and 'value="one_time"' in bill and 'value="unexpected"' in bill
    assert 'name="note"' in html
    assert re.search(r'<input type="checkbox" name="transfer"', html)


def test_a_card_charge_has_no_transfer_mark(client, db, user_id, accounts):
    charge = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=2),
                          "ACME BOOKS", -20.00)
    assert 'name="transfer"' not in form(client, charge)


def test_the_bill_shows_what_the_transaction_is_matched_to_or_marked(client, db, user_id, store):
    reconcile(db, user_id, "bill", store["phone"], store["tx"])
    assert re.search(rf'<option value="{store["phone"]}"[^>]*selected', form(client, store["tx"]))
    db.execute("DELETE FROM expected_reconciliations")
    db.execute("UPDATE imported_transactions SET unexpected = TRUE WHERE id = ?", [store["tx"]])
    assert re.search(r'<option value="unexpected"[^>]*selected', form(client, store["tx"]))


def test_a_split_transaction_opens_on_its_lines(client, db, user_id, store):
    db.save_transaction_splits(user_id, store["tx"], [
        {"category_id": store["home"], "amount": 30.00, "note": "", "bill_id": store["phone"]}])
    html = form(client, store["tx"])
    assert "data-split-open" in html
    assert re.search(r'name="part_amount"[^>]*value="30.00"', html)
    assert re.search(rf'<select name="part_bill".*?<option value="{store["phone"]}"[^>]*selected', html, re.S)
    assert re.search(r'name="original_amount"[^>]*value="70.00"', html)
    assert re.search(r'<button[^>]*name="unsplit"[^>]*>un-split</button>', html)


def test_splitting_starts_with_two_lines_half_and_half(client, db, user_id, accounts):
    odd = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY - timedelta(days=2),
                       "ACME STORE 13", -100.01)
    html = form(client, odd, split="1")
    assert re.search(r'name="original_amount"[^>]*value="50.01"', html)
    assert re.findall(r'name="part_amount"[^>]*value="([\d.]+)"', html) == ["50.00"]


# --- Saving ----------------------------------------------------------------------------


def test_a_new_category_is_saved_and_the_rule_is_offered_as_step_two(client, db, store):
    response = save(client, store["tx"], category=str(store["home"]))
    assert tx_row(db, store["tx"])["category_id"] == store["home"]
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert location.path == f"/transactions/{store['tx']}/edit"
    assert (query["step"], query["rule_kind"], query["next"]) == (["rule"], ["category"], [NEXT])


def test_step_two_shows_the_rule_inside_the_form(client, db, store):
    save(client, store["tx"], category=str(store["home"]))
    html = form(client, store["tx"], step="rule", rule_kind="category", next=NEXT)
    assert "data-tx-edit" in html
    assert re.search(rf'<form[^>]*action="/categories/{store["home"]}/rules"', html)
    assert f'name="next" value="{NEXT.replace("&", "&amp;")}"' in html


def test_matching_a_bill_offers_its_rule(client, db, store):
    response = save(client, store["tx"], bill=str(store["phone"]))
    assert linked(db, store["tx"]) == ("bill", store["phone"])
    assert "rule_kind=bill" in response.headers["location"]


def test_choosing_no_bill_unmatches_and_returns_to_the_ledger(client, db, user_id, store):
    reconcile(db, user_id, "bill", store["phone"], store["tx"])
    response = save(client, store["tx"], bill="")
    assert linked(db, store["tx"]) is None
    assert response.headers["location"].startswith(NEXT)


def test_unexpected_is_marked_and_taken_off(client, db, store):
    save(client, store["tx"], bill="unexpected")
    assert tx_row(db, store["tx"])["unexpected"] is True
    save(client, store["tx"], bill="")
    assert tx_row(db, store["tx"])["unexpected"] is False


def test_one_time_makes_a_one_time_item_from_the_transaction(client, db, store):
    save(client, store["tx"], bill="one_time")
    assert linked(db, store["tx"])[0] == "one_time"


def test_switching_bills_moves_the_match(client, db, user_id, accounts, store):
    gym = add_bill(db, user_id, "Example Gym", 30.00, TODAY + timedelta(days=5), account_id=accounts["Example Checking"])
    reconcile(db, user_id, "bill", store["phone"], store["tx"])
    save(client, store["tx"], bill=str(gym))
    assert linked(db, store["tx"]) == ("bill", gym)


def test_an_unchanged_save_returns_to_the_ledger_without_a_rule(client, db, store):
    response = save(client, store["tx"])
    assert response.headers["location"].startswith(NEXT)


def test_the_note_is_saved(client, db, store):
    save(client, store["tx"], note="for the office")
    assert tx_row(db, store["tx"])["note"] == "for the office"


def test_the_transfer_mark_follows_its_checkbox(client, db, store):
    save(client, store["tx"], transfer_field="1", transfer="1")
    assert tx_row(db, store["tx"])["is_transfer"] is True
    save(client, store["tx"], transfer_field="1")
    assert tx_row(db, store["tx"])["is_transfer"] is False


def test_split_lines_are_saved_each_with_its_bill(client, db, user_id, store):
    garden = db.create_category(user_id, "garden")
    save(client, store["tx"], category=str(garden), part_category=[str(store["home"])],
         part_bill=[str(store["phone"])], part_amount=["40.00"], part_note=["phone case"])
    assert [(p["category_id"], p["bill_id"], p["amount"], p["note"]) for p in db.load_transaction_splits(user_id, store["tx"])] == [
        (store["home"], store["phone"], 40.00, "phone case")]
    assert tx_row(db, store["tx"])["category_id"] == garden


def test_lines_over_the_transaction_are_refused(client, db, user_id, store):
    response = save(client, store["tx"], part_category=[str(store["home"])], part_bill=[""], part_amount=["120.00"],
                    part_note=[""])
    assert "err=1" in response.headers["location"]
    assert db.load_transaction_splits(user_id, store["tx"]) == []


def test_un_split_takes_the_lines_off(client, db, user_id, store):
    db.save_transaction_splits(user_id, store["tx"], [{"category_id": store["home"], "amount": 30.00, "note": ""}])
    save(client, store["tx"], unsplit="1", part_category=[str(store["home"])], part_bill=[""], part_amount=["30.00"],
         part_note=[""])
    assert db.load_transaction_splits(user_id, store["tx"]) == []


def test_saving_with_no_lines_takes_them_off(client, db, user_id, store):
    db.save_transaction_splits(user_id, store["tx"], [{"category_id": store["home"], "amount": 30.00, "note": ""}])
    save(client, store["tx"])
    assert db.load_transaction_splits(user_id, store["tx"]) == []


def test_another_users_transaction_is_not_found(client, db, store):
    response = save(client, 999999, note="nope")
    assert "err=1" in response.headers["location"]
    assert client.get("/transactions/999999/edit", follow_redirects=False).status_code == 303


def test_a_next_off_the_site_is_ignored(client, db, store):
    response = save(client, store["tx"], next="https://example.com/elsewhere")
    assert not response.headers["location"].startswith("https://example.com")


def test_the_split_address_opens_the_form_on_its_lines(client, store):
    response = client.get(f"/transactions/{store['tx']}/split", params={"next": "/inbox#categories"},
                          follow_redirects=False)
    location = urlsplit(response.headers["location"])
    assert location.path == f"/transactions/{store['tx']}/edit"
    assert parse_qs(location.query)["split"] == ["1"]


def test_a_card_charge_is_never_made_a_transfer(client, db, user_id, accounts):
    charge = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=3),
                          "ACME BOOKS", -20.00)
    response = save(client, charge, transfer_field="1", transfer="1")
    assert "err=1" in response.headers["location"]
    assert tx_row(db, charge)["is_transfer"] is False
