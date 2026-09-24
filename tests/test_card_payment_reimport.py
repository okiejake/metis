"""Re-importing an export must never add a card payment a second time.

A row's dedup key hashes its signed amount. When card payments moved from being
stored negative to positive, migrate_card_payment_signs flipped the old rows but
left them keyed by their old negative amount — so the next export covering those
dates computed a different key and inserted each payment again. A duplicated
payment pays the card down twice, and every statement after it reads as
overpaid by that amount.

All data here is synthetic.
"""

from datetime import date

import pytest

EXPORT = "export.csv"


def visa_payment(when="2026-07-15", amount="250.00"):
    return {
        "Processed Date": when,
        "Description": "PAYMENT - THANK YOU",
        "Credit or Debit": "Credit",
        "Amount": amount,
    }


def visa_purchase(when="2026-07-10", amount="40.00"):
    return {
        "Processed Date": when,
        "Description": "ACME HARDWARE",
        "Credit or Debit": "Debit",
        "Amount": amount,
    }


@pytest.fixture
def card_id(db, user_id, accounts):
    """The built-in Visa parser labels rows 'Visa'; bind that to the card."""
    db.save_import_account_link(user_id, "Visa", accounts["Example Card"])
    return accounts["Example Card"]


def import_rows(db, user_id, *raw_rows):
    return db.upsert_imported_transactions(
        user_id, [db._parse_visa_row(row) for row in raw_rows], EXPORT
    )


def import_the_old_way(db, user_id, *raw_rows):
    """What the importer did before the sign fix: payments stored negative, and
    keyed by that negative amount. The startup repair then flips the sign."""
    rows = [db._parse_visa_row(row) for row in raw_rows]
    for row in rows:
        if row["is_transfer"]:
            row["amount"] = -row["amount"]
    db.upsert_imported_transactions(user_id, rows, EXPORT)
    with db.get_connection() as conn:
        db.migrate_card_payment_signs(conn)


def payments_on(db, user_id, card_id):
    return db.fetch_all(
        "SELECT tx_date, amount FROM imported_transactions "
        "WHERE user_id = ? AND account_id = ? AND is_transfer ORDER BY tx_date",
        [user_id, card_id],
    )


def test_reimport_skips_a_payment_stored_under_its_old_key(db, user_id, card_id):
    import_the_old_way(db, user_id, visa_purchase(), visa_payment())

    result = import_rows(db, user_id, visa_purchase(), visa_payment())

    assert result["inserted"] == 0, "the payment was already imported, just keyed by its old sign"
    assert payments_on(db, user_id, card_id) == [(date(2026, 7, 15), 250.00)]


def test_startup_repair_rekeys_old_payments(db, user_id, card_id):
    import_the_old_way(db, user_id, visa_payment())

    with db.get_connection() as conn:
        outcome = db.migrate_card_payment_fingerprints(conn)

    assert outcome == {"rekeyed": 1, "removed": 0}
    stored = db.fetch_one(
        "SELECT fingerprint FROM imported_transactions WHERE user_id = ? AND account_id = ?",
        [user_id, card_id],
    )[0]
    fresh = db._parse_visa_row(visa_payment())
    db._assign_import_fingerprints([fresh])
    assert stored == fresh["fingerprint"], "re-keyed to what an import computes today"


def test_startup_repair_removes_a_payment_already_imported_twice(db, user_id, card_id):
    import_the_old_way(db, user_id, visa_payment())
    # A re-import from before this fix: the fresh key missed, so it went in again.
    rows = [db._parse_visa_row(visa_payment())]
    db._assign_import_fingerprints(rows)
    rows[0].pop("legacy_fingerprint")
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO imported_transactions (user_id, account, account_id, tx_date, description, "
            "merchant, amount, flow, is_transfer, raw_type, source_file, fingerprint) "
            "VALUES (?, 'Example Card', ?, ?, ?, ?, ?, ?, ?, ?, 'later.csv', ?)",
            [user_id, card_id, rows[0]["tx_date"], rows[0]["description"], rows[0]["merchant"],
             rows[0]["amount"], rows[0]["flow"], True, rows[0]["raw_type"], rows[0]["fingerprint"]],
        )
    assert len(payments_on(db, user_id, card_id)) == 2

    with db.get_connection() as conn:
        outcome = db.migrate_card_payment_fingerprints(conn)

    assert outcome == {"rekeyed": 0, "removed": 1}
    assert payments_on(db, user_id, card_id) == [(date(2026, 7, 15), 250.00)]


def test_startup_repair_is_idempotent(db, user_id, card_id):
    import_the_old_way(db, user_id, visa_payment())
    with db.get_connection() as conn:
        db.migrate_card_payment_fingerprints(conn)
        second = db.migrate_card_payment_fingerprints(conn)

    assert second == {"rekeyed": 0, "removed": 0}


def test_a_genuine_second_payment_is_kept(db, user_id, card_id):
    """Same amount, different day: two real payments, not a duplicate."""
    import_the_old_way(db, user_id, visa_payment("2026-07-15"))
    import_rows(db, user_id, visa_payment("2026-07-18"))

    with db.get_connection() as conn:
        db.migrate_card_payment_fingerprints(conn)

    assert payments_on(db, user_id, card_id) == [
        (date(2026, 7, 15), 250.00),
        (date(2026, 7, 18), 250.00),
    ]
