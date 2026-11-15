"""Repeat rules (RFC 5545) as dates, and moving a household's dates in time."""

from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from dateutil.rrule import rrulestr

from .model import Rows

# The date columns in generated rows, by table.
DATE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "accounts": ("opening_date",),
    "budgets": ("target_date",),
    "expected_transactions": ("starts_at", "ends_at"),
    "transactions": ("posted_on",),
    "assignments": ("assigned_on",),
}


def occurrences(repeats: str, starts: date, until: date, ends: Optional[date] = None) -> List[date]:
    """Every date the rule gives from `starts` through `until` (or `ends`, if sooner).

    A monthly or yearly rule anchored on the 29th to 31st lands on the last day
    of a shorter month instead of skipping it, as a bill due on the 31st does.
    """
    last = min(until, ends) if ends else until
    if last < starts:
        return []
    first = datetime.combine(starts, time())
    rule = rrulestr(repeats, dtstart=first)
    parts = repeats.upper()
    freq = next((p.split("=", 1)[1] for p in parts.split(";") if p.startswith("FREQ=")), "")
    if freq in ("MONTHLY", "YEARLY") and starts.day > 28 and not any(
            k in parts for k in ("BYMONTHDAY", "BYDAY", "BYSETPOS")):
        extra = {"bymonthday": tuple(range(28, starts.day + 1)), "bysetpos": -1}
        if freq == "YEARLY":
            extra["bymonth"] = starts.month
        rule = rule.replace(**extra)
    return [moment.date() for moment in rule.between(first, datetime.combine(last, time()), inc=True)]


def shift(rows: Rows, end: date, today: date) -> Rows:
    """A copy of `rows` with every date moved so that `end` falls on `today`."""
    delta = today - end
    moved: Rows = {}
    for table, records in rows.items():
        columns = DATE_COLUMNS.get(table, ())
        moved[table] = [
            {column: (value + delta if column in columns and value is not None else value)
             for column, value in record.items()}
            for record in records]
    return moved
