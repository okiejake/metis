"""An export names the account in the bank's words, not the user's. Metis has to
land those rows on the right account without asking again every single import:
once by hand is a setup step, every time is a chore.

All data here is synthetic.
"""

from datetime import date


def add_card(db, user_id, name):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO accounts (user_id, name, account_type, statement_day, due_day) "
            "VALUES (?, ?, 'credit_card', 0, 0)",
            [user_id, name],
        )
        row = conn.execute(
            "SELECT id FROM accounts WHERE user_id = ? AND name = ?", [user_id, name]
        ).fetchone()
    return int(row[0])


def test_exact_name_still_wins(db, user_id, accounts):
    resolved = db.resolve_import_account_id(user_id, "Example Card")

    assert resolved == accounts["Example Card"]


def test_partial_name_match_bridges_the_bank_s_wording(db, user_id):
    """"Acme Card" from the export and "Acme Credit Card" in the app are the same
    account; the words that differ only say what kind of thing it is."""
    card_id = add_card(db, user_id, "Acme Credit Card")

    assert db.resolve_import_account_id(user_id, "Acme Card") == card_id


def test_partial_match_gives_up_when_two_accounts_fit(db, user_id):
    """Guessing between two cards would silently hand one card the other's
    history, which is worse than asking."""
    add_card(db, user_id, "Acme Credit Card")
    add_card(db, user_id, "Acme Rewards Card")

    assert db.resolve_import_account_id(user_id, "Acme Card") is None


def test_unrelated_label_is_left_for_the_user(db, user_id):
    add_card(db, user_id, "Example Bank Credit Card")

    assert db.resolve_import_account_id(user_id, "Visa") is None


def test_mapping_a_label_by_hand_is_remembered(db, user_id):
    """The fix for the label that cannot be guessed: map it once, and the next
    import of that same label anchors itself."""
    card_id = add_card(db, user_id, "Example Bank Credit Card")
    assert db.resolve_import_account_id(user_id, "Visa") is None

    db.map_import_label_to_account(user_id, "Visa", card_id)

    assert db.resolve_import_account_id(user_id, "Visa") == card_id


def test_a_saved_link_outranks_an_incidental_name_match(db, user_id, accounts):
    """The saved link is the user's own decision about this label."""
    other_id = add_card(db, user_id, "Acme Credit Card")
    db.map_import_label_to_account(user_id, "Example Card", other_id)

    assert db.resolve_import_account_id(user_id, "Example Card") == other_id


def test_forgetting_a_link_asks_again(db, user_id):
    card_id = add_card(db, user_id, "Example Bank Credit Card")
    db.map_import_label_to_account(user_id, "Visa", card_id)

    db.delete_import_account_link(user_id, "Visa")

    assert db.resolve_import_account_id(user_id, "Visa") is None


def test_a_link_to_a_deleted_account_is_ignored(db, user_id):
    card_id = add_card(db, user_id, "Example Bank Credit Card")
    db.map_import_label_to_account(user_id, "Visa", card_id)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", [card_id])

    assert db.resolve_import_account_id(user_id, "Visa") is None


def test_import_anchors_rows_without_a_second_step(db, user_id):
    """End to end: the built-in Apple parser labels rows "Apple Card"; the user
    named the account something longer. The rows still land anchored, and
    labelled in the user's words."""
    card_id = add_card(db, user_id, "Household Apple Card")
    _label, _account_id, rows = db.parse_import_csv(
        "export.csv",
        "Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD)\n"
        "09/10/2026,09/11/2026,Acme Store,Acme Store,Shopping,Purchase,20.00\n",
    )

    db.upsert_imported_transactions(user_id, rows, "export.csv")

    stored = db.query_all(
        "SELECT account, account_id, tx_date FROM imported_transactions WHERE user_id = ?",
        [user_id],
    )
    assert len(stored) == 1
    assert stored[0]["account_id"] == card_id, "anchored with no manual mapping step"
    assert stored[0]["account"] == "Household Apple Card", "labelled in the user's words"
    assert stored[0]["tx_date"] == date(2026, 9, 11)


def test_reimporting_the_same_export_adds_nothing(db, user_id):
    """Anchoring must not change the dedup key, or every re-upload would double."""
    add_card(db, user_id, "Household Apple Card")
    csv_text = (
        "Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD)\n"
        "09/10/2026,09/11/2026,Acme Store,Acme Store,Shopping,Purchase,20.00\n"
    )
    _label, _account_id, rows = db.parse_import_csv("export.csv", csv_text)
    db.upsert_imported_transactions(user_id, rows, "export.csv")

    _label, _account_id, again = db.parse_import_csv("export.csv", csv_text)
    result = db.upsert_imported_transactions(user_id, again, "export.csv")

    assert result["inserted"] == 0
    assert result["skipped"] == 1
