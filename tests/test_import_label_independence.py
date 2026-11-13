"""A row's stored label is whatever named the account when it was imported, and
that changes: rows imported by the old built-in formats carry the format's label,
while rows imported through a template carry the account's own name. None of
that makes a transaction a different transaction. If the label were part of what
makes a row "already imported", moving to templates would add an account's whole
history a second time.

All data here is synthetic.
"""

from datetime import date

EXPORT = "export.csv"


def row(account_id, label, when, description, amount):
    """One parsed row, in the shape the parser hands to the importer."""
    return {
        "account": label,
        "account_id": account_id,
        "tx_date": when,
        "description": description,
        "merchant": description,
        "amount": amount,
        "flow": "expense",
        "is_transfer": False,
        "raw_type": "Debit",
    }


def history(account_id, label):
    return [
        row(account_id, label, date(2026, 7, 10), "ACME HARDWARE", -40.00),
        row(account_id, label, date(2026, 7, 12), "ACME GROCER", -25.50),
    ]


def stored(db, user_id):
    return db.query_all(
        "SELECT account_id, description FROM imported_transactions WHERE user_id = ? ORDER BY tx_date, id",
        [user_id],
    )


def test_history_is_not_added_again_when_the_label_changes(db, user_id, accounts):
    card = accounts["Example Card"]
    db.upsert_imported_transactions(user_id, history(card, "Old Label"), EXPORT)

    result = db.upsert_imported_transactions(user_id, history(card, "Example Card"), EXPORT)

    assert result["inserted"] == 0
    assert len(stored(db, user_id)) == 2


def test_a_genuine_second_identical_charge_is_still_added(db, user_id, accounts):
    """Two identical purchases on one day are two charges. Recognizing the first
    as already imported must not swallow the second."""
    card = accounts["Example Card"]
    coffee = (date(2026, 7, 10), "ACME COFFEE", -4.50)
    db.upsert_imported_transactions(user_id, [row(card, "Old Label", *coffee)], EXPORT)

    result = db.upsert_imported_transactions(
        user_id, [row(card, "Example Card", *coffee), row(card, "Example Card", *coffee)], EXPORT
    )

    assert result["inserted"] == 1
    assert len(stored(db, user_id)) == 2


def test_rows_land_on_the_account_they_carry_in_its_own_name(db, user_id, accounts):
    card = accounts["Example Card"]

    db.upsert_imported_transactions(user_id, history(card, "Whatever The File Said"), EXPORT)

    assert {r["account_id"] for r in stored(db, user_id)} == {card}
    assert db.fetch_one("SELECT DISTINCT account FROM imported_transactions")[0] == "Example Card"
