"""An export's label is the bank's wording for an account, and wording changes:
a bank renames a product, a user moves from a built-in format to a template of
their own, a built-in format is given a new name. None of that makes a
transaction a different transaction. If the label were part of what makes a row
"already imported", every such change would add an account's whole history a
second time.

All data here is synthetic.
"""

from datetime import date

EXPORT = "export.csv"


def row(label, when, description, amount):
    """One parsed row, in the shape every parser hands to the importer."""
    return {
        "account": label,
        "tx_date": when,
        "description": description,
        "merchant": description,
        "amount": amount,
        "flow": "expense",
        "is_transfer": False,
        "raw_type": "Debit",
    }


def history(label):
    return [
        row(label, date(2026, 7, 10), "ACME HARDWARE", -40.00),
        row(label, date(2026, 7, 12), "ACME GROCER", -25.50),
    ]


def stored(db, user_id):
    return db.query_all(
        "SELECT account_id, description FROM imported_transactions "
        "WHERE user_id = ? ORDER BY tx_date, id",
        [user_id],
    )


def test_history_is_not_added_again_when_the_label_changes(db, user_id, accounts):
    card_id = accounts["Example Card"]
    db.save_import_account_link(user_id, "Old Label", card_id)
    db.save_import_account_link(user_id, "New Label", card_id)
    db.upsert_imported_transactions(user_id, history("Old Label"), EXPORT)

    result = db.upsert_imported_transactions(user_id, history("New Label"), EXPORT)

    assert result["inserted"] == 0
    assert len(stored(db, user_id)) == 2


def test_a_genuine_second_identical_charge_is_still_added(db, user_id, accounts):
    """Two identical purchases on one day are two charges. Recognizing the first
    as already imported must not swallow the second."""
    card_id = accounts["Example Card"]
    db.save_import_account_link(user_id, "Old Label", card_id)
    db.save_import_account_link(user_id, "New Label", card_id)
    coffee = (date(2026, 7, 10), "ACME COFFEE", -4.50)
    db.upsert_imported_transactions(user_id, [row("Old Label", *coffee)], EXPORT)

    result = db.upsert_imported_transactions(
        user_id, [row("New Label", *coffee), row("New Label", *coffee)], EXPORT
    )

    assert result["inserted"] == 1
    assert len(stored(db, user_id)) == 2


def test_an_unplaced_export_is_recognized_by_the_history_it_shares(db, user_id, accounts):
    """An export whose label matches nothing still says which account it is: it
    carries transactions that account already holds. Only the new one is added,
    and it lands on that account rather than on no account at all."""
    card_id = accounts["Example Card"]
    db.save_import_account_link(user_id, "Old Label", card_id)
    db.upsert_imported_transactions(user_id, history("Old Label"), EXPORT)

    result = db.upsert_imported_transactions(
        user_id,
        history("Unplaced Label") + [row("Unplaced Label", date(2026, 7, 20), "ACME BOOKS", -15.00)],
        EXPORT,
    )

    assert result["inserted"] == 1
    assert stored(db, user_id) == [
        {"account_id": card_id, "description": "ACME HARDWARE"},
        {"account_id": card_id, "description": "ACME GROCER"},
        {"account_id": card_id, "description": "ACME BOOKS"},
    ]


def test_a_recognized_label_is_remembered(db, user_id, accounts):
    """Next month's export shares nothing with what is stored. Having placed the
    label once, Metis must not need the overlap again."""
    card_id = accounts["Example Card"]
    db.save_import_account_link(user_id, "Old Label", card_id)
    db.upsert_imported_transactions(user_id, history("Old Label"), EXPORT)
    db.upsert_imported_transactions(user_id, history("Unplaced Label"), EXPORT)

    db.upsert_imported_transactions(
        user_id, [row("Unplaced Label", date(2026, 8, 3), "ACME BOOKS", -15.00)], EXPORT
    )

    assert stored(db, user_id)[-1] == {"account_id": card_id, "description": "ACME BOOKS"}


def unanchored(db, user_id):
    return db.query_all(
        "SELECT description FROM imported_transactions "
        "WHERE user_id = ? AND account_id IS NULL ORDER BY tx_date, id",
        [user_id],
    )


def test_history_held_by_two_accounts_is_not_guessed_between(db, user_id, accounts):
    """When two accounts hold the same transactions the overlap says nothing
    about which one the export is. Picking one would hand it the other's history,
    so the rows wait for the user to place them."""
    db.save_import_account_link(user_id, "Card Label", accounts["Example Card"])
    db.save_import_account_link(user_id, "Checking Label", accounts["Example Checking"])
    db.upsert_imported_transactions(user_id, history("Card Label"), EXPORT)
    db.upsert_imported_transactions(user_id, history("Checking Label"), EXPORT)

    db.upsert_imported_transactions(
        user_id, [row("Unplaced Label", date(2026, 7, 10), "ACME HARDWARE", -40.00)], EXPORT
    )

    assert unanchored(db, user_id) == [{"description": "ACME HARDWARE"}]


def test_one_coincidence_does_not_claim_a_whole_export(db, user_id, accounts):
    """A single matching row among several is as likely chance as history — the
    same subscription price on the same day on two cards. That is not enough to
    put a whole export on an account."""
    db.save_import_account_link(user_id, "Old Label", accounts["Example Card"])
    db.upsert_imported_transactions(
        user_id, [row("Old Label", date(2026, 7, 10), "ACME STREAMING", -9.99)], EXPORT
    )

    db.upsert_imported_transactions(
        user_id,
        [
            row("Unplaced Label", date(2026, 7, 10), "ACME STREAMING", -9.99),
            row("Unplaced Label", date(2026, 7, 11), "ACME BOOKS", -15.00),
            row("Unplaced Label", date(2026, 7, 12), "ACME GROCER", -25.50),
        ],
        EXPORT,
    )

    assert unanchored(db, user_id) == [
        {"description": "ACME STREAMING"},
        {"description": "ACME BOOKS"},
        {"description": "ACME GROCER"},
    ]
