"""A credit card statement is a record of its own, not a view over whatever dates
the card happens to be configured with today.

Each cycle a card closes is recorded with the dates it actually had. Its figures
are summed from imported rows every time, so a transaction imported late lands in
the statement it belongs to. The balance the issuer printed can be entered; it
becomes what the next statement carries in, and a statement whose rows do not add
up to it says by how much — which is how a missing transaction shows itself.

All data here is synthetic. The fixture card closes on the 5th, due the 25th.
"""

from datetime import date

import pytest

from conftest import add_imported

TODAY = date(2026, 9, 10)
UNTIL = date(2026, 10, 31)
NO_CUTOVER = None


@pytest.fixture
def card(db, user_id, accounts):
    return db.load_account_by_id(user_id, accounts["Example Card"])


def charge(db, user_id, card, when, amount, name="Acme Store"):
    return add_imported(db, user_id, card["id"], card["name"], when, name, -amount)


def pay(db, user_id, card, when, amount):
    return add_imported(
        db, user_id, card["id"], card["name"], when, f"Payment {when}", amount,
        flow="transfer", is_transfer=True,
    )


def statements(db, user_id, card, cutover=NO_CUTOVER, today=TODAY):
    return db.build_card_statements(user_id, card, UNTIL, cutover, today=today)


def by_close(items, close):
    return next(item for item in items if item["close_date"] == close)


def recorded_closes(db, user_id, card):
    return [row["close_date"] for row in db.load_recorded_statements(user_id, card["id"])]


def test_each_closed_cycle_is_recorded_from_the_first_imported_row(db, user_id, card):
    charge(db, user_id, card, date(2026, 6, 20), 100.00)

    db.sync_card_statements(user_id, card, TODAY)

    assert recorded_closes(db, user_id, card) == [
        date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5),
    ], "the open cycle (closing Oct 5) is not a statement yet"


def test_syncing_again_records_nothing_new(db, user_id, card):
    charge(db, user_id, card, date(2026, 6, 20), 100.00)

    db.sync_card_statements(user_id, card, TODAY)
    db.sync_card_statements(user_id, card, TODAY)

    assert len(recorded_closes(db, user_id, card)) == 3


def test_older_history_imported_later_is_backfilled(db, user_id, card):
    charge(db, user_id, card, date(2026, 8, 20), 100.00)
    db.sync_card_statements(user_id, card, TODAY)
    assert recorded_closes(db, user_id, card) == [date(2026, 9, 5)]

    charge(db, user_id, card, date(2026, 6, 20), 50.00)
    db.sync_card_statements(user_id, card, TODAY)

    assert recorded_closes(db, user_id, card) == [
        date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5),
    ]


def test_changing_the_cycle_day_leaves_recorded_statements_alone(db, user_id, card):
    charge(db, user_id, card, date(2026, 6, 20), 100.00)
    db.sync_card_statements(user_id, card, date(2026, 8, 10))
    assert recorded_closes(db, user_id, card) == [date(2026, 7, 5), date(2026, 8, 5)]

    moved = {**card, "statement_day": 12}
    db.sync_card_statements(user_id, moved, TODAY)
    assert recorded_closes(db, user_id, card) == [date(2026, 7, 5), date(2026, 8, 5)]

    db.sync_card_statements(user_id, moved, date(2026, 9, 20))

    # Aug 12 is a week after the last close, so the switch-over cycle runs long
    # to Sep 12 rather than recording a one-week statement.
    assert recorded_closes(db, user_id, card) == [
        date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 12),
    ]


def test_a_statement_balance_runs_on_from_the_last_one(db, user_id, card):
    charge(db, user_id, card, date(2026, 6, 20), 300.00)
    pay(db, user_id, card, date(2026, 7, 20), 300.00)
    charge(db, user_id, card, date(2026, 7, 22), 80.00)

    july, august = (by_close(statements(db, user_id, card, date(2026, 9, 9)), close)
                    for close in (date(2026, 7, 5), date(2026, 8, 5)))

    assert july["balance"] == 300.00 and july["status"] == "paid"
    assert august["previous_balance"] == 300.00
    assert august["payments"] == 300.00 and august["charges"] == 80.00
    assert august["balance"] == 80.00


