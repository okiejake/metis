"""Shared fixtures. Every test runs against a throwaway DuckDB file built by
init_db(), so tests never touch the real finance.duckdb and all data below is
synthetic."""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh, fully migrated database. get_connection() reads services.DB_PATH
    at call time, so pointing it at tmp_path is enough to redirect everything."""
    monkeypatch.setattr(services, "DB_PATH", str(tmp_path / "test.duckdb"))
    services.init_db()
    return services


@pytest.fixture
def user_id(db):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (slug, display_name, email) VALUES (?, ?, ?)",
            ["testuser", "Test User", "test@example.com"],
        )
        row = conn.execute("SELECT id FROM users WHERE slug = 'testuser'").fetchone()
    return int(row[0])


@pytest.fixture
def accounts(db, user_id):
    """One checking account and one credit card, returned by name."""
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) VALUES (?, ?, ?)",
            [user_id, "Example Checking", "checking"],
        )
        conn.execute(
            """
            INSERT INTO accounts (user_id, name, account_type, statement_day, due_day)
            VALUES (?, ?, ?, ?, ?)
            """,
            [user_id, "Example Card", "credit_card", 5, 25],
        )
        rows = conn.execute(
            "SELECT name, id FROM accounts WHERE user_id = ?", [user_id]
        ).fetchall()
    return {name: int(account_id) for name, account_id in rows}


def add_recurring(
    db,
    user_id,
    name,
    amount,
    day_of_month,
    start_date,
    account_id=None,
    kind="expense",
    frequency_type="monthly",
):
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO recurring_items (
                name, kind, amount, start_date, frequency_type, interval_months,
                semimonthly_day1, semimonthly_day2, day_of_month, user_id, account_id, active
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, 15, ?, ?, ?, TRUE)
            """,
            [name, kind, amount, start_date, frequency_type, day_of_month, user_id, account_id],
        )
        row = conn.execute(
            "SELECT id FROM recurring_items WHERE user_id = ? AND name = ?", [user_id, name]
        ).fetchone()
    return int(row[0])


def add_imported(
    db,
    user_id,
    account_id,
    account_name,
    tx_date,
    description,
    amount,
    flow="expense",
    is_transfer=False,
):
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO imported_transactions (
                user_id, account, tx_date, description, merchant, amount, flow,
                is_transfer, fingerprint, account_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                user_id,
                account_name,
                tx_date,
                description,
                description,
                amount,
                flow,
                is_transfer,
                f"fp-{description}-{tx_date}-{amount}",
                account_id,
            ],
        )
        row = conn.execute(
            "SELECT id FROM imported_transactions WHERE user_id = ? AND fingerprint = ?",
            [user_id, f"fp-{description}-{tx_date}-{amount}"],
        ).fetchone()
    return int(row[0])


def reconcile(db, user_id, source_type, source_id, imported_transaction_id):
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO expected_reconciliations (
                user_id, imported_transaction_id, source_type, source_id, matched_via
            )
            VALUES (?, ?, ?, ?, 'confirm')
            """,
            [user_id, imported_transaction_id, source_type, source_id],
        )


def months_before(anchor: date, months: int) -> date:
    """Same day-of-month, `months` earlier — clamped for short months."""
    month_index = (anchor.year * 12 + anchor.month - 1) - months
    year, month = month_index // 12, month_index % 12 + 1
    import calendar

    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def rows_on(transactions, when, description=None):
    return [
        tx
        for tx in transactions
        if tx["date"] == when and (description is None or tx["description"] == description)
    ]


__all__ = [
    "add_recurring",
    "add_imported",
    "reconcile",
    "months_before",
    "rows_on",
    "timedelta",
]


def quiet_imports(user_id):
    """Dismiss Review's stale-account flags, for tests about the other lists.
    Fixture accounts start with nothing imported, which Review lists."""
    import review

    for row in review.stale_accounts(user_id, date.today()):
        review.dismiss(user_id, row["key"])
