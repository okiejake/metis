"""A plain-text summary of a household's generated rows: the numbers wiki pages use."""

import math
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .model import TO_ASSIGN, Household, Rows

ZERO = Decimal("0")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _months(start: date, end: date) -> List[str]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _events(rows: Rows) -> Dict[str, List[Tuple[date, int, Decimal]]]:
    """Every change to every budget's balance, keyed by budget key: (date, order, change)."""
    keys = {b["id"]: b["key"] for b in rows["budgets"]}
    expected_budget = {e["id"]: e["budget_id"] for e in rows["expected_transactions"]}
    posted = {t["id"]: t for t in rows["transactions"]}
    events: Dict[str, List[Tuple[date, int, Decimal]]] = defaultdict(list)
    for a in rows["accounts"]:
        if a["on_budget"] and a["opening_balance"] is not None:
            events[TO_ASSIGN].append((a["opening_date"], 0, a["opening_balance"]))
    for a in rows["assignments"]:
        events[keys[a["from_budget_id"]]].append((a["assigned_on"], 1, -a["amount"]))
        events[keys[a["to_budget_id"]]].append((a["assigned_on"], 1, a["amount"]))
    for line in rows["transaction_lines"]:
        t = posted[line["transaction_id"]]
        budget_id = line["budget_id"] if line["budget_id"] is not None else expected_budget[line["expected_transaction_id"]]
        key = keys[budget_id] if budget_id is not None else TO_ASSIGN
        events[key].append((t["posted_on"], 2, line["amount"] if t["amount"] > 0 else -line["amount"]))
    return events


def summary(h: Household, rows: Rows, window: Optional[Tuple[date, date]] = None) -> str:
    """`window` gives the dates to show when the rows were shifted; it defaults to the household's own."""
    start, end = window or (h.start, h.end)
    names = {b["key"]: b["name"] for b in rows["budgets"]}
    events = _events(rows)

    income: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    spent: Dict[str, Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for key, changes in events.items():
        for day, order, change in changes:
            if order != 2:
                continue
            month = f"{day:%Y-%m}"
            if key == TO_ASSIGN and change > 0:
                income[month] += change
            elif change < 0:
                spent[month][key] -= change

    lines = [f"{h.name}  {start} to {end}", ""]
    for month in _months(start, end):
        by_budget = spent[month]
        lines.append(f"{month}  income {_money(income[month])}  spent {_money(sum(by_budget.values(), ZERO))}")
        if by_budget:
            ranked = sorted(by_budget.items(), key=lambda item: (-item[1], item[0]))
            lines.append("  " + " · ".join(f"{names[key]} {_money(amount)}" for key, amount in ranked))

    balances = {key: sum((change for _d, _o, change in changes), ZERO) for key, changes in events.items()}
    lines += ["", "balances"]
    for b in rows["budgets"]:
        lines.append(f"  {b['name']}  {_money(balances.get(b['key'], ZERO))}")

    lines += ["", "funds"]
    recent = set(_months(start, end)[-3:])
    for b in rows["budgets"]:
        target = b["target_amount"]
        if target is None:
            continue
        running, reached = ZERO, None
        for day, _order, change in sorted(events[b["key"]], key=lambda e: (e[0], e[1])):
            running += change
            if reached is None and running >= target:
                reached = day
        balance = balances.get(b["key"], ZERO)
        head = f"  {b['name']}  {_money(balance)} of {_money(target)}"
        if reached is not None:
            lines.append(f"{head}  reached {reached}")
            continue
        monthly = sum((change for day, order, change in events[b["key"]]
                       if order == 1 and change > 0 and f"{day:%Y-%m}" in recent), ZERO) / 3
        if monthly <= 0:
            lines.append(f"{head}  not on track")
            continue
        ahead = math.ceil((target - balance) / monthly)
        year, month = divmod(end.year * 12 + end.month - 1 + ahead, 12)
        lines.append(f"{head}  projected {year:04d}-{month + 1:02d}")
    return "\n".join(lines) + "\n"
