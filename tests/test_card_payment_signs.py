"""A payment to a credit card is money *in* to that card: it must be stored
positive, exactly like a refund, because that is what pays the balance down. Two
importers stored it negative instead, which under this app's sign convention reads
as "owe more" — so a card's balance climbed with every payment made against it.

Nothing caught it for a long time because every consumer took ABS() of a payment.
Anything that derives a real balance (what a statement actually closed at) does
not have that luxury, so these tests pin the sign at the point of import and pin
the repair of rows imported before the fix.

All data here is synthetic.
"""

from datetime import date

from conftest import add_imported

def visa_row(description, credit_or_debit, amount="250.00"):
    return {
        "Processed Date": "2026-07-15",
        "Description": description,
        "Credit or Debit": credit_or_debit,
        "Amount": amount,
    }


def card_template(accounts):
    return {
        "account_name": "Example Card",
        "account_id": accounts["Example Card"],
        "account_type": "credit_card",
        "date_field": "Date",
        "date_format": "%Y-%m-%d",
        "amount_field": "Amount",
        "amount_sign": "standard",
        "type_field": "Type",
        "credit_value": "Credit",
        "description_field": "Description",
        "merchant_field": None,
    }


def template_row(description, row_type, amount="250.00"):
    return {
        "Date": "2026-07-15",
        "Description": description,
        "Type": row_type,
        "Amount": amount,
    }


def test_visa_payment_is_stored_positive(db):
    parsed = db._parse_visa_row(visa_row("PAYMENT - THANK YOU", "Credit"))

    assert parsed["amount"] == 250.00, "a payment pays the balance down, so it is money in"
    assert parsed["is_transfer"] is True


def test_visa_refund_and_purchase_keep_their_signs(db):
    refund = db._parse_visa_row(visa_row("ACME STORE REFUND", "Credit"))
    purchase = db._parse_visa_row(visa_row("ACME STORE", "Debit"))

    assert refund["amount"] == 250.00
    assert refund["is_transfer"] is False
    assert purchase["amount"] == -250.00


def test_template_payment_to_a_card_is_stored_positive(db, accounts):
    parsed = db._parse_template_row(template_row("AUTOMATIC PAYMENT - THANK YOU", "Credit"), card_template(accounts))

    assert parsed["amount"] == 250.00
    assert parsed["is_transfer"] is True
    assert parsed["flow"] == "transfer"


def test_template_purchase_on_a_card_stays_negative(db, accounts):
    parsed = db._parse_template_row(template_row("ACME HARDWARE", "Debit"), card_template(accounts))

    assert parsed["amount"] == -250.00
    assert parsed["is_transfer"] is False


def test_a_run_of_payments_reduces_the_balance(db, user_id, accounts):
    """The property that actually matters: paying a card must make it owe less."""
    card = [a for a in db.load_accounts(user_id) if a["name"] == "Example Card"][0]
    add_imported(db, user_id, card["id"], "Example Card", date(2026, 7, 1), "Acme Hardware", -300.00)
    before = db._card_actual_net_through(user_id, card, date(2026, 7, 31))

    payment = db._parse_visa_row(visa_row("PAYMENT - THANK YOU", "Credit", "100.00"))
    add_imported(db, user_id, card["id"], "Example Card", date(2026, 7, 15),
                 payment["description"], payment["amount"], flow=payment["flow"], is_transfer=True)

    after = db._card_actual_net_through(user_id, card, date(2026, 7, 31))
    assert after > before, "a payment must move the balance toward zero, not away"
    assert after == -200.00


def repair(db):
    with db.get_connection() as conn:
        return db.migrate_card_payment_signs(conn)


def test_migration_repairs_payments_imported_as_debits(db, user_id, accounts):
    card_id = accounts["Example Card"]
    add_imported(db, user_id, card_id, "Example Card", date(2026, 7, 15),
                 "AUTOMATIC PAYMENT - THANK YOU", -250.00, flow="transfer", is_transfer=True)
    with db.get_connection() as conn:
        conn.execute("UPDATE imported_transactions SET raw_type = 'Credit' WHERE amount = -250.00")

    assert repair(db) == 1

    row = db.fetch_one("SELECT amount FROM imported_transactions WHERE user_id = ?", [user_id])
    assert float(row[0]) == 250.00


def test_migration_leaves_a_genuine_negative_transfer_alone(db, user_id, accounts):
    """A payment reversal or cash-advance adjustment really does increase the
    balance. The issuer books it as a debit, and that is the tell."""
    card_id = accounts["Example Card"]
    add_imported(db, user_id, card_id, "Example Card", date(2026, 7, 15),
                 "PAYMENT REVERSAL - INSUFFICIENT FUNDS", -30.00, flow="transfer", is_transfer=True)
    with db.get_connection() as conn:
        conn.execute("UPDATE imported_transactions SET raw_type = 'Other' WHERE amount = -30.00")

    assert repair(db) == 0

    row = db.fetch_one("SELECT amount FROM imported_transactions WHERE user_id = ?", [user_id])
    assert float(row[0]) == -30.00


def test_migration_is_idempotent(db, user_id, accounts):
    card_id = accounts["Example Card"]
    add_imported(db, user_id, card_id, "Example Card", date(2026, 7, 15),
                 "AUTOMATIC PAYMENT - THANK YOU", -250.00, flow="transfer", is_transfer=True)
    with db.get_connection() as conn:
        conn.execute("UPDATE imported_transactions SET raw_type = 'Credit' WHERE amount = -250.00")

    assert repair(db) == 1
    assert repair(db) == 0, "re-running must not flip the repaired row back"

    row = db.fetch_one("SELECT amount FROM imported_transactions WHERE user_id = ?", [user_id])
    assert float(row[0]) == 250.00


def test_migration_ignores_checking_accounts(db, user_id, accounts):
    """A transfer out of checking to pay a card is money out and stays negative."""
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 7, 15),
                 "PAYMENT TO EXAMPLE CARD", -250.00, flow="transfer", is_transfer=True)
    with db.get_connection() as conn:
        conn.execute("UPDATE imported_transactions SET raw_type = 'Credit' WHERE amount = -250.00")

    assert repair(db) == 0

    row = db.fetch_one("SELECT amount FROM imported_transactions WHERE user_id = ?", [user_id])
    assert float(row[0]) == -250.00
