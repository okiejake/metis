"""Shared fixtures. Every test runs against a throwaway DuckDB file built by
init_db(), so tests never touch the real finance.duckdb and all data below is
synthetic."""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests never open the real database. services reads its default path when it is
# imported, so point the default at a file that is never created before then;
# a test that undoes its own DB_PATH falls back to this, not to finance.duckdb.
_NO_DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "no-such-dir", "never.duckdb")
os.environ["FINANCE_DB_PATH"] = _NO_DATABASE

import services  # noqa: E402

REAL_DATABASE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finance.duckdb"))


_open_database = services._database


def _guarded_database():
    if os.path.abspath(services.DB_PATH) == REAL_DATABASE:
        raise RuntimeError("a test tried to open finance.duckdb")
    return _open_database()


services._database = _guarded_database


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


def add_bill(
    db,
    user_id,
    name,
    amount,
    first_due_date,
    account_id=None,
    kind="expense",
    frequency_type="monthly",
    category_id=None,
    interval_months=1,
):
    """A bill (or scheduled income, with kind="income") that first falls due on
    first_due_date and recurs on that date's day."""
    with db.get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO bills (
                name, kind, amount, first_due_date, frequency_type, interval_months,
                semimonthly_day1, semimonthly_day2, user_id, account_id, category_id, active
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 15, ?, ?, ?, TRUE)
            RETURNING id
            """,
            [name, kind, amount, first_due_date, frequency_type, interval_months, user_id, account_id, category_id],
        ).fetchone()
    return int(row[0])


def add_one_time(db, user_id, name, amount, expected_date, account_id=None, kind="expense", category_id=None):
    with db.get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO one_time_items (name, kind, amount, expected_date, category_id, account_id, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            [name, kind, amount, expected_date, category_id, account_id, user_id],
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


def due_months_before(anchor: date, months: int) -> date:
    """A first due date at least `months` before the anchor that keeps the
    anchor's day, so a monthly bill starting there falls due on the anchor.
    Steps further back past any month too short to hold the day."""
    while True:
        candidate = months_before(anchor, months)
        if candidate.day == anchor.day:
            return candidate
        months += 1


def rows_on(transactions, when, description=None):
    return [
        tx
        for tx in transactions
        if tx["date"] == when and (description is None or tx["description"] == description)
    ]


__all__ = [
    "add_bill",
    "add_one_time",
    "add_imported",
    "reconcile",
    "months_before",
    "due_months_before",
    "rows_on",
    "timedelta",
]


def quiet_imports(user_id):
    """Dismiss the inbox's stale-account flags, for tests about the other lists.
    Fixture accounts start with nothing imported, which the inbox lists."""
    import inbox

    for row in inbox.stale_accounts(user_id, date.today()):
        inbox.dismiss(user_id, row["key"])


V0_4_0_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "schema_v0_4_0.sql")


@pytest.fixture
def v040(tmp_path, monkeypatch):
    """A database in the v0.4.0 shape, holding the synthetic rows in
    tests/fixtures/schema_v0_4_0.sql. init_db() has not run on it yet."""
    monkeypatch.setattr(services, "DB_PATH", str(tmp_path / "v040.duckdb"))
    with services.get_connection() as conn:
        with open(V0_4_0_FIXTURE, encoding="utf-8") as handle:
            conn.execute(handle.read())
    return services


def table_names(db):
    return {row[0] for row in db.fetch_all("SELECT table_name FROM duckdb_tables()")}


def snapshot(db):
    """Everything in the database: each table's columns and rows, and the
    indexes. Two equal snapshots mean nothing changed."""
    tables = {}
    for table in sorted(table_names(db)):
        columns = [row[1] for row in db.fetch_all(f"PRAGMA table_info('{table}')")]
        rows = db.fetch_all(f'SELECT * FROM "{table}"')
        tables[table] = (columns, sorted(rows, key=repr))
    indexes = sorted(row[0] for row in db.fetch_all("SELECT sql FROM duckdb_indexes()"))
    return tables, indexes
