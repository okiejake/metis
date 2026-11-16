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
    """The ledger in cash flow, one account at a time (see test_ledger_modes.py
    for transactions)."""
    params = {"start": START.isoformat(), "end": END.isoformat(), "mode": "cash-flow", **params}
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
    assert text(bill_cell(row)) == ["example", "gym"]


def test_a_rows_note_shows_beside_its_description(client, db, user_id, accounts, month):
    db.execute("UPDATE imported_transactions SET note = 'for the office' WHERE id = ?", [month["grocer"]])
    assert re.search(r'class="ledger-note"[^>]*>\s*for the office', page(client))


def row_of(html, text):
    return re.search(rf"<tr[^>]*>(?:(?!</tr>).)*{re.escape(text)}(?:(?!</tr>).)*</tr>", html, re.S).group(0)


def bill_cell(row):
    return re.search(r'<td class="ledger-bill">(.*?)</td>', row, re.S).group(1)


def text(html):
    return re.sub(r"<[^>]+>", " ", html).split()


def test_an_unmatched_row_leaves_the_bill_blank(client, accounts, month):
    assert text(bill_cell(row_of(page(client), "EXAMPLE GROCER 0042"))) == []


def test_an_unexpected_row_shows_a_warning_in_the_bill_column(client, db, accounts, month):
    db.execute("UPDATE imported_transactions SET unexpected = TRUE WHERE id = ?", [month["grocer"]])
    cell = bill_cell(row_of(page(client), "EXAMPLE GROCER 0042"))
    assert 'data-lucide="triangle-alert"' in cell and 'aria-label="unexpected"' in cell


def test_a_forecast_row_names_its_bill_and_reads_expected_transaction(client, accounts, month):
    row = row_of(page(client), "acme utilities")
    assert text(bill_cell(row)) == ["acme", "utilities"]
    description = re.search(r'<td class="ledger-description">(.*?)</td>', row, re.S).group(1)
    assert text(description) == ["expected", "transaction"] and 'class="ledger-expected"' in description


def test_a_split_row_shows_every_lines_category_and_bill(client, db, user_id, accounts, month):
    home = db.create_category(user_id, "home")
    garden = db.create_category(user_id, "garden")
    gym = add_bill(db, user_id, "Example Gym", 30.00, TODAY - timedelta(days=37), account_id=accounts["Example Checking"])
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [home, month["grocer"]])
    db.save_transaction_splits(user_id, month["grocer"], [{"category_id": garden, "amount": 15.00, "note": "", "bill_id": gym}])
    row = row_of(page(client), "EXAMPLE GROCER 0042")
    category = re.search(r'<td class="ledger-category">(.*?)</td>', row, re.S).group(1)
    assert re.findall(r'class="category-pill">.*?</span>(\w+)</span>', category) == ["home", "garden"]
    assert "example gym" in bill_cell(row)


# --- Settling a row in place -------------------------------------------------------------------


def test_each_imported_row_has_one_edit_button_that_opens_the_form(client, accounts, month):
    row = row_of(page(client), "EXAMPLE GROCER 0042")
    tx = month["grocer"]
    assert re.search(rf'href="/transactions/{tx}/edit\?next=[^"]+"[^>]*data-edit-href="/transactions/{tx}/edit\?part=1"', row)
    assert "row-action-menu" not in row


def test_an_uncategorized_rows_categorize_opens_the_form(client, accounts, month):
    category = re.search(r'<td class="ledger-category">(.*?)</td>', row_of(page(client), "EXAMPLE GROCER 0042"),
                         re.S).group(1)
    assert f'data-edit-href="/transactions/{month["grocer"]}/edit?part=1"' in category and "categorize" in category


def test_the_edit_dialog_sits_outside_the_table_so_opening_it_leaves_the_table_alone(client, accounts, month):
    html = page(client)
    assert re.search(r'<dialog id="tx-edit-dialog"', html)
    section = html[html.index('id="ledger-section"'):]
    assert 'id="tx-edit-dialog"' not in section[:section.index("</section>")]


def test_the_ledger_never_shows_the_rule_at_the_top_of_the_table(client, db, user_id, accounts, month):
    home = db.create_category(user_id, "home")
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [home, month["grocer"]])
    assert "data-rule-prompt" not in page(client, rule_tx=month["grocer"], rule_kind="category")


def test_actions_post_in_the_background_and_keep_the_view(client, accounts, month):
    html = page(client, q="grocer")
    section = re.search(r'<section class="panel ledger-section" id="ledger-section"([^>]*)>', html).group(1)
    assert 'data-section-url="/ledger/table"' in section
    assert re.search(r'data-state-query="[^"]*q=grocer', section)


def test_the_ledger_table_comes_alone(client, accounts, month):
    response = client.get("/ledger/table", params={"start": START.isoformat(), "end": END.isoformat(),
                                                   "mode": "cash-flow"})
    assert response.status_code == 200
    assert 'id="ledger-section"' in response.text and "<html" not in response.text


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
