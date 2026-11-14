"""One form, on its own page, adds and edits every expected transaction: a
bill, scheduled income or a one-time item (#101, #108). It opens prefilled from
ids and enums in the query string, never from names or amounts.

All data here is synthetic.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import inbox
from conftest import add_bill, add_imported, add_one_time
from services import SESSION_COOKIE

TODAY = date.today()


@pytest.fixture
def client(db, user_id, accounts):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def field(html, name):
    """The value of a named input, or the selected option of a named select, in
    the expected form (the header's date range form has a `next` of its own)."""
    form = re.search(r'<form[^>]*data-expected-form.*?</form>', html, re.S)
    html = form.group(0) if form else html
    select = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', html, re.S)
    if select:
        chosen = re.search(r'<option value="([^"]*)"[^>]*\bselected', select.group(1))
        return chosen.group(1) if chosen else ""
    tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert tag, f"no field named {name}"
    value = re.search(r'value="([^"]*)"', tag.group(0))
    return value.group(1) if value else ""


def bill_form(**changes):
    form = {"name": "Acme Utilities", "kind": "expense", "amount": "40.00", "repeats": "monthly",
            "first_due_date": "2026-01-10"}
    form.update(changes)
    return form


# --- One form -----------------------------------------------------------------

def test_one_template_serves_bills_income_and_one_time_items(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    item = add_one_time(db, user_id, "Acme Repair", 100.0, date(2026, 3, 14))

    pages = {path: client.get(path) for path in
             ("/bills/new", "/bills/new?kind=income", "/one-time/new", f"/bills/{bill}/edit", f"/one-time/{item}/edit")}

    assert all(response.status_code == 200 for response in pages.values())
    assert all('data-expected-form' in response.text for response in pages.values())
    assert "<h2>add bill</h2>" in pages["/bills/new"].text
    assert "<h2>add income</h2>" in pages["/bills/new?kind=income"].text
    assert "<h2>add one-time</h2>" in pages["/one-time/new"].text
    assert "<h2>edit bill</h2>" in pages[f"/bills/{bill}/edit"].text
    assert "<h2>edit one-time</h2>" in pages[f"/one-time/{item}/edit"].text


def test_repeats_offers_one_time_and_each_frequency_when_adding(client):
    html = client.get("/bills/new").text
    repeats = re.search(r'<select[^>]*name="repeats"[^>]*>(.*?)</select>', html, re.S).group(1)

    assert re.findall(r'<option value="([^"]*)"', repeats) == [
        "one-time", "weekly", "biweekly", "semimonthly", "monthly", "every_x_months", "yearly"
    ]
    assert field(html, "repeats") == "monthly"
    assert field(client.get("/one-time/new").text, "repeats") == "one-time"


def test_the_kind_of_item_is_fixed_when_editing(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    item = add_one_time(db, user_id, "Acme Repair", 100.0, date(2026, 3, 14))

    bill_page = client.get(f"/bills/{bill}/edit").text
    item_page = client.get(f"/one-time/{item}/edit").text

    assert 'value="one-time"' not in re.search(r'<select[^>]*name="repeats".*?</select>', bill_page, re.S).group(0)
    assert 'name="repeats"' not in item_page or '<select' not in re.search(r'[^>]*name="repeats"[^>]*', item_page).group(0)
    assert field(bill_page, "first_due_date") == "2026-01-10"
    assert field(item_page, "first_due_date") == "2026-03-14"


def test_no_day_of_month_input_is_left_anywhere(client, db, user_id, accounts):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    for months in (4, 3, 2, 1):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=30 * months),
                     "ACME STREAMING", -10.0)

    for path in ("/bills/new", f"/bills/{bill}/edit", "/one-time/new", "/budget", "/inbox", "/ledger"):
        html = client.get(path).text
        assert "day_of_month" not in html and "day of month" not in html.replace("(day of month)", ""), path


# --- Adding -------------------------------------------------------------------

def test_adding_a_bill_stores_its_first_due_date(client, db, user_id, accounts):
    response = client.post("/bills", data=bill_form(account_id=str(accounts["Example Checking"])), follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"].startswith("/budget?")
    assert db.fetch_all("SELECT name, kind, amount, first_due_date, frequency_type, account_id FROM bills") == [
        ("Acme Utilities", "expense", 40.0, date(2026, 1, 10), "monthly", accounts["Example Checking"])
    ]


def test_income_is_a_bill_row_of_kind_income(client, db):
    client.post("/bills", data=bill_form(name="Example Employer", kind="income", repeats="biweekly"))

    assert db.fetch_all("SELECT name, kind, frequency_type FROM bills") == [("Example Employer", "income", "biweekly")]


def test_choosing_one_time_under_repeats_adds_a_one_time_item(client, db):
    client.post("/bills", data=bill_form(name="Acme Repair", repeats="one-time", first_due_date="2026-03-14"))

    assert db.fetch_one("SELECT COUNT(*) FROM bills")[0] == 0
    assert db.fetch_all("SELECT name, kind, amount, expected_date FROM one_time_items") == [
        ("Acme Repair", "expense", 40.0, date(2026, 3, 14))
    ]


def test_the_one_time_route_takes_the_same_form(client, db):
    client.post("/one-time", data=bill_form(name="Acme Repair", repeats="one-time", first_due_date="2026-03-14"))
    client.post("/one-time", data=bill_form(name="Acme Utilities", repeats="monthly"))

    assert db.fetch_one("SELECT COUNT(*) FROM one_time_items")[0] == 1
    assert db.fetch_one("SELECT COUNT(*) FROM bills")[0] == 1


def test_every_x_months_and_semi_monthly_keep_their_own_fields(client, db):
    client.post("/bills", data=bill_form(name="Acme Insurance", repeats="every_x_months", interval_months="3"))
    client.post("/bills", data=bill_form(name="Example Employer", kind="income", repeats="semimonthly",
                                         semimonthly_day1="5", semimonthly_day2="20"))

    assert db.fetch_all("SELECT name, interval_months, semimonthly_day1, semimonthly_day2 FROM bills ORDER BY name") == [
        ("Acme Insurance", 3, 1, 15), ("Example Employer", 1, 5, 20)
    ]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"name": "  "}, "name"),
        ({"amount": "0"}, "amount"),
        ({"kind": "gift"}, "type"),
        ({"repeats": "hourly"}, "repeats"),
        ({"first_due_date": "soon"}, "date"),
        ({"end_date": "2025-12-31"}, "end date"),
    ],
)
def test_a_bad_form_adds_nothing_and_says_why(client, db, changes, message):
    response = client.post("/bills", data=bill_form(**changes), follow_redirects=False)

    assert db.fetch_one("SELECT COUNT(*) FROM bills")[0] == 0
    assert response.status_code == 303 and "err=1" in response.headers["location"]
    assert message in response.headers["location"].lower().replace("+", " ")


