"""A card's statements are paid from one checking account: its paid-from
account (v0.5.1). An empty paid-from means the default checking account, the
user's checking account with the lowest id, so nothing moves on upgrade.

All data here is synthetic.
"""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


def add_checking(db, user_id, name):
    row = db.fetch_one(
        "INSERT INTO accounts (user_id, name, account_type, payment_pattern) VALUES (?, ?, 'checking', '') RETURNING id",
        [user_id, name],
    )
    return int(row[0])


def paid_from(db, account_id):
    return db.fetch_one("SELECT paid_from_account_id FROM accounts WHERE id = ?", [account_id])[0]


def edit(client, account, **changes):
    form = {"name": account["name"], "account_type": account["account_type"],
            "statement_day": str(account["statement_day"] or ""), "due_day": str(account["due_day"] or ""),
            "payment_pattern": account.get("payment_pattern") or ""}
    form.update(changes)
    return client.post(f"/accounts/{account['id']}/edit", data=form, follow_redirects=False)


# --- The column and the upgrade ------------------------------------------------


def test_a_new_database_gives_accounts_a_paid_from_column(db):
    columns = [row[1] for row in db.fetch_all("PRAGMA table_info('accounts')")]
    assert "paid_from_account_id" in columns


def test_the_upgrade_points_each_card_at_the_only_checking_account(v040):
    v040.init_db()
    rows = dict(v040.fetch_all("SELECT name, paid_from_account_id FROM accounts WHERE user_id = 1"))
    checking_id = v040.fetch_one("SELECT id FROM accounts WHERE name = 'Example Checking'")[0]
    assert rows["Example Card"] == checking_id
    assert rows["Example Checking"] is None


def test_the_upgrade_leaves_paid_from_empty_when_there_are_two_checking_accounts(v040):
    with v040.get_connection() as conn:
        conn.execute("INSERT INTO accounts (user_id, name, account_type, payment_pattern) "
                     "VALUES (1, 'Example Second Checking', 'checking', '')")
    v040.init_db()
    assert v040.fetch_one("SELECT paid_from_account_id FROM accounts WHERE name = 'Example Card'")[0] is None


def test_the_upgrade_keeps_every_account_its_id_and_its_unique_name(v040):
    query = ("SELECT id, user_id, name, account_type, statement_day, due_day, payment_pattern, default_template_id "
             "FROM accounts ORDER BY id")
    before = v040.fetch_all(query)
    v040.init_db()
    assert v040.fetch_all(query) == before
    with pytest.raises(Exception):
        v040.execute("INSERT INTO accounts (user_id, name, account_type) VALUES (1, 'Example Checking', 'checking')")


def test_a_new_account_takes_the_next_id_after_the_upgrade(v040):
    highest = v040.fetch_one("SELECT MAX(id) FROM accounts")[0]
    v040.init_db()
    new_id = v040.fetch_one("INSERT INTO accounts (user_id, name, account_type) "
                            "VALUES (1, 'Example New Checking', 'checking') RETURNING id")[0]
    assert new_id == highest + 1


# --- Which account pays a card ---------------------------------------------------


def test_the_default_checking_account_is_the_one_with_the_lowest_id(db, user_id, accounts):
    add_checking(db, user_id, "Example Second Checking")
    assert db.default_checking_account(user_id)["id"] == accounts["Example Checking"]


def test_a_user_with_no_checking_account_has_no_default(db, user_id):
    assert db.default_checking_account(user_id) is None


def test_an_empty_paid_from_means_the_default_checking_account(db, user_id, accounts):
    card = db.load_account_by_id(user_id, accounts["Example Card"])
    assert db.paying_account(user_id, card)["id"] == accounts["Example Checking"]


def test_a_card_is_paid_from_the_account_it_names(db, user_id, accounts):
    second = add_checking(db, user_id, "Example Second Checking")
    db.execute("UPDATE accounts SET paid_from_account_id = ? WHERE id = ?", [second, accounts["Example Card"]])
    card = db.load_account_by_id(user_id, accounts["Example Card"])
    assert db.paying_account(user_id, card)["id"] == second


# --- The account edit form -------------------------------------------------------


def test_the_edit_form_saves_a_cards_paid_from(client, db, user_id, accounts):
    second = add_checking(db, user_id, "Example Second Checking")
    card = db.load_account_by_id(user_id, accounts["Example Card"])
    response = edit(client, card, paid_from=str(second))
    assert response.status_code == 303
    assert paid_from(db, accounts["Example Card"]) == second


def test_the_edit_form_refuses_a_paid_from_that_is_not_the_users_checking_account(client, db, user_id, accounts):
    card = db.load_account_by_id(user_id, accounts["Example Card"])
    response = edit(client, card, paid_from=str(accounts["Example Card"]))
    assert "err=1" in response.headers["location"]
    assert paid_from(db, accounts["Example Card"]) is None


def test_a_checking_account_never_keeps_a_paid_from(client, db, user_id, accounts):
    checking = db.load_account_by_id(user_id, accounts["Example Checking"])
    second = add_checking(db, user_id, "Example Second Checking")
    edit(client, checking, paid_from=str(second))
    assert paid_from(db, accounts["Example Checking"]) is None


def test_the_edit_page_offers_the_users_checking_accounts_for_a_card(client, db, user_id, accounts):
    page = client.get(f"/accounts/{accounts['Example Card']}/edit").text
    assert 'name="paid_from"' in page
    assert f'value="{accounts["Example Checking"]}"' in page
