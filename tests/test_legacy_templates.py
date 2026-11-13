"""Someone who imported with the old built-in formats keeps importing the same
files: their layouts become templates the user owns, once, and each account that
clearly used one gets it as its default.

All data here is synthetic.
"""

from datetime import date

import import_templates as it
from conftest import add_imported

LEGACY = ("Card export", "Checking and Visa export")


def imported(db, user_id, account_id, raw_type):
    tx = add_imported(db, user_id, account_id, "Example", date(2026, 7, 1), f"ACME {raw_type}", -1.0)
    db.execute("UPDATE imported_transactions SET raw_type = ? WHERE id = ?", [raw_type, tx])


def restart(db):
    db.init_db()


def upgrade(db):
    """Start as a database from before templates replaced the built-in formats."""
    db.execute("DELETE FROM applied_migrations WHERE name = 'legacy_templates'")
    db.init_db()


def names(user_id):
    return [t["name"] for t in it.load_templates(user_id)]


def test_users_with_imports_get_the_old_layouts_as_templates(db, user_id, accounts):
    imported(db, user_id, accounts["Example Checking"], "Debit")
    imported(db, user_id, accounts["Example Card"], "Purchase")

    upgrade(db)

    assert names(user_id) == list(LEGACY)
    by_name = {t["name"]: t["id"] for t in it.load_templates(user_id)}
    assert db.load_account_by_id(user_id, accounts["Example Checking"])["default_template_id"] == \
        by_name["Checking and Visa export"]
    assert db.load_account_by_id(user_id, accounts["Example Card"])["default_template_id"] == by_name["Card export"]


def test_a_card_imported_with_credit_and_debit_rows_uses_the_shared_layout(db, user_id, accounts):
    imported(db, user_id, accounts["Example Card"], "Debit")

    upgrade(db)

    default = db.load_account_by_id(user_id, accounts["Example Card"])["default_template_id"]
    assert it.load_template(user_id, default)["name"] == "Checking and Visa export"


def test_it_happens_once_even_after_the_user_deletes_them(db, user_id, accounts):
    imported(db, user_id, accounts["Example Checking"], "Debit")
    upgrade(db)
    restart(db)
    assert names(user_id) == list(LEGACY)

    for t in it.load_templates(user_id):
        it.delete_template(user_id, t["id"])
    restart(db)

    assert names(user_id) == []


def test_users_without_imports_get_none(db, user_id, accounts):
    upgrade(db)

    assert names(user_id) == []


def test_the_legacy_layouts_read_their_old_files(db, user_id, accounts):
    imported(db, user_id, accounts["Example Checking"], "Debit")
    upgrade(db)
    by_name = {t["name"]: t for t in it.load_templates(user_id)}
    checking = db.load_account_by_id(user_id, accounts["Example Checking"])
    card = db.load_account_by_id(user_id, accounts["Example Card"])

    shared = it.parse_rows(*it.read_csv(
        b"Account Name,Processed Date,Description,Check Number,Credit or Debit,Amount\n"
        b"Example,2026-07-01,ACH ACME UTILITIES TYPE: BILL,,Debit,40.00\n"), by_name["Checking and Visa export"], checking)
    cleared = it.parse_rows(*it.read_csv(
        b"Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD)\n"
        b"07/30/2026,,Acme Store,Acme Store,Shopping,Purchase,20.00\n"), by_name["Card export"], card)

    assert (shared[0]["amount"], shared[0]["merchant"]) == (-40.0, "ACME UTILITIES")
    assert (cleared[0]["amount"], cleared[0]["tx_date"]) == (-20.0, date(2026, 7, 30))


def test_a_user_who_imports_after_the_change_gets_none(db, user_id, accounts):
    restart(db)  # the first start with templates; nothing imported yet
    imported(db, user_id, accounts["Example Checking"], "Debit")

    restart(db)

    assert names(user_id) == []