def test_saving_returns_to_next_on_this_site_only(client):
    back = client.post("/bills", data=bill_form(next="/inbox#bills"), follow_redirects=False)
    away = client.post("/bills", data=bill_form(name="Other", next="//evil.example/x"), follow_redirects=False)

    assert back.headers["location"].startswith("/inbox?") and back.headers["location"].endswith("#bills")
    assert away.headers["location"].startswith("/dashboard?")


# --- Editing ------------------------------------------------------------------

def test_editing_a_bill_changes_it(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))

    response = client.post(f"/bills/{bill}/edit", data=bill_form(name="Acme Power", first_due_date="2026-01-20",
                                                                 amount="45.00"), follow_redirects=False)

    assert response.headers["location"].startswith("/budget?")
    assert db.fetch_all("SELECT name, amount, first_due_date FROM bills") == [("Acme Power", 45.0, date(2026, 1, 20))]


def test_a_changed_amount_can_apply_from_today_forward_only(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))

    client.post(f"/bills/{bill}/edit", data=bill_form(amount="55.00", amount_change_scope="future"))

    assert db.fetch_one("SELECT amount FROM bills WHERE id = ?", [bill])[0] == 40.0
    assert db.fetch_all("SELECT bill_id, effective_date, amount FROM amount_overrides") == [(bill, TODAY, 55.0)]


def test_a_changed_amount_for_all_occurrences_clears_earlier_changes(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    db.save_amount_override(bill, date(2026, 6, 1), 50.0)

    client.post(f"/bills/{bill}/edit", data=bill_form(amount="60.00", amount_change_scope="all"))

    assert db.fetch_one("SELECT amount FROM bills WHERE id = ?", [bill])[0] == 60.0
    assert db.fetch_one("SELECT COUNT(*) FROM amount_overrides")[0] == 0


def test_a_bill_cannot_be_turned_into_a_one_time_item_by_editing(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))

    response = client.post(f"/bills/{bill}/edit", data=bill_form(repeats="one-time"), follow_redirects=False)

    assert "err=1" in response.headers["location"]
    assert db.fetch_one("SELECT COUNT(*) FROM bills")[0] == 1 and db.fetch_one("SELECT COUNT(*) FROM one_time_items")[0] == 0


