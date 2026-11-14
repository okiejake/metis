"""One-time data steps: each is a named function, runs once, is recorded in
applied_migrations, and runs inside a transaction so a failure leaves the
database as it was. The v0.4.0 fixture is where every upgrade test starts.

All data here is synthetic.
"""

import pytest

import migrations
from conftest import snapshot, table_names


def applied(db):
    return [row[0] for row in db.fetch_all("SELECT name FROM applied_migrations ORDER BY name")]


def test_a_step_runs_once_and_is_recorded(db):
    calls = []

    def step(conn):
        calls.append("ran")
        conn.execute("CREATE TABLE made_by_step (n INTEGER)")

    with db.get_connection() as conn:
        migrations.run(conn, [("made_table", step)])
        migrations.run(conn, [("made_table", step)])

    assert calls == ["ran"]
    assert "made_table" in applied(db)
    assert "made_by_step" in table_names(db)


def test_a_failing_step_leaves_the_database_as_it_was(db, user_id):
    before = snapshot(db)

    def step(conn):
        conn.execute("CREATE TABLE half_made (n INTEGER)")
        conn.execute("UPDATE users SET display_name = 'changed'")
        conn.execute("DROP TABLE saved_ranges")
        raise RuntimeError("the step broke")

    with db.get_connection() as conn:
        with pytest.raises(RuntimeError, match="the step broke"):
            migrations.run(conn, [("breaks", step)])

    assert snapshot(db) == before
    assert "breaks" not in applied(db)


def test_steps_run_in_order_and_stop_at_the_first_failure(db):
    ran = []

    def first(conn):
        ran.append("first")

    def second(conn):
        ran.append("second")
        raise RuntimeError("no")

    def third(conn):
        ran.append("third")

    with db.get_connection() as conn:
        with pytest.raises(RuntimeError):
            migrations.run(conn, [("first", first), ("second", second), ("third", third)])

    assert ran == ["first", "second"]
    assert "first" in applied(db) and "second" not in applied(db) and "third" not in applied(db)


def test_a_step_that_failed_runs_again_next_time(db):
    attempts = []

    def step(conn):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise RuntimeError("first try")
        conn.execute("CREATE TABLE made_second_time (n INTEGER)")

    with db.get_connection() as conn:
        with pytest.raises(RuntimeError):
            migrations.run(conn, [("retried", step)])
        migrations.run(conn, [("retried", step)])

    assert len(attempts) == 2 and "made_second_time" in table_names(db)


def test_the_fixture_is_the_v0_4_0_shape(v040):
    names = table_names(v040)

    assert {"recurring_items", "manual_transactions", "review_actions", "merchant_decisions"} <= names
    assert v040.fetch_one("SELECT COUNT(*) FROM recurring_items")[0] == 13


def test_running_init_db_twice_changes_nothing_the_second_time(v040):
    v040.init_db()
    first = snapshot(v040)

    v040.init_db()

    assert snapshot(v040) == first


def test_a_new_database_records_every_step_without_running_it(db):
    """A database made by this version is already in its shape."""
    assert set(applied(db)) >= {name for name, _step in migrations.STEPS}
