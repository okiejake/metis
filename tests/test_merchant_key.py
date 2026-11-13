"""One merchant key, shared by Review and the recurring detector, decides which
charges are one merchant. A payment processor's prefix used to swallow the
merchant behind it, so every merchant billed through that processor became one.

All data here is synthetic.
"""

from datetime import date

from conftest import add_imported
from merchants import legacy_merchant_key, merchant_key


def test_merchants_behind_one_processor_prefix_stay_apart():
    assert merchant_key("PAYPAL *ACME COFFEE") != merchant_key("PAYPAL *EXAMPLE BAKERY")
    assert merchant_key("PAYPAL *ACME COFFEE") == "PAYPAL ACME COFFEE"


def test_an_order_id_after_the_prefix_is_dropped():
    assert merchant_key("AMZN MKTP US*2K1AB3CD0") == merchant_key("AMZN MKTP US*9ZZ8YY7XX")


def test_store_numbers_and_a_trailing_state_still_collapse():
    assert merchant_key("ACME GROCER #1234 SPRINGFIELD IL") == merchant_key("ACME GROCER #99 SPRINGFIELD IL")


def test_the_legacy_key_is_the_old_grouping():
    assert legacy_merchant_key("PAYPAL *ACME COFFEE") == "PAYPAL"


def test_a_suggestion_dismissed_under_the_old_key_stays_dismissed(db, user_id, accounts):
    checking = accounts["Example Checking"]
    for month in (3, 4, 5, 6):
        add_imported(db, user_id, checking, "Example Checking", date(2026, month, 2), "PAYPAL *ACME STREAMING", -10.0)
    assert [s["key"] for s in db.detect_recurring_suggestions(user_id, lookback_days=100000)] == ["PAYPAL ACME STREAMING"]

    db.dismiss_suggestion(user_id, "PAYPAL")

    assert db.detect_recurring_suggestions(user_id, lookback_days=100000) == []
