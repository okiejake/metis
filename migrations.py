"""One-time data steps.

Each step is a named function that takes a connection. A step runs once per
database, inside a transaction, and is then recorded in applied_migrations, so
a step that fails leaves the database exactly as it was and runs again on the
next start. DuckDB rolls back schema changes along with data.

A step must do all of its work through the connection it is given. Another
connection would not see the transaction's changes.

DuckDB cannot rename a table or a column while an index depends on the table,
and cannot rename a sequence, so a table that changes its name or a column's
name is copied into a new table, which keeps every id, and the old one is
dropped. The new table's sequence starts after the highest id.

The old names (recurring_items, manual_transactions, review_actions,
merchant_decisions) live on here and in legacy_schema.py, for upgrading only.
"""

import calendar
import json
from datetime import date
from typing import Callable, List, Optional, Sequence, Tuple

import duckdb

Step = Tuple[str, Callable[[duckdb.DuckDBPyConnection], None]]


def _ensure_record(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS applied_migrations "
        "(name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT NOW())"
    )


def applied(conn: duckdb.DuckDBPyConnection) -> set:
    _ensure_record(conn)
    return {row[0] for row in conn.execute("SELECT name FROM applied_migrations").fetchall()}


def run(conn: duckdb.DuckDBPyConnection, steps: Sequence[Step]) -> List[str]:
    """Run each step that has not run on this database yet, in order. Returns
    the names of the steps it ran. Stops at the first step that raises."""
    done = applied(conn)
    ran: List[str] = []
    for name, step in steps:
        if name in done:
            continue
        conn.execute("BEGIN TRANSACTION")
        try:
            step(conn)
            conn.execute("INSERT INTO applied_migrations (name) VALUES (?)", [name])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        ran.append(name)
    return ran


def record(conn: duckdb.DuckDBPyConnection, steps: Sequence[Step]) -> None:
    """Mark steps as done without running them: a database created by this
    version is already in the shape they produce."""
    done = applied(conn)
    for name, _step in steps:
        if name not in done:
            conn.execute("INSERT INTO applied_migrations (name) VALUES (?)", [name])


# --- Helpers ------------------------------------------------------------------


def _exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM duckdb_tables() WHERE table_name = ?", [table]).fetchone())


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]


def _replace(conn: duckdb.DuckDBPyConnection, old: str, new: str, create_sql: str,
             insert_sql: str, rows: Optional[List[tuple]] = None) -> None:
    """Copy `old` into a new table `new` made by `create_sql`, keeping ids, then
    drop `old` and its sequence. With `rows`, insert_sql runs once per row;
    otherwise it is an INSERT ... SELECT. When old and new share a name the copy
    goes through a holding table."""
    source = old
    if old == new:
        source = f"{old}_holding"
        conn.execute(f"CREATE TABLE {source} AS SELECT * FROM {old}")
        conn.execute(f"DROP TABLE {old}")
        conn.execute(f"DROP SEQUENCE IF EXISTS {old}_id_seq")
    highest = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {source}").fetchone()[0]
    conn.execute(f"CREATE SEQUENCE {new}_id_seq START {int(highest) + 1}")
    conn.execute(create_sql)
    if rows is None:
        conn.execute(insert_sql.format(source=source))
    else:
        for row in rows:
            conn.execute(insert_sql, list(row))
    conn.execute(f"DROP TABLE {source}")
    if source == old:
        conn.execute(f"DROP SEQUENCE IF EXISTS {old}_id_seq")


# --- v0.4.0 to v0.5.0 ---------------------------------------------------------


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _month_step(year: int, month: int, months: int) -> Tuple[int, int]:
    index = year * 12 + month - 1 + months
    return index // 12, index % 12 + 1


def _v0_4_0_monthly(start: date, step: int, day: int, through: date) -> List[date]:
    found, year, month = [], start.year, start.month
    while date(year, month, 1) <= through:
        candidate = _clamped(year, month, day)
        if start <= candidate <= through:
            found.append(candidate)
        year, month = _month_step(year, month, step)
    return found


def _v0_5_0_monthly(first: date, step: int, through: date) -> List[date]:
    return _v0_4_0_monthly(first, step, first.day, through)


def first_due_date_from_v0_4_0(start: date, frequency: str, interval: Optional[int], day: Optional[int]) -> date:
    """The first due date that makes a v0.4.0 item fall due on exactly the dates
    it did, now that the day comes from the first due date (#69).

    An item whose day matched its start date keeps it. Otherwise the date moves
    to the item's first old due date. When that month is too short to hold the
    day, it moves back, a period at a time, to the nearest month that holds it,
    so the day survives; this can add due dates before the old start, never
    after it. Weekly, bi-weekly and semi-monthly items never used the day."""
    if frequency not in ("monthly", "yearly", "every_x_months"):
        return start
    wanted = int(day) if day else start.day
    step = {"monthly": 1, "yearly": 12}.get(frequency) or int(interval or 1)
    through = date(start.year + 12, start.month, 1)
    old = _v0_4_0_monthly(start, step, wanted, through)
    if not old:
        return start
    first_old = old[0]
    # A day no month on the schedule can hold (the 31st, yearly in February)
    # behaves as the longest day those months have.
    for target in range(wanted, 27, -1):
        year, month = first_old.year, first_old.month
        for _ in range(49):
            if calendar.monthrange(year, month)[1] >= target:
                candidate = date(year, month, target)
                if [when for when in _v0_5_0_monthly(candidate, step, through) if when >= start] == old:
                    return candidate
            year, month = _month_step(year, month, -step)
    return first_old