def test_a_late_import_lands_in_the_statement_it_belongs_to(db, user_id, card):
    charge(db, user_id, card, date(2026, 7, 10), 100.00)
    before = by_close(statements(db, user_id, card), date(2026, 8, 5))

    charge(db, user_id, card, date(2026, 7, 15), 40.00, "Acme Books")
    after = by_close(statements(db, user_id, card), date(2026, 8, 5))

    assert after["id"] == before["id"], "the same recorded statement, not a new one"
    assert after["charges"] == before["charges"] + 40.00
    assert len(after["activity"]) == 2


def test_the_issuers_balance_carries_forward_and_shows_the_gap(db, user_id, card):
    charge(db, user_id, card, date(2026, 7, 10), 100.00)
    charge(db, user_id, card, date(2026, 8, 10), 50.00)
    july = by_close(statements(db, user_id, card), date(2026, 8, 5))
    # The issuer printed 120: a 20.00 charge never made it into the import.
    db.update_card_statement(
        user_id, july["id"], july["close_date"], july["due_date"], 120.00, today=TODAY
    )

    items = statements(db, user_id, card, date(2026, 9, 9))
    july, august = by_close(items, date(2026, 8, 5)), by_close(items, date(2026, 9, 5))

    assert july["computed_balance"] == 100.00
    assert july["balance"] == 120.00
    assert july["difference"] == 20.00, "the statement says how far the import is off"
    assert august["previous_balance"] == 120.00, "the issuer's figure is what carries in"


def test_moving_a_close_date_moves_rows_between_statements(db, user_id, card):
    charge(db, user_id, card, date(2026, 7, 10), 100.00)
    charge(db, user_id, card, date(2026, 8, 6), 30.00, "Acme Books")
    july = by_close(statements(db, user_id, card), date(2026, 8, 5))

    db.update_card_statement(
        user_id, july["id"], date(2026, 8, 7), july["due_date"], None, today=TODAY
    )
    items = statements(db, user_id, card)

    assert by_close(items, date(2026, 8, 7))["charges"] == 130.00
    assert by_close(items, date(2026, 9, 5))["period_start"] == date(2026, 8, 8)


@pytest.mark.parametrize(
    "close, due, message",
    [
        (date(2026, 7, 5), date(2026, 8, 25), "after the previous"),
        (date(2026, 9, 5), date(2026, 9, 25), "before the next"),
        (date(2026, 8, 5), date(2026, 8, 5), "Due date must be after"),
    ],
)
def test_a_close_date_has_to_stay_between_its_neighbours(db, user_id, card, close, due, message):
    charge(db, user_id, card, date(2026, 6, 20), 100.00)
    august = by_close(statements(db, user_id, card), date(2026, 8, 5))

    with pytest.raises(ValueError, match=message):
        db.update_card_statement(user_id, august["id"], close, due, None, today=TODAY)


def test_a_statement_cannot_be_moved_to_close_in_the_future(db, user_id, card):
    charge(db, user_id, card, date(2026, 8, 20), 100.00)
    september = by_close(statements(db, user_id, card), date(2026, 9, 5))

    with pytest.raises(ValueError, match="passed"):
        db.update_card_statement(
            user_id, september["id"], date(2026, 9, 12), date(2026, 9, 30), None, today=TODAY
        )


def test_the_open_and_later_cycles_are_estimates(db, user_id, card):
    charge(db, user_id, card, date(2026, 8, 20), 100.00)
    charge(db, user_id, card, date(2026, 9, 7), 25.00)

    items = statements(db, user_id, card)
    open_one = by_close(items, date(2026, 10, 5))

    assert open_one["status"] == "open" and open_one["id"] is None
    assert open_one["charges"] == 25.00
    assert open_one["label"] == "Example Card estimated statement"
    assert by_close(items, date(2026, 11, 5))["status"] == "estimated"
