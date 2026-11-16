"""The ledger page (v0.5.1): one account at a time, the same columns and summary
cards on every account, a filter that hides the balance, a jump to the
forecast, and rows settled in place.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import ledger
from conftest import add_bill, add_imported, reconcile
from services import SESSION_COOKIE

TODAY = date.today()
START = TODAY - timedelta(days=30)
END = TODAY + timedelta(days=60)


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def page(client, **params):
    params = {"start": START.isoformat(), "end": END.isoformat(), **params}
    return client.get("/ledger", params=params).text


def header(html):
    thead = re.search(r'<table class="ledger-table">\s*<thead>(.*?)</thead>', html, re.S).group(1)
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)]


def checking_import(db, user_id, accounts, when, description, amount, **kwargs):
    return add_imported(db, user_id, accounts["Example Checking"], "Example Checking", when, description, amount,
                        **kwargs)


@pytest.fixture
def month(db, user_id, accounts):
    """Imports up to five days ago, then a bill in the forecast."""
    grocer = checking_import(db, user_id, accounts, TODAY - timedelta(days=10), "EXAMPLE GROCER 0042", -40.00)
    checking_import(db, user_id, accounts, TODAY - timedelta(days=5), "EXAMPLE COFFEE", -5.00)
    add_bill(db, user_id, "Acme Utilities", 90.00, TODAY + timedelta(days=10), account_id=accounts["Example Checking"])
    return {"grocer": grocer}


# --- Columns and cards -----------------------------------------------------------------


def test_every_account_has_the_same_columns(client, accounts, month):
    expected = ["date", "description", "bill", "category", "type", "payments", "charges", "balance", ""]
    assert header(page(client)) == expected
    assert header(page(client, account=accounts["Example Card"])) == expected


def test_every_account_has_the_same_summary_cards(client, accounts, month):
    for html in (page(client), page(client, account=accounts["Example Card"])):
        cards = re.findall(r'<article class="card compact-card">\s*<h3>(.*?)</h3>', html)
        assert cards == ["current balance", "payments", "charges", "projected balance"]


def test_the_page_names_the_account_it_shows(client, accounts, month):
    assert re.search(r'class="ledger-account"[^>]*>\s*example checking', page(client))
    assert re.search(r'class="ledger-account"[^>]*>\s*example card', page(client, account=accounts["Example Card"]))


def test_an_account_that_is_not_the_users_falls_back_to_the_default_checking_account(client, accounts, month):
    html = page(client, account=999999)
    assert re.search(r'class="ledger-account"[^>]*>\s*example checking', html)
    assert 'class="flash error"' in html


# --- Filtering ----------------------------------------------------------------------------


def test_a_filter_hides_the_balance_the_summary_cards_and_month_summaries(client, accounts, month):
    html = page(client, q="grocer")
    assert "balance" not in header(html)
    assert 'class="card compact-card"' not in html
    assert "month summary" not in html
    assert "EXAMPLE GROCER 0042" in html and "EXAMPLE COFFEE" not in html


def test_choosing_another_account_keeps_the_balance_and_cards(client, accounts, month):
    html = page(client, account=accounts["Example Card"])
    assert "balance" in header(html)
    assert 'class="card compact-card"' in html


def test_each_filter_shows_a_chip_that_removes_it_and_clear_removes_them_all(client, accounts, month):
    html = page(client, q="grocer")
    chip = re.search(r'<a class="filter-chip" href="([^"]+)"[^>]*>\s*search: grocer', html)
    assert chip and "q=" not in chip.group(1)
    clear = re.search(r'<a class="button-link filter-clear" href="([^"]*)"', html).group(1)
    assert re.search(r"/ledger\?account=\d+", clear) and "q=" not in clear


def test_the_filter_offers_the_accounts_a_search_and_every_category(client, db, user_id, accounts, month):
    db.create_category(user_id, "groceries")
    html = page(client)
    form = re.search(r'<form method="get" action="[^"]*/ledger" class="filter-form"(.*?)</form>', html, re.S).group(1)
    assert 'name="account"' in form and f'value="{accounts["Example Card"]}"' in form
    assert 'name="q"' in form
    assert re.search(r'name="category" value="all"[^>]*checked', form)
    assert 'value="uncategorized"' in form
    assert ">groceries<" in form
    assert "data-category-search" in form
    assert re.search(r'<button type="submit"[^>]*>apply</button>', form)


# --- Jump to forecast ------------------------------------------------------------------------


def test_jump_to_forecast_shows_when_the_forecast_begins_on_the_page(client, accounts, month):
    html = page(client)
    assert 'href="#forecast-begins"' in html and 'id="forecast-begins"' in html
    assert "forecast from" not in html


def test_there_is_no_jump_when_the_window_holds_no_forecast(client, accounts, month):
    html = page(client, start=(TODAY - timedelta(days=20)).isoformat(), end=(TODAY - timedelta(days=8)).isoformat())
    assert 'href="#forecast-begins"' not in html


# --- What a row shows -------------------------------------------------------------------------


def test_an_imported_row_shows_the_bill_it_is_matched_to(client, db, user_id, accounts, month):
    bill = add_bill(db, user_id, "Example Gym", 30.00, TODAY - timedelta(days=37),
                    account_id=accounts["Example Checking"])
    gym = checking_import(db, user_id, accounts, TODAY - timedelta(days=7), "EXMPL FITNESS 99", -30.00)
    reconcile(db, user_id, "bill", bill, gym)
    row = re.search(r"<tr[^>]*>(?:(?!</tr>).)*EXMPL FITNESS 99(?:(?!</tr>).)*</tr>", page(client), re.S).group(0)
    assert re.search(r'<td class="ledger-bill">\s*example gym', row)


def test_a_rows_note_shows_under_its_description(client, db, user_id, accounts, month):
    db.execute("UPDATE imported_transactions SET note = 'for the office' WHERE id = ?", [month["grocer"]])
    assert re.search(r'class="ledger-note"[^>]*>\s*for the office', page(client))


# --- Settling a row in place -------------------------------------------------------------------


def test_the_row_menu_offers_every_action(client, accounts, month):
    html = page(client)
    tx = month["grocer"]
    for action in ("category", "split", "match", "note"):
        assert re.search(rf'href="[^"]*open={tx}%3A{action}', html), action
    assert f'action="/transactions/{tx}/transfer"' in html


@pytest.mark.parametrize("action, marker", [
    ("category", 'action="/transactions/{tx}/category"'),
    ("split", 'action="/transactions/{tx}/split"'),
    ("match", 'action="/transactions/{tx}/one-time"'),
    ("note", 'action="/transactions/{tx}/note"'),
])
def test_each_action_opens_its_panel_under_the_row(client, accounts, month, action, marker):
    tx = month["grocer"]
    html = page(client, open=f"{tx}:{action}")
    assert 'class="ledger-panel-row"' in html
    assert marker.format(tx=tx) in html


def test_only_the_open_row_carries_a_panel(client, accounts, month):
    assert 'class="ledger-panel-row"' not in page(client)


def test_a_matched_row_offers_unmatch(client, db, user_id, accounts, month):
    bill = add_bill(db, user_id, "Example Gym", 30.00, TODAY - timedelta(days=37),
                    account_id=accounts["Example Checking"])
    gym = checking_import(db, user_id, accounts, TODAY - timedelta(days=7), "EXMPL FITNESS 99", -30.00)
    reconcile(db, user_id, "bill", bill, gym)
    assert f'action="/transactions/{gym}/unmatch"' in page(client)


def test_actions_post_in_the_background_and_keep_the_view(client, accounts, month):
    html = page(client, q="grocer", open=f"{month['grocer']}:note")
    section = re.search(r'<section class="panel ledger-section" id="ledger-section"([^>]*)>', html).group(1)
    assert 'data-section-url="/ledger/table"' in section
    assert re.search(r'data-state-query="[^"]*q=grocer', section)
    assert "open=" not in re.search(r'data-state-query="([^"]*)"', section).group(1)
    note_form = re.search(r'<form[^>]*action="/transactions/\d+/note"[^>]*>', html).group(0)
    assert "data-partial" in note_form


def test_the_ledger_table_comes_alone(client, accounts, month):
    response = client.get("/ledger/table", params={"start": START.isoformat(), "end": END.isoformat()})
    assert response.status_code == 200
    assert 'id="ledger-section"' in response.text and "<html" not in response.text


def test_a_note_is_saved_from_the_ledger(client, db, accounts, month):
    response = client.post(f"/transactions/{month['grocer']}/note",
                           data={"note": "for the office", "next": "/ledger?q=grocer"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/ledger?q=grocer")
    assert db.fetch_one("SELECT note FROM imported_transactions WHERE id = ?", [month["grocer"]])[0] == "for the office"


def test_a_row_is_marked_a_transfer_and_back(client, db, accounts, month):
    tx = month["grocer"]
    client.post(f"/transactions/{tx}/transfer", data={"transfer": "1", "next": "/ledger"})
    assert db.fetch_one("SELECT is_transfer FROM imported_transactions WHERE id = ?", [tx])[0] is True
    client.post(f"/transactions/{tx}/transfer", data={"transfer": "0", "next": "/ledger"})
    assert db.fetch_one("SELECT is_transfer FROM imported_transactions WHERE id = ?", [tx])[0] is False


def test_a_card_charge_is_never_made_a_transfer(client, db, user_id, accounts, month):
    charge = add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=3),
                          "ACME BOOKS", -20.00)
    response = client.post(f"/transactions/{charge}/transfer", data={"transfer": "1", "next": "/ledger"},
                           follow_redirects=False)
    assert "err=1" in response.headers["location"]
    assert db.fetch_one("SELECT is_transfer FROM imported_transactions WHERE id = ?", [charge])[0] is False


# --- Transfers by merchant -----------------------------------------------------------------------


def test_a_merchant_view_offers_to_mark_its_debits_as_transfers(client, db, user_id, accounts, month):
    token = ledger.merchant_token("EXAMPLE GROCER 0042")
    html = page(client, merchant=token)
    assert re.search(r'<form method="post" action="/merchants/transfer"', html)
    assert f'name="merchant" value="{token}"' in html


def test_marking_a_merchant_a_transfer_marks_its_debits_now_and_later(client, db, user_id, accounts, month):
    token = ledger.merchant_token("EXAMPLE GROCER 0042")
    response = client.post("/merchants/transfer", data={"merchant": token, "next": f"/ledger?merchant={token}"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert db.fetch_one("SELECT is_transfer FROM imported_transactions WHERE id = ?", [month["grocer"]])[0] is True
    assert db.fetch_one("SELECT COUNT(*) FROM transfer_rules WHERE user_id = ?", [user_id])[0] == 1
