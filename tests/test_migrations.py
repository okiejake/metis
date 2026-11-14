"""One-time data steps: each is a named function, runs once, is recorded in
applied_migrations, and runs inside a transaction so a failure leaves the
database as it was. The v0.4.0 fixture is where every upgrade test starts.

All data here is synthetic.
"""

from datetime import date

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


# --- v0.4.0 to v0.5.0: names and data ----------------------------------------

BILL_COLUMNS = (
    "id, name, kind, amount, end_date, frequency_type, interval_months, semimonthly_day1, "
    "semimonthly_day2, category_id, user_id, active, account_id, created_at"
)


def columns(db, table):
    return [row[1] for row in db.fetch_all(f"PRAGMA table_info('{table}')")]


def test_every_recurring_item_becomes_a_bill_with_the_same_id(v040):
    before = v040.fetch_all(f"SELECT {BILL_COLUMNS} FROM recurring_items ORDER BY id")

    v040.init_db()

    assert v040.fetch_all(f"SELECT {BILL_COLUMNS} FROM bills ORDER BY id") == before
    assert "recurring_items" not in table_names(v040)
    assert not v040.fetch_all("SELECT 1 FROM duckdb_sequences() WHERE sequence_name = 'recurring_items_id_seq'")


def test_a_bill_has_a_first_due_date_and_no_day_of_month(v040):
    v040.init_db()

    names = columns(v040, "bills")
    assert "first_due_date" in names and "start_date" not in names and "day_of_month" not in names
    assert dict(v040.fetch_all("SELECT id, first_due_date FROM bills ORDER BY id")) == {
        1: date(2026, 1, 1),    # the day already matched
        2: date(2026, 1, 9),    # every two weeks: untouched
        3: date(2026, 1, 20),   # day 20, started on the 5th
        4: date(2026, 2, 20),   # day 20, started on the 25th
        5: date(2025, 8, 31),   # day 31 every 3 months from February: back to a month that holds it
        6: date(2027, 3, 20),   # yearly on March 20, started March 25
        7: date(2026, 1, 31),
        8: date(2026, 1, 1),
        9: date(2025, 1, 10),
        10: date(2026, 1, 12),
        11: date(2026, 1, 31),  # day 31 from February 10: back to January 31
        12: date(2026, 4, 18),  # no day set: the start date's
        13: date(2026, 1, 3),
    }


def test_a_bill_carries_the_columns_bills_in_depth_will_use(v040):
    v040.init_db()

    assert {"balance", "balance_as_of", "interest_rate", "biller_url", "charge_frequency", "charge_first_date"} <= set(
        columns(v040, "bills")
    )
    assert v040.fetch_one(
        "SELECT COUNT(*) FROM bills WHERE balance IS NOT NULL OR interest_rate IS NOT NULL OR biller_url IS NOT NULL "
        "OR charge_frequency IS NOT NULL"
    )[0] == 0


def v0_4_0_dates(start, frequency, interval, day, semi1, semi2, through):
    """What v0.4.0 generated for a recurring item, written out independently."""
    import calendar
    from datetime import timedelta

    def clamp(year, month, wanted):
        return date(year, month, min(wanted, calendar.monthrange(year, month)[1]))

    found = []
    if frequency in ("weekly", "biweekly"):
        current = start
        while current <= through:
            found.append(current)
            current += timedelta(days=7 if frequency == "weekly" else 14)
        return found
    step = {"monthly": 1, "yearly": 12, "semimonthly": 1}.get(frequency, interval or 1)
    year, month = start.year, start.month
    while date(year, month, 1) <= through:
        days = sorted({semi1 or 1, semi2 or 15}) if frequency == "semimonthly" else [day or start.day]
        for wanted in days:
            candidate = clamp(year, month, wanted)
            if start <= candidate <= through:
                found.append(candidate)
        index = year * 12 + month - 1 + step
        year, month = index // 12, index % 12 + 1
    return found


def test_a_bill_forecasts_the_same_dates_before_and_after(v040):
    through = date(2029, 12, 31)
    old = {
        row[0]: (row[1], v0_4_0_dates(*row[1:], through))
        for row in v040.fetch_all(
            "SELECT id, start_date, frequency_type, interval_months, day_of_month, semimonthly_day1, "
            "semimonthly_day2 FROM recurring_items ORDER BY id"
        )
    }

    v040.init_db()

    earlier = {}
    for user_id in (1, 2):
        for bill in v040.load_all_bills(user_id):
            started, expected = old[bill["id"]]
            dates = [row["date"] for row in v040.bill_due_dates({**bill, "end_date": None}, date(2020, 1, 1), through)]
            assert [when for when in dates if when >= started] == expected, bill["name"]
            earlier[bill["id"]] = [when for when in dates if when < started]
    # Only a bill whose start month could not hold its day gains due dates, all before its old start.
    assert {bill_id: found for bill_id, found in earlier.items() if found} == {
        5: [date(2025, 8, 31), date(2025, 11, 30)],
        11: [date(2026, 1, 31)],
    }


def test_a_new_bill_takes_the_next_id(v040):
    v040.init_db()

    new_id = v040.fetch_one(
        "INSERT INTO bills (name, kind, amount, first_due_date, frequency_type, user_id) "
        "VALUES ('Example', 'expense', 10, DATE '2026-01-01', 'monthly', 1) RETURNING id"
    )[0]

    assert new_id == 14