def test_editing_and_deleting_a_one_time_item(client, db, user_id, accounts):
    item = add_one_time(db, user_id, "Acme Repair", 100.0, date(2026, 3, 14))
    tx = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 6, 1), "ACME AUTO", -75.0)
    db.confirm_reconciliation(user_id, "one_time", item, tx)
    db.create_match_rule(user_id, "one_time", item, "ACME AUTO")

    client.post(f"/one-time/{item}/edit", data=bill_form(name="Acme Car Repair", repeats="one-time",
                                                         first_due_date="2026-03-20", amount="120.00"))

    assert db.fetch_all("SELECT name, amount, expected_date FROM one_time_items") == [
        ("Acme Car Repair", 120.0, date(2026, 3, 20))
    ]

    response = client.post(f"/one-time/{item}/delete", follow_redirects=False)

    assert response.headers["location"].startswith("/budget?")
    assert db.fetch_one("SELECT COUNT(*) FROM one_time_items")[0] == 0
    assert db.fetch_one("SELECT COUNT(*) FROM expected_reconciliations")[0] == 0
    assert db.fetch_one("SELECT COUNT(*) FROM expected_match_rules")[0] == 0


def test_pausing_and_deleting_a_bill(client, db, user_id, accounts):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    tx = add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 1, 10), "ACME UTIL", -40.0)
    db.confirm_reconciliation(user_id, "bill", bill, tx)
    db.save_amount_override(bill, date(2026, 6, 1), 50.0)

    client.post(f"/bills/{bill}/toggle")
    assert db.fetch_one("SELECT active FROM bills WHERE id = ?", [bill])[0] is False

    client.post(f"/bills/{bill}/delete")
    assert db.fetch_one("SELECT COUNT(*) FROM bills")[0] == 0
    assert db.fetch_one("SELECT COUNT(*) FROM expected_reconciliations")[0] == 0
    assert db.fetch_one("SELECT COUNT(*) FROM amount_overrides")[0] == 0


def test_another_users_bill_is_out_of_reach(client, db):
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
    theirs = add_bill(db, other, "Their Rent", 800.0, date(2026, 1, 3))
    their_item = add_one_time(db, other, "Their Gift", 50.0, date(2026, 2, 2))

    assert "err=1" in client.get(f"/bills/{theirs}/edit", follow_redirects=False).headers["location"]
    client.post(f"/bills/{theirs}/edit", data=bill_form(name="Mine now"))
    client.post(f"/bills/{theirs}/toggle")
    client.post(f"/bills/{theirs}/delete")
    client.post(f"/one-time/{their_item}/edit", data=bill_form(name="Mine now", repeats="one-time"))
    client.post(f"/one-time/{their_item}/delete")

    assert db.fetch_all("SELECT name, active FROM bills") == [("Their Rent", True)]
    assert db.fetch_all("SELECT name FROM one_time_items") == [("Their Gift",)]


# --- Prefill ------------------------------------------------------------------

def test_the_form_opens_with_a_category_kind_and_repeats_from_the_query(client, db, user_id):
    housing = db.create_category(user_id, "Housing")

    html = client.get(f"/bills/new?category_id={housing}&kind=income&repeats=weekly").text

    assert field(html, "category_id") == str(housing)
    assert field(html, "kind") == "income"
    assert field(html, "repeats") == "weekly"


def test_names_and_amounts_in_the_query_are_ignored(client):
    html = client.get("/bills/new?name=Planted&amount=999&first_due_date=2020-01-01").text

    assert "Planted" not in html and "999" not in html
    assert field(html, "name") == "" and field(html, "amount") == ""


def test_the_form_opens_filled_from_a_transaction_and_saving_links_it(client, db, user_id, accounts):
    dining = db.create_category(user_id, "Dining")
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 3, 4), "ACME DINER", -42.5)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])

    html = client.get(f"/bills/new?from_tx={tx}").text

    assert field(html, "name") == "Acme Diner"
    assert field(html, "amount") == "42.50"
    assert field(html, "kind") == "expense"
    assert field(html, "account_id") == str(accounts["Example Card"])
    assert field(html, "category_id") == str(dining)
    assert field(html, "first_due_date") == "2026-03-04"
    assert field(html, "from_tx") == str(tx)

    client.post("/bills", data=bill_form(name="Acme Diner", amount="42.50", first_due_date="2026-03-04", from_tx=str(tx)))

    bill = db.fetch_one("SELECT id FROM bills")[0]
    assert db.fetch_all("SELECT imported_transaction_id, source_type, source_id FROM expected_reconciliations") == [
        (tx, "bill", bill)
    ]


