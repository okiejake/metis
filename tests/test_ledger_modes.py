"""The ledger's two modes (#124). transactions, the default, is every account's
imported transactions in one list, newest first; the filter picks accounts,
several at a time. cash flow is one account's run-down with its forecast, as
v0.5.1 built it.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import ledger
from conftest import add_bill, add_imported
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


@pytest.fixture
def spending(db, user_id, accounts):
    """A grocer on checking, then books on the card, then a bill in the forecast."""
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", TODAY - timedelta(days=10),
                 "EXAMPLE GROCER 0042", -40.00)
    add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=5), "ACME BOOKS",
                 -20.00)
    add_bill(db, user_id, "Acme Utilities", 90.00, TODAY + timedelta(days=10), account_id=accounts["Example Checking"])


def page(client, **params):
    return client.get("/ledger", params={"start": START.isoformat(), "end": END.isoformat(), **params}).text


def header(html):
    thead = re.search(r'<table class="ledger-table[^"]*">\s*<thead>(.*?)</thead>', html, re.S).group(1)
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)]


def descriptions(html):
    """The imported rows shown, top to bottom, by their description cell."""
    cells = re.findall(r'<td class="ledger-description">(.*?)</td>', html, re.S)
    return [found.group(0) for cell in cells if (found := re.search(r"EXAMPLE GROCER 0042|ACME BOOKS", cell))]


# --- transactions -------------------------------------------------------------------------


def test_the_ledger_opens_on_transactions_from_every_account_newest_first(client, accounts, spending):
    html = page(client)
    assert re.search(r'class="[^"]*ledger-mode[^"]*is-active[^"]*"[^>]*>transactions<', html)
    assert descriptions(html) == ["ACME BOOKS", "EXAMPLE GROCER 0042"]


def test_transactions_name_each_rows_account_and_have_no_type_or_balance(client, accounts, spending):
    html = page(client)
    assert header(html) == ["category", "bill", "account", "description", "amount", ""]
    books = re.search(r"<tr[^>]*>(?:(?!</tr>).)*ACME BOOKS(?:(?!</tr>).)*</tr>", html, re.S).group(0)
    assert re.search(r'<td class="ledger-account-cell">(?:<[^>]+>)*\s*example card', books)


def test_transactions_hold_no_forecast_summary_cards_or_jump(client, accounts, spending):
    html = page(client)
    assert "expected transaction" not in html and "forecast begins" not in html
    assert 'class="card compact-card"' not in html
    assert 'href="#forecast-begins"' not in html


def test_transactions_can_be_narrowed_to_some_accounts(client, accounts, spending):
    html = page(client, account=accounts["Example Card"])
    assert descriptions(html) == ["ACME BOOKS"]
    chip = re.search(r'<a class="filter-chip" href="([^"]+)"[^>]*>\s*example card', html)
    assert chip and "account=" not in chip.group(1)


def test_the_filter_offers_every_account_as_a_checkbox_all_checked_by_default(client, accounts, spending):
    form = re.search(r'<form method="get" action="[^"]*/ledger" class="filter-form"(.*?)</form>', page(client),
                     re.S).group(1)
    for name in ("Example Checking", "Example Card"):
        assert re.search(rf'<input type="checkbox" name="account" value="{accounts[name]}"[^>]*checked', form)
    assert '<input type="hidden" name="mode" value="transactions"' in form


def test_search_and_categories_work_on_transactions(client, accounts, spending):
    assert descriptions(page(client, q="books")) == ["ACME BOOKS"]
    assert descriptions(page(client, category="uncategorized")) == ["ACME BOOKS", "EXAMPLE GROCER 0042"]


def test_each_transaction_opens_the_edit_form(client, accounts, spending):
    assert len(re.findall(r'data-edit-href="/transactions/\d+/edit\?part=1"', page(client))) >= 2


def test_the_refreshed_table_keeps_the_mode(client, accounts, spending):
    section = re.search(r'<section class="panel ledger-section"[^>]*data-state-query="([^"]*)"', page(client)).group(1)
    assert "mode=transactions" in section
    table = client.get("/ledger/table", params={"mode": "transactions", "start": START.isoformat(),
                                                "end": END.isoformat()}).text
    assert header(table)[2] == "account"


# --- cash flow --------------------------------------------------------------------------


def test_cash_flow_is_one_accounts_run_down(client, accounts, spending):
    html = page(client, mode="cash-flow")
    assert header(html) == ["category", "bill", "type", "description", "amount", "balance", ""]
    assert descriptions(html) == ["EXAMPLE GROCER 0042"]
    assert "expected transaction" in html


def test_an_unknown_mode_is_transactions(client, accounts, spending):
    assert header(page(client, mode="sideways"))[2] == "account"


# --- switching ----------------------------------------------------------------------------


def test_the_switch_keeps_the_search_and_categories(client, accounts, spending):
    html = page(client, q="acme", category="uncategorized")
    link = re.search(r'<a class="[^"]*ledger-mode[^"]*" href="([^"]+)"[^>]*>cash flow<', html).group(1)
    assert "mode=cash-flow" in link and "q=acme" in link and "category=uncategorized" in link


def test_switching_to_cash_flow_keeps_one_picked_account_but_not_several(client, accounts, spending):
    card = accounts["Example Card"]
    one = page(client, account=card)
    link = re.search(r'href="([^"]+)"[^>]*>cash flow<', one).group(1)
    assert f"account={card}" in link
    several = page(client, account=[card, accounts["Example Checking"]])
    link = re.search(r'href="([^"]+)"[^>]*>cash flow<', several).group(1)
    assert "account=" not in link


def test_a_view_reads_several_accounts_in_transactions_and_one_in_cash_flow():
    assert ledger.LedgerView.from_params(account=["3", "4"]).account_ids == (3, 4)
    flow = ledger.LedgerView.from_params(mode="cash-flow", account=["3", "4"])
    assert (flow.account_id, flow.account_ids) == (3, ())