def test_amount_changes_follow_their_bill(v040):
    before = v040.fetch_all("SELECT id, recurring_item_id, effective_date, amount FROM amount_overrides ORDER BY id")

    v040.init_db()

    assert v040.fetch_all("SELECT id, bill_id, effective_date, amount FROM amount_overrides ORDER BY id") == before
    assert "recurring_item_id" not in columns(v040, "amount_overrides")
    january, june = (
        v040.bill_due_dates(v040.load_bill_by_id(1, 1), day, day, v040.load_amount_overrides(1))[0]["amount"]
        for day in (date(2026, 1, 1), date(2026, 6, 1))
    )
    assert (january, june) == (1200.0, 1250.0)


def test_every_manual_transaction_becomes_a_one_time_item(v040):
    before = v040.fetch_all(
        "SELECT id, name, kind, amount, tx_date, category_id, user_id, account_id, created_at "
        "FROM manual_transactions ORDER BY id"
    )

    v040.init_db()

    assert v040.fetch_all(
        "SELECT id, name, kind, amount, expected_date, category_id, user_id, account_id, created_at "
        "FROM one_time_items ORDER BY id"
    ) == before
    assert "manual_transactions" not in table_names(v040)
    assert v040.fetch_one(
        "INSERT INTO one_time_items (name, kind, amount, expected_date, user_id) "
        "VALUES ('Example', 'expense', 10, DATE '2026-01-01', 1) RETURNING id"
    )[0] == 4


def test_links_and_auto_match_rules_point_at_bills(v040):
    links = v040.fetch_all("SELECT id, imported_transaction_id, source_id, matched_via FROM expected_reconciliations ORDER BY id")
    rules = v040.fetch_all("SELECT id, source_id, pattern, amount_min, amount_max FROM expected_match_rules ORDER BY id")

    v040.init_db()

    assert v040.fetch_all("SELECT id, imported_transaction_id, source_id, matched_via FROM expected_reconciliations ORDER BY id") == links
    assert v040.fetch_all("SELECT id, source_id, pattern, amount_min, amount_max FROM expected_match_rules ORDER BY id") == rules
    assert v040.fetch_all("SELECT id, source_type FROM expected_reconciliations ORDER BY id") == [
        (1, "bill"), (2, "bill"), (3, "bill"), (4, "one_time"), (5, "bill")
    ]
    assert v040.fetch_all("SELECT id, source_type FROM expected_match_rules ORDER BY id") == [
        (1, "bill"), (2, "one_time"), (3, "bill")
    ]


def test_recorded_actions_move_to_inbox_actions_with_their_ids(v040):
    before = v040.fetch_all("SELECT id, user_id, created_at FROM review_actions ORDER BY id")

    v040.init_db()

    assert v040.fetch_all("SELECT id, user_id, created_at FROM inbox_actions ORDER BY id") == before
    assert "review_actions" not in table_names(v040)
    import inbox

    assert inbox.record_action(1, "dismiss", {"key": "x"}) == 8


def test_an_action_recorded_before_the_upgrade_can_still_be_undone(v040):
    """Adding a bill from a suggestion, recorded as a 'recurring' action."""
    v040.init_db()
    import inbox

    assert v040.fetch_one("SELECT kind FROM inbox_actions WHERE id = 6")[0] == "bill"

    inbox.undo(1, 6)

    assert v040.fetch_one("SELECT COUNT(*) FROM bills WHERE id = 3")[0] == 0
    assert v040.fetch_one("SELECT COUNT(*) FROM expected_reconciliations WHERE source_type = 'bill' AND source_id = 3")[0] == 0
    assert v040.fetch_one("SELECT COUNT(*) FROM dismissed_suggestions WHERE suggestion_key = 'ACME POWER CO'")[0] == 0


def test_the_forecast_and_budget_read_the_upgraded_data(v040):
    v040.init_db()

    rent = [
        row["date"] for row in v040.collect_window_transactions(1, date(2026, 1, 1), date(2026, 3, 31))
        if row["description"] == "Rent"
    ]
    summary = v040.build_budget_summary(1, date(2026, 1, 1), date(2026, 1, 31))

    assert rent == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    housing = next(category for category in summary["categories"] if category["name"] == "Housing")
    assert housing["actual"] == 1200.0 and housing["budgeted"] >= 1200.0


def test_a_database_older_than_v0_4_0_is_brought_forward_first(v040):
    """Before the inbox existed there was no table of recorded actions."""
    v040.execute("DROP TABLE review_actions")
    v040.execute("DROP SEQUENCE review_actions_id_seq")
    v040.execute("ALTER TABLE users DROP COLUMN locked_until")

    v040.init_db()

    assert {"inbox_actions", "bills", "one_time_items"} <= table_names(v040)
    assert "locked_until" in columns(v040, "users")
    assert v040.fetch_one("SELECT COUNT(*) FROM bills")[0] == 13


def test_a_step_that_fails_keeps_the_old_table_until_the_next_start(v040, monkeypatch):
    def broken(conn):
        conn.execute("CREATE TABLE one_time_items AS SELECT * FROM manual_transactions")
        conn.execute("DROP TABLE manual_transactions")
        raise RuntimeError("out of disk")

    real_steps = migrations.STEPS
    steps = [(name, broken if name == "one_time_items" else step) for name, step in real_steps]
    monkeypatch.setattr(migrations, "STEPS", steps)
    with pytest.raises(RuntimeError):
        v040.init_db()

    assert "manual_transactions" in table_names(v040) and "one_time_items" not in table_names(v040)
    assert v040.fetch_one("SELECT COUNT(*) FROM manual_transactions")[0] == 3

    # Put back only the steps. monkeypatch.undo() would also undo the fixture's
    # DB_PATH, pointing the next init_db() at the default database.
    monkeypatch.setattr(migrations, "STEPS", real_steps)
    v040.init_db()

    assert v040.fetch_one("SELECT COUNT(*) FROM one_time_items")[0] == 3
