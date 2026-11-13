"""The saved date range: a rule that resolves to dates for a given today.

A rule has a grain (day, month, year, decade), a mode (absolute or relative) and
a start and end. Absolute values name a unit (`2026-11-10`, `2026-11`, `2026`,
`2020` for the 2020s). Relative values count units from the current one, so a
relative rule slides forward with the calendar. Every page reads its range
through resolve_rule; `static/date-range.js` mirrors it for the live popup, and
both are pinned to `tests/fixtures/date_range_cases.json`.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple, Union

GRAINS = ("day", "month", "year", "decade")
MODES = ("absolute", "relative")
RELATIVE_LIMITS = {"day": 3650, "month": 240, "year": 50, "decade": 10}
MAX_SPAN_DAYS = 36525  # 100 years


@dataclass(frozen=True)
class RangeRule:
    grain: str
    mode: str
    start: Union[str, int]
    end: Union[str, int]


DEFAULT_RULE = RangeRule("month", "relative", -3, 3)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_month(year: int, month: int, count: int) -> Tuple[int, int]:
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def _parse_absolute(grain: str, value: str) -> Tuple[date, date]:
    """The first and last day of the unit an absolute value names."""
    if grain == "day":
        day = date.fromisoformat(value)
        return day, day
    if grain == "month":
        year_text, _, month_text = value.partition("-")
        if len(year_text) != 4 or len(month_text) != 2:
            raise ValueError("Choose a month like 2026-11")
        year, month = int(year_text), int(month_text)
        if not 1 <= month <= 12:
            raise ValueError("Choose a month like 2026-11")
        return date(year, month, 1), _month_end(year, month)
    year = int(value)
    if not 1 <= year <= 9989:
        raise ValueError("Choose a year like 2026")
    if grain == "year":
        return date(year, 1, 1), date(year, 12, 31)
    if year % 10:
        raise ValueError("Choose a decade by its first year, like 2020")
    return date(year, 1, 1), date(year + 9, 12, 31)


def _relative_unit(grain: str, offset: int, today: date) -> Tuple[date, date]:
    """The first and last day of the unit `offset` units from today's."""
    if grain == "day":
        day = today + timedelta(days=offset)
        return day, day
    if grain == "month":
        year, month = _shift_month(today.year, today.month, offset)
        return date(year, month, 1), _month_end(year, month)
    if grain == "year":
        year = today.year + offset
        return date(year, 1, 1), date(year, 12, 31)
    first = today.year // 10 * 10 + offset * 10
    return date(first, 1, 1), date(first + 9, 12, 31)


def resolve_rule(rule: RangeRule, today: date) -> Tuple[date, date]:
    """The first day of the start unit to the last day of the end unit."""
    if rule.mode == "absolute":
        return _parse_absolute(rule.grain, str(rule.start))[0], _parse_absolute(rule.grain, str(rule.end))[1]
    return (
        _relative_unit(rule.grain, int(rule.start), today)[0],
        _relative_unit(rule.grain, int(rule.end), today)[1],
    )


def _build(grain: str, mode: str, start: object, end: object) -> RangeRule:
    """A checked rule, or ValueError with a message fit to show the user."""
    if grain not in GRAINS:
        raise ValueError("Choose a grain: day, month, year or decade")
    if mode not in MODES:
        raise ValueError("Choose a mode: absolute or relative")
    if mode == "relative":
        try:
            start_value, end_value = int(str(start).strip()), int(str(end).strip())
        except ValueError:
            raise ValueError("Relative start and end must be a number") from None
        limit = RELATIVE_LIMITS[grain]
        if abs(start_value) > limit or abs(end_value) > limit:
            raise ValueError(f"That's too far: up to {limit} {grain}s either way")
        rule = RangeRule(grain, mode, start_value, end_value)
    else:
        start_value, end_value = str(start).strip(), str(end).strip()
        try:
            _parse_absolute(grain, start_value)
            _parse_absolute(grain, end_value)
        except ValueError as error:
            message = str(error)
            if not message.startswith("Choose"):
                message = f"Choose a {grain} for the start and end"
            raise ValueError(message) from None
        rule = RangeRule(grain, mode, start_value, end_value)
    # Which value comes first doesn't depend on today, for any grain.
    range_start, range_end = resolve_rule(rule, date.today())
    if range_start > range_end:
        raise ValueError("The start is after the end")
    return rule


def rule_from_form(grain: str, mode: str, start: str, end: str) -> RangeRule:
    return _build(grain.strip(), mode.strip(), start, end)


def rule_from_saved(value: object) -> Optional[RangeRule]:
    """A saved rule, or the older two-date shape read as day/absolute. Anything
    malformed reads as None, so a bad setting never breaks a page."""
    if not isinstance(value, dict):
        return None
    try:
        if "grain" in value:
            if value.get("mode") == "relative" and not all(
                isinstance(value.get(key), int) and not isinstance(value.get(key), bool) for key in ("start", "end")
            ):
                return None
            return _build(str(value["grain"]), str(value.get("mode")), value.get("start"), value.get("end"))
        if "start" in value and "end" in value:
            return _build("day", "absolute", value["start"], value["end"])
    except (ValueError, TypeError):
        return None
    return None


def rule_to_saved(rule: RangeRule) -> dict:
    return {"grain": rule.grain, "mode": rule.mode, "start": rule.start, "end": rule.end}


def format_range(start: date, end: date) -> str:
    """`Jan 1, 2026 – Dec 31, 2026`, as the header shows it. formatRange in
    static/date-range.js writes the same text while the popup is open."""
    return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