def _inbox_actions(conn: duckdb.DuckDBPyConnection) -> None:
    """review_actions becomes inbox_actions. A bill added from a suggestion was
    recorded as a 'recurring' action; it is now a 'bill' action."""
    if not _exists(conn, "review_actions"):
        return
    _replace(conn, "review_actions", "inbox_actions", """
        CREATE TABLE inbox_actions (
            id BIGINT PRIMARY KEY DEFAULT nextval('inbox_actions_id_seq'),
            user_id BIGINT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """, "INSERT INTO inbox_actions (id, user_id, kind, payload, created_at) "
         "SELECT id, user_id, kind, payload, created_at FROM {source}")
    for action_id, payload in conn.execute(
            "SELECT id, payload FROM inbox_actions WHERE kind = 'recurring'").fetchall():
        data = json.loads(payload)
        data["bill_id"] = data.pop("item_id", None)
        conn.execute("UPDATE inbox_actions SET kind = 'bill', payload = ? WHERE id = ?", [json.dumps(data), action_id])


BILLS_TABLE = """
    CREATE TABLE bills (
        id BIGINT PRIMARY KEY DEFAULT nextval('bills_id_seq'),
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        amount DOUBLE NOT NULL,
        first_due_date DATE NOT NULL,
        end_date DATE,
        frequency_type TEXT NOT NULL,
        interval_months INTEGER,
        semimonthly_day1 INTEGER,
        semimonthly_day2 INTEGER,
        category_id BIGINT,
        account_id BIGINT,
        user_id BIGINT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        balance DOUBLE,
        balance_as_of DATE,
        interest_rate DOUBLE,
        biller_url TEXT,
        charge_frequency TEXT,
        charge_first_date DATE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
"""


def _bills(conn: duckdb.DuckDBPyConnection) -> None:
    """recurring_items becomes bills. start_date becomes first_due_date, moved
    so the schedule is unchanged, and day_of_month goes. The columns bills in
    depth uses arrive now, empty."""
    if not _exists(conn, "recurring_items"):
        return
    rows = []
    for row in conn.execute(
            "SELECT id, name, kind, amount, start_date, end_date, frequency_type, interval_months, "
            "semimonthly_day1, semimonthly_day2, day_of_month, category_id, account_id, user_id, active, created_at "
            "FROM recurring_items ORDER BY id").fetchall():
        (bill_id, name, kind, amount, start, end, frequency, interval, day1, day2, day,
         category_id, account_id, user_id, active, created_at) = row
        first = first_due_date_from_v0_4_0(start, frequency, interval, day)
        rows.append((bill_id, name, kind, amount, first, end, frequency, interval, day1, day2,
                     category_id, account_id, user_id, active, created_at))
    _replace(conn, "recurring_items", "bills", BILLS_TABLE,
             "INSERT INTO bills (id, name, kind, amount, first_due_date, end_date, frequency_type, interval_months, "
             "semimonthly_day1, semimonthly_day2, category_id, account_id, user_id, active, created_at) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _bill_overrides(conn: duckdb.DuckDBPyConnection) -> None:
    """amount_overrides.recurring_item_id becomes bill_id."""
    if "recurring_item_id" not in _columns(conn, "amount_overrides"):
        return
    _replace(conn, "amount_overrides", "amount_overrides", """
        CREATE TABLE amount_overrides (
            id BIGINT PRIMARY KEY DEFAULT nextval('amount_overrides_id_seq'),
            bill_id BIGINT NOT NULL,
            effective_date DATE NOT NULL,
            amount DOUBLE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """, "INSERT INTO amount_overrides (id, bill_id, effective_date, amount, created_at) "
         "SELECT id, recurring_item_id, effective_date, amount, created_at FROM {source}")


def _one_time_items(conn: duckdb.DuckDBPyConnection) -> None:
    """manual_transactions becomes one_time_items; tx_date becomes expected_date."""
    if not _exists(conn, "manual_transactions"):
        return
    _replace(conn, "manual_transactions", "one_time_items", """
        CREATE TABLE one_time_items (
            id BIGINT PRIMARY KEY DEFAULT nextval('one_time_items_id_seq'),
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            amount DOUBLE NOT NULL,
            expected_date DATE NOT NULL,
            category_id BIGINT,
            account_id BIGINT,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """, "INSERT INTO one_time_items (id, name, kind, amount, expected_date, category_id, account_id, user_id, "
         "created_at) SELECT id, name, kind, amount, tx_date, category_id, account_id, user_id, created_at FROM {source}")


def _bill_source_type(conn: duckdb.DuckDBPyConnection) -> None:
    """Links and auto-match rules name a bill 'bill', not 'recurring'."""
    for table in ("expected_reconciliations", "expected_match_rules"):
        conn.execute(f"UPDATE {table} SET source_type = 'bill' WHERE source_type = 'recurring'")


# The steps that bring a v0.4.0 database to this version's shape, in order.
STEPS: List[Step] = [
    ("inbox_actions", _inbox_actions),
    ("bills", _bills),
    ("bill_overrides", _bill_overrides),
    ("one_time_items", _one_time_items),
    ("bill_source_type", _bill_source_type),
]
