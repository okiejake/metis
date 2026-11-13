"""Some card issuers bill a statement by when a charge *clears*, not when it was
made, and the two routinely straddle a month end. Filing a charge under the transaction date
pushes it into the statement before the one it really belongs to, which shows up
later as a statement the data calls underpaid even though it was settled in full.

The other half of the same bug: a balance adjustment that *increases* what is owed
is not a payment, and counting it as one cancels out real payments beside it.

All data here is synthetic.
"""

from datetime import date

import import_templates as it
from conftest import add_imported


CARD = {"id": 2, "name": "Example Card", "account_type": "credit_card"}
# A card export with a transaction date and a clearing date, purchases positive.
CLEARED = it.build_template({
    "name": "Example cleared export", "date_field": "Clearing Date", "date_fallback_field": "Transaction Date",
    "description_field": "Description", "merchant_field": "Merchant", "type_field": "Type",
    "amount_layout": "signed", "amount_field": "Amount (USD)", "amount_sign": "inverted"})


def cleared_row(txn_date, clearing_date, amount="20.00", row_type="Purchase", merchant="Acme Store"):
    return it.parse_row({
        "Transaction Date": txn_date,
        "Clearing Date": clearing_date,
        "Description": merchant,
        "Merchant": merchant,
        "Type": row_type,
        "Amount (USD)": amount,
    }, CLEARED, CARD)


def test_charge_is_filed_on_the_date_it_cleared():
    """A month-end purchase that clears in the next month belongs to the next
    statement — the whole point of the clearing date."""
    assert cleared_row("07/31/2026", "08/02/2026")["tx_date"] == date(2026, 8, 2)


def test_pending_charge_falls_back_to_the_transaction_date():
    """A charge that has not cleared yet has no clearing date; it still has to
    land somewhere rather than blowing up the import."""
    assert cleared_row("09/14/2026", "")["tx_date"] == date(2026, 9, 14)


def test_adjustment_that_increases_the_balance_is_not_a_payment():
    """An adjustment that adds to what is owed is a charge. Calling it a transfer
    would let it count against a statement as a negative payment."""
    parsed = cleared_row("08/19/2026", "08/19/2026", "0.16", "Other")

    assert parsed["amount"] == -0.16, "adds to the balance, so it is signed like a charge"
    assert parsed["is_transfer"] is False


def test_a_payment_row_is_a_transfer_by_its_type():
    parsed = cleared_row("08/19/2026", "08/19/2026", "-500.00", "Payment", merchant="Online")

    assert parsed["amount"] == 500.0
    assert parsed["is_transfer"] is True


def test_one_mis_signed_payment_row_cannot_cancel_out_a_real_one(db, user_id, accounts):
    """Payments are clamped per row, not on the total. Summing first would let a
    single row signed the wrong way eat into the payments beside it and report a
    settled statement as short."""
    card_id = accounts["Example Card"]
    # A statement closing Aug 5 owing 500, then paid after it closed.
    add_imported(db, user_id, card_id, "Example Card", date(2026, 7, 20), "Acme Store", -500.00)
    add_imported(db, user_id, card_id, "Example Card", date(2026, 8, 10),
                 "Payment", 500.00, flow="transfer", is_transfer=True)
    add_imported(db, user_id, card_id, "Example Card", date(2026, 8, 12),
                 "Mis-signed payment", -25.00, flow="transfer", is_transfer=True)

    card = db.load_account_by_id(user_id, card_id)
    statement = [
        s for s in db.build_card_statements(user_id, card, date(2026, 9, 30), None, today=date(2026, 9, 1))
        if s["close_date"] == date(2026, 8, 5)
    ][0]

    assert statement["paid_since_close"] == 500.00, "the real payment stands on its own"
    assert statement["status"] == "paid"
