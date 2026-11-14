"""One-time data steps.

Each step is a named function that takes a connection. A step runs once per
database, inside a transaction, and is then recorded in applied_migrations, so
a step that fails leaves the database exactly as it was and runs again on the
next start. DuckDB rolls back schema changes along with data.

A step must do all of its work through the connection it is given. Another
connection would not see the transaction's changes.
"""

from typing import Callable, List, Sequence, Tuple

import duckdb

Step = Tuple[str, Callable[[duckdb.DuckDBPyConnection], None]]

# The steps that bring a v0.4.0 database to this version's shape, in order.
STEPS: List[Step] = []


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
