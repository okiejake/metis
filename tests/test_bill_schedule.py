"""A bill's schedule comes from its first due date (#69): monthly, every-N-months
and yearly bills recur on that date's day, clamped to the last day of shorter
months. There is no separate day of month.

All data here is synthetic.
"""

import calendar
from datetime import date, timedelta

import pytest

import migrations
from conftest import add_bill
from test_migrations import v0_4_0_dates

THROUGH = date(2031, 12, 31)


def due(db, user_id, bill_id, start, end):
    bill = db.load_bill_by_id(user_id, bill_id)
    return [row["date"] for row in db.bill_due_dates(bill, start, end)]


def test_a_bill_first_due_on_the_10th_recurs_on_the_10th(db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))

    assert due(db, user_id, bill, date(2026, 1, 1), date(2026, 4, 30)) == [
        date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10), date(2026, 4, 10)
    ]


def test_moving_the_first_due_date_moves_later_due_dates(db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 1, 10))

    db.execute("UPDATE bills SET first_due_date = ? WHERE id = ?", [date(2026, 1, 20), bill])

    assert due(db, user_id, bill, date(2026, 1, 1), date(2026, 3, 31)) == [
        date(2026, 1, 20), date(2026, 2, 20), date(2026, 3, 20)
    ]


@pytest.mark.parametrize(
    "first, expected",
    [
        (date(2026, 1, 31), [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]),
        (date(2026, 1, 30), [date(2026, 1, 30), date(2026, 2, 28), date(2026, 3, 30), date(2026, 4, 30)]),
        (date(2026, 1, 29), [date(2026, 1, 29), date(2026, 2, 28), date(2026, 3, 29), date(2026, 4, 29)]),
        (date(2028, 1, 31), [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)]),
    ],
)
def test_a_day_past_the_end_of_a_month_falls_on_its_last_day(db, user_id, first, expected):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, first)

    assert due(db, user_id, bill, first, first + timedelta(days=100)) == expected


def test_every_n_months_and_yearly_keep_the_day(db, user_id):
    quarterly = add_bill(db, user_id, "Acme Insurance", 300.0, date(2026, 1, 15),
                         frequency_type="every_x_months", interval_months=3)
    yearly = add_bill(db, user_id, "Acme Membership", 120.0, date(2026, 2, 28), frequency_type="yearly")

    assert due(db, user_id, quarterly, date(2026, 1, 1), date(2026, 12, 31)) == [
        date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15)
    ]
    assert due(db, user_id, yearly, date(2026, 1, 1), date(2028, 12, 31)) == [
        date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 28)
    ]


def test_weekly_and_biweekly_bills_step_from_the_first_due_date(db, user_id):
    weekly = add_bill(db, user_id, "Acme Lawn", 20.0, date(2026, 1, 5), frequency_type="weekly")
    biweekly = add_bill(db, user_id, "Example Employer", 900.0, date(2026, 1, 9), kind="income", frequency_type="biweekly")

    assert due(db, user_id, weekly, date(2026, 1, 1), date(2026, 1, 31)) == [
        date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)
    ]
    assert due(db, user_id, biweekly, date(2026, 1, 1), date(2026, 2, 10)) == [
        date(2026, 1, 9), date(2026, 1, 23), date(2026, 2, 6)
    ]


def test_semi_monthly_keeps_its_two_explicit_days(db, user_id):
    bill = add_bill(db, user_id, "Example Employer", 900.0, date(2026, 1, 1), kind="income", frequency_type="semimonthly")

    assert due(db, user_id, bill, date(2026, 1, 1), date(2026, 2, 28)) == [
        date(2026, 1, 1), date(2026, 1, 15), date(2026, 2, 1), date(2026, 2, 15)
    ]


def test_nothing_is_due_before_the_first_due_date_or_after_the_end_date(db, user_id):
    bill = add_bill(db, user_id, "Acme Utilities", 40.0, date(2026, 3, 10))
    db.execute("UPDATE bills SET end_date = ? WHERE id = ?", [date(2026, 5, 31), bill])

    assert due(db, user_id, bill, date(2026, 1, 1), date(2026, 12, 31)) == [
        date(2026, 3, 10), date(2026, 4, 10), date(2026, 5, 10)
    ]


# --- The first due date a v0.4.0 item gets ------------------------------------

def new_dates(first, frequency, interval, through):
    """The v0.5.0 rule, written out independently: the first due date's day."""
    step = {"monthly": 1, "yearly": 12}.get(frequency, interval or 1)
    found, year, month = [], first.year, first.month
    while date(year, month, 1) <= through:
        candidate = date(year, month, min(first.day, calendar.monthrange(year, month)[1]))
        if first <= candidate <= through:
            found.append(candidate)
        index = year * 12 + month - 1 + step
        year, month = index // 12, index % 12 + 1
    return found


CASES = [
    (start, frequency, interval, day)
    for start in (date(2026, 1, 5), date(2026, 1, 25), date(2026, 1, 31), date(2026, 2, 10), date(2026, 2, 28),
                  date(2026, 4, 30), date(2027, 11, 30), date(2028, 2, 29), date(2026, 12, 31))
    for frequency, interval in (("monthly", 1), ("yearly", 1), ("every_x_months", 2), ("every_x_months", 3),
                                ("every_x_months", 6), ("every_x_months", 5))
    for day in (None, 1, 5, 15, 20, 28, 29, 30, 31)
]


@pytest.mark.parametrize("start, frequency, interval, day", CASES)
def test_a_migrated_item_keeps_the_schedule_it_had(start, frequency, interval, day):
    first = migrations.first_due_date_from_v0_4_0(start, frequency, interval, day)
    old = v0_4_0_dates(start, frequency, interval, day, 1, 15, THROUGH)
    new = new_dates(first, frequency, interval, THROUGH)

    assert [when for when in new if when >= start] == old
    assert all(when < start for when in new if when not in old)


@pytest.mark.parametrize("frequency", ["weekly", "biweekly", "semimonthly"])
def test_a_schedule_that_never_used_the_day_keeps_its_start(frequency):
    assert migrations.first_due_date_from_v0_4_0(date(2026, 1, 9), frequency, 1, 20) == date(2026, 1, 9)


def test_a_day_the_start_month_holds_is_not_moved_earlier():
    assert migrations.first_due_date_from_v0_4_0(date(2026, 1, 5), "monthly", 1, 20) == date(2026, 1, 20)
    assert migrations.first_due_date_from_v0_4_0(date(2026, 1, 25), "monthly", 1, 20) == date(2026, 2, 20)


def test_a_day_the_start_month_cannot_hold_moves_to_the_nearest_earlier_date_that_does():
    assert migrations.first_due_date_from_v0_4_0(date(2026, 2, 10), "monthly", 1, 31) == date(2026, 1, 31)
