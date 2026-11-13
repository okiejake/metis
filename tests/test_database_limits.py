"""A small home server has little memory to spare. DuckDB claims most of a
machine's memory and all of its cores by default, so a deployment can cap both.
Unset, DuckDB keeps its own defaults. The caps apply when the database opens.
"""

import duckdb


def settings(db):
    db.close_database()  # reopen with the settings as they are now
    with db.get_connection() as conn:
        return conn.execute("SELECT current_setting('memory_limit'), current_setting('threads')").fetchone()


def test_memory_and_threads_can_be_capped(db, monkeypatch):
    monkeypatch.setenv("METIS_DB_MEMORY_LIMIT", "64MB")
    monkeypatch.setenv("METIS_DB_THREADS", "1")

    assert settings(db) == ("61.0 MiB", 1)


def test_unset_limits_leave_duckdb_defaults(db, monkeypatch):
    monkeypatch.delenv("METIS_DB_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("METIS_DB_THREADS", raising=False)

    with duckdb.connect(":memory:") as conn:
        defaults = conn.execute("SELECT current_setting('memory_limit'), current_setting('threads')").fetchone()

    assert settings(db) == defaults
