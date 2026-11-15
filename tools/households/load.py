"""Read a household file, fill in catalog items, and check the result."""

from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from .catalog import CATALOG_PATH, HOUSEHOLDS_DIR, Catalog, load_catalog, read_toml
from .check import check_household
from .errors import HouseholdError
from .model import (Account, Budget, Expected, Fill, Household, Line, Moment, Spending, StoryAssignment,
                    money, month_day)


def _merge(base: dict, item: dict) -> dict:
    """A catalog item with the file's fields on top; `fill` merges key by key."""
    merged = {**base, **item}
    if "fill" in base or "fill" in item:
        merged["fill"] = {**base.get("fill", {}), **item.get("fill", {})}
    merged.pop("use", None)
    return merged


def _need(item: dict, field: str, where: str):
    if field not in item:
        raise HouseholdError(where, f"missing {field}")
    return item[field]


def _optional_money(value) -> Optional:
    return None if value is None else money(value)


def load_household(path: Path, catalog: Optional[Catalog] = None) -> Household:
    catalog = catalog or load_catalog()
    data = read_toml(path)
    head = _need(data, "household", str(path))
    key = _need(head, "key", str(path))
    start = _need(head, "start", key)

    accounts = {}
    for item in data.get("account", []):
        where = f"{key}: account {item.get('key', '?')}"
        kind = catalog.account_types.get(_need(item, "type", where))
        if kind is None:
            raise HouseholdError(where, f"unknown account type '{item['type']}'")
        opening = _optional_money(item.get("opening_balance"))
        accounts[_need(item, "key", where)] = Account(
            key=item["key"], name=_need(item, "name", where), type=item["type"], cls=kind["class"],
            on_budget=kind["on_budget"], parent=item.get("parent"), default_budget=item.get("default_budget"),
            url=item.get("url"), opening_balance=opening,
            opening_date=item.get("opening_date", start - timedelta(days=1)) if opening is not None else None,
            paid_on=item.get("paid_on"), paid_from=item.get("paid_from"))

    budgets = {}
    for item in data.get("budget", []):
        use = item.get("use")
        where = f"{key}: budget {item.get('key', use)}"
        if use is not None and use not in catalog.budgets:
            raise HouseholdError(where, f"unknown catalog budget '{use}'")
        merged = _merge(catalog.budgets[use], item) if use else dict(item)
        fill = merged.get("fill")
        budgets[_need(merged, "key", where)] = Budget(
            key=merged["key"], name=_need(merged, "name", where), parent=merged.get("parent"),
            priority=merged.get("priority"),
            fill=Fill(fill.get("basis", "amount"), _optional_money(fill.get("value")), fill.get("every"))
            if fill else None,
            target=_optional_money(merged.get("target")), target_date=merged.get("target_date"),
            rolls_over=bool(merged.get("rolls_over", False)))

    expected = {}
    for item in data.get("expected", []):
        use = item.get("use")
        where = f"{key}: expected {item.get('key', use)}"
        base = None
        if use is not None:
            base = catalog.bills.get(use) or catalog.incomes.get(use)
            if base is None:
                raise HouseholdError(where, f"unknown catalog bill or income '{use}'")
        merged = _merge(base, item) if base else dict(item)
        expected[_need(merged, "key", where)] = Expected(
            key=merged["key"], name=_need(merged, "name", where), type=_need(merged, "type", where),
            from_account=_need(merged, "from", where), to_account=_need(merged, "to", where),
            budget=merged.get("budget"), amount=money(_need(merged, "amount", where)),
            starts=_need(merged, "starts", where), ends=merged.get("ends"), repeats=_need(merged, "repeats", where))

    spending = []
    for item in data.get("spending", []):
        where = f"{key}: spending {item.get('budget', '?')}"
        spending.append(Spending(budget=_need(item, "budget", where), at=tuple(_need(item, "at", where)),
                                 on=_need(item, "on", where), per_month=int(_need(item, "per_month", where))))

    months = _need(head, "months", key)
    moments = []
    for number, item in enumerate(data.get("moment", []), start=1):
        where = f"{key}: moment-{number}"
        on = _day(start, months, item, where)
        lines = tuple(Line(amount=money(_need(line, "amount", where)), budget=line.get("budget"),
                           expected=line.get("expected")) for line in item.get("lines", []))
        moments.append(Moment(key=f"moment-{number}", on=on, account=_need(item, "account", where),
                              counterparty=item.get("counterparty"), amount=money(_need(item, "amount", where)),
                              description=_need(item, "description", where), lines=lines,
                              overspend=bool(item.get("overspend", False))))

    assignments = []
    for number, item in enumerate(data.get("assignment", []), start=1):
        where = f"{key}: assignment {number}"
        assignments.append(StoryAssignment(on=_day(start, months, item, where), from_budget=_need(item, "from", where),
                                           to_budget=_need(item, "to", where),
                                           amount=money(_need(item, "amount", where))))

    household = Household(
        key=key, name=_need(head, "name", key), shows=tuple(head.get("shows", [])), seed=_need(head, "seed", key),
        start=start, months=months, accounts=accounts, budgets=budgets, expected=expected,
        spending=tuple(spending), moments=tuple(moments), assignments=tuple(assignments))
    check_household(household)
    return household


def _day(start, months, item, where):
    month, day = _need(item, "month", where), _need(item, "day", where)
    if not 1 <= month <= months:
        raise HouseholdError(where, f"month {month} falls outside the household's {months} months")
    try:
        return month_day(start, month, day)
    except ValueError:
        raise HouseholdError(where, f"month {month} has no day {day}") from None


def load_all(catalog: Optional[Catalog] = None) -> List[Household]:
    """Every household in fixtures/households, sorted by key."""
    catalog = catalog or load_catalog()
    paths = [p for p in sorted(HOUSEHOLDS_DIR.glob("*.toml")) if p.resolve() != CATALOG_PATH.resolve()]
    return sorted((load_household(p, catalog) for p in paths), key=lambda h: h.key)


def find(key: str) -> Household:
    for household in load_all():
        if household.key == key:
            return household
    raise HouseholdError(key, f"unknown household '{key}'")