def test_a_one_time_item_made_from_a_transaction_is_linked_too(client, db, user_id, accounts):
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 3, 4), "ACME DINER", -42.5)

    client.post("/one-time", data=bill_form(name="Acme Diner", amount="42.50", repeats="one-time",
                                            first_due_date="2026-03-04", from_tx=str(tx)))

    item = db.fetch_one("SELECT id FROM one_time_items")[0]
    assert db.fetch_all("SELECT imported_transaction_id, source_type, source_id FROM expected_reconciliations") == [
        (tx, "one_time", item)
    ]


def test_someone_elses_transaction_fills_nothing(client, db, accounts):
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
    tx = add_imported(db, other, None, "Their Card", date(2026, 3, 4), "THEIR SECRET SHOP", -42.5)

    html = client.get(f"/bills/new?from_tx={tx}").text
    client.post("/bills", data=bill_form(from_tx=str(tx)))

    assert "secret" not in html.lower() and field(html, "name") == ""
    assert db.fetch_one("SELECT COUNT(*) FROM expected_reconciliations")[0] == 0


def test_the_form_opens_filled_from_a_suggestion_and_saving_marks_it_added(client, db, user_id, accounts):
    for months in (4, 3, 2, 1):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY - timedelta(days=30 * months),
                     "ACME STREAMING", -10.0)
    suggestion = db.detect_bill_suggestions(user_id)[0]

    html = client.get(f"/bills/new?from_suggestion={suggestion['token']}&next=/inbox%23bills").text

    assert field(html, "name") == suggestion["name"]
    assert field(html, "amount") == "10.00"
    assert field(html, "repeats") == suggestion["frequency_type"]
    assert field(html, "first_due_date") == suggestion["first_due_date"]
    assert field(html, "account_id") == str(accounts["Example Card"])
    assert field(html, "next") == "/inbox#bills"
    assert suggestion["key"] not in f"/bills/new?from_suggestion={suggestion['token']}"

    response = client.post("/bills", data=bill_form(name=suggestion["name"], amount="10.00",
                                                    first_due_date=suggestion["first_due_date"],
                                                    from_suggestion=suggestion["token"], next="/inbox#bills"),
                           follow_redirects=False)

    assert "undo=" in response.headers["location"]
    assert db.detect_bill_suggestions(user_id) == []
    assert inbox.suggestion_added(user_id, suggestion["key"])


def test_a_suggestion_starts_on_the_latest_date_that_falls_on_its_usual_day(db, user_id, accounts):
    card = accounts["Example Card"]
    for when in (date(2026, 1, 12), date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 12), date(2026, 5, 14)):
        add_imported(db, user_id, card, "Example Card", when, "ACME STREAMING", -10.0)

    suggestion = db.detect_bill_suggestions(user_id, lookback_days=100000)[0]

    assert suggestion["first_due_date"] == "2026-04-12"
    assert "day_of_month" not in suggestion and "start_date" not in suggestion


# --- Routes that are gone, and the ones kept ------------------------------------

@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/recurring"), ("post", "/recurring"), ("get", "/recurring/1/edit"), ("post", "/recurring/1/toggle"),
        ("post", "/manual"), ("get", "/manual/1/edit"), ("post", "/manual/1/delete"),
        ("post", "/expected/one-time"), ("post", "/expected/one-time/1/delete"),
        ("get", "/expected/recurring/1/reconcile"), ("get", "/expected/one_time/1/reconcile"),
        ("get", "/expected/bill/1/reconcile"),
    ],
)
def test_the_old_routes_are_gone(client, method, path):
    assert getattr(client, method)(path, follow_redirects=False).status_code in (404, 405)


def test_expected_and_review_still_redirect(client):
    assert client.get("/expected", follow_redirects=False).headers["location"] == "/budget"
    assert client.get("/review", follow_redirects=False).headers["location"] == "/inbox"


def test_each_item_has_its_own_reconcile_page(client, db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))
    item = add_one_time(db, user_id, "Acme Repair", 100.0, date(2026, 3, 14))

    assert "acme utilities" in client.get(f"/bills/{bill}/reconcile").text.lower()
    assert "acme repair" in client.get(f"/one-time/{item}/reconcile").text.lower()
    assert "err=1" in client.get("/bills/999/reconcile", follow_redirects=False).headers["location"]
