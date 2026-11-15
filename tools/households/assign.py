"""Assign money the way Metis would propose and the household would confirm.

The engine replays a household one day at a time, keeping a balance for every
budget, to assign included, so every dollar is accounted for. The order of a
day is in docs/superpowers/specs/2026-11-14-households-design.md.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .errors import HouseholdError
from .model import TO_ASSIGN, Budget, Household, Rows, money
from .schedules import occurrences

ZERO = Decimal("0")


def run_assignments(h: Household, rows: Rows) -> List[dict]:
    """Assignment rows (without ids), in the order they happen."""
    budget_keys = {b["id"]: b["key"] for b in rows["budgets"]}
    budget_ids = {key: number for number, key in budget_keys.items()}
    expected_budget = {e["id"]: e["budget_id"] for e in rows["expected_transactions"]}
    lines_by_transaction: Dict[int, list] = defaultdict(list)
    for line in rows["transaction_lines"]:
        lines_by_transaction[line["transaction_id"]].append(line)
    transactions_by_day: Dict[date, list] = defaultdict(list)
    for t in rows["transactions"]:
        transactions_by_day[t["posted_on"]].append(t)
    stories_by_day: Dict[date, list] = defaultdict(list)
    for story in h.assignments:
        stories_by_day[story.on].append(story)
    overspend = {(m.on, m.description, m.amount) for m in h.moments if m.overspend}

    spending = [b for b in h.budgets.values() if b.spending]
    percent = [b for b in h.budgets.values()
               if b.priority is None and b.fill is not None and b.fill.basis == "income_percent"]
    order = {key: index for index, key in enumerate(h.budgets)}
    ranked = sorted((b for b in h.budgets.values() if b.priority is not None and b.fill is not None),
                    key=lambda b: (b.priority, order[b.key]))
    periods = {b.key: occurrences(b.fill.every, h.start, h.end) for b in ranked if b.fill.basis == "amount"}

    balance: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    given_month: Dict[Tuple[str, int, int], Decimal] = defaultdict(lambda: ZERO)
    given_period: Dict[Tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    out: List[dict] = []

    def move(day: date, source: str, target: str, amount: Decimal, note: str) -> Decimal:
        if amount <= 0:
            return ZERO
        balance[source] -= amount
        balance[target] += amount
        out.append({"from_budget_id": budget_ids[source], "to_budget_id": budget_ids[target], "amount": amount,
                    "assigned_on": day, "note": note})
        return amount

    def headroom(b: Budget, want: Decimal) -> Decimal:
        if b.target is not None:
            want = min(want, max(b.target - balance[b.key], ZERO))
        return min(want, max(balance[TO_ASSIGN], ZERO))

    def fill_spending(day: date, note: str) -> None:
        for b in spending:
            month = (b.key, day.year, day.month)
            given = move(day, TO_ASSIGN, b.key, headroom(b, b.fill.value - given_month[month]), note)
            given_month[month] += given

    def fill_from_income(day: date, income: Decimal) -> None:
        fill_spending(day, "income")
        for b in percent:
            move(day, TO_ASSIGN, b.key, headroom(b, money(income * b.fill.value / 100)), "income")
        for b in ranked:
            if b.fill.basis == "income_percent":
                move(day, TO_ASSIGN, b.key, headroom(b, money(income * b.fill.value / 100)), "income")
                continue
            starts = periods[b.key]
            index = bisect_right(starts, day) - 1
            if index < 0:
                continue
            period = (b.key, starts[index])
            given_period[period] += move(day, TO_ASSIGN, b.key, headroom(b, b.fill.value - given_period[period]),
                                         "income")

    def line_budget(line: dict) -> Optional[str]:
        budget_id = line["budget_id"] if line["budget_id"] is not None else expected_budget[line["expected_transaction_id"]]
        return budget_keys[budget_id] if budget_id is not None else None

    day = h.start
    while day <= h.end:
        if day == h.start:
            # The opening cash of on-budget accounts, less what's owed on on-budget cards.
            balance[TO_ASSIGN] += sum((a.opening_balance for a in h.accounts.values()
                                       if a.on_budget and a.opening_balance is not None), ZERO)
        if day.day == 1:
            if day != h.start:
                for b in spending:
                    if not b.rolls_over and balance[b.key] > 0:
                        move(day, b.key, TO_ASSIGN, balance[b.key], "leftover")
            fill_spending(day, "month start")
        for story in stories_by_day[day]:
            move(day, story.from_budget, story.to_budget, story.amount, "story")
        for t in transactions_by_day[day]:
            income = ZERO
            for line in lines_by_transaction[t["id"]]:
                key = line_budget(line)
                if key is None or key == TO_ASSIGN:
                    change = line["amount"] if t["amount"] > 0 else -line["amount"]
                    balance[TO_ASSIGN] += change
                    income += max(change, ZERO)
                elif t["amount"] > 0:
                    balance[key] += line["amount"]
                else:
                    short = line["amount"] - balance[key]
                    if short > 0 and (t["posted_on"], t["description"], t["amount"]) not in overspend:
                        if h.budgets[key].spending and balance[TO_ASSIGN] >= short:
                            move(day, TO_ASSIGN, key, short, "top up")
                        else:
                            raise HouseholdError(f"{h.key}: budget {key}", f"goes below zero on {day}")
                    balance[key] -= line["amount"]
            if income > 0:
                fill_from_income(day, income)
        day += timedelta(days=1)
    return out
