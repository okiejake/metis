"""Turn a household into plain rows per table of the Metis 1.0 data map.

Ids are 1-based per table. Randomness comes from one stream per item, seeded
by the household's seed and the item's key, so adding an item never changes
another item's rows.
"""

import calendar
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .assign import run_assignments
from .check import check_rows
from .model import TO_ASSIGN, Household, Rows, money, month_day
from .schedules import occurrences


@dataclass
class Draft:
    """A transaction before it has an id."""
    posted_on: date
    rank: int                       # income 0, everything else 1: income lands first each day
    source: str                     # "expected:<key>", "moment:<key>", "spending:<budget>", "card:<key>"
    seq: int
    account: str
    counterparty: Optional[str]
    amount: Decimal
    description: str
    lines: List[Tuple[Decimal, Optional[str], Optional[str]]] = field(default_factory=list)  # (amount, budget, expected)


def stream(h: Household, kind: str, key: str) -> random.Random:
    return random.Random(f"{h.seed}:{kind}:{key}")


def _record(h: Household, from_account: str, to_account: str, amount: Decimal) -> Tuple[str, str, Decimal]:
    """Which own account records a movement, its counterparty, and the signed amount."""
    if h.own(from_account):
        return from_account, to_account, -amount
    return to_account, from_account, amount


def _transfer(h: Household, account: str, counterparty: Optional[str]) -> bool:
    return (counterparty is not None and h.own(counterparty)
            and h.on_budget(account) and h.on_budget(counterparty))


def _from_expected(h: Household) -> List[Draft]:
    drafts = []
    for e in h.expected.values():
        income = h.accounts[e.from_account].cls == "income"
        rng = stream(h, "expected", e.key)
        account, counterparty, amount = _record(h, e.from_account, e.to_account, e.amount)
        lines = [] if _transfer(h, account, counterparty) else [(e.amount, None, e.key)]
        for seq, due in enumerate(occurrences(e.repeats, e.starts, h.end, e.ends)):
            posted = due if income else due + timedelta(days=rng.randint(0, 2))
            if posted > h.end:
                continue
            drafts.append(Draft(posted, 0 if income else 1, f"expected:{e.key}", seq, account, counterparty,
                                amount, e.name, list(lines)))
    return drafts


def _from_moments(h: Household) -> List[Draft]:
    return [Draft(m.on, 0 if m.amount > 0 else 1, f"moment:{m.key}", 0, m.account, m.counterparty, m.amount,
                  m.description, [(line.amount, line.budget, line.expected) for line in m.lines])
            for m in h.moments]


def _purchases(h: Household) -> List[Draft]:
    """Everyday spending: each entry's purchases come to 85-105% of its budget's
    monthly fill, less what story moments already spent from it that month."""
    drafts = []
    for s in h.spending:
        fill = h.budgets[s.budget].fill.value
        for month in range(1, h.months + 1):
            first = month_day(h.start, month, 1)
            rng = stream(h, "spending", f"{s.budget}:{first:%Y-%m}")
            drawn = sum((line.amount for m in h.moments if m.amount < 0 and (m.on.year, m.on.month) == (first.year, first.month)
                         for line in m.lines if line.budget == s.budget), Decimal("0"))
            total = money((fill - drawn) * Decimal(str(rng.uniform(0.85, 1.05))))
            if total <= 0:
                continue
            count = s.per_month if total >= s.per_month else 1   # never a purchase under 1.00 from rounding
            weights = [Decimal(str(rng.uniform(0.5, 1.5))) for _ in range(count)]
            parts = [money(total * w / sum(weights)) for w in weights]
            parts[-1] = total - sum(parts[:-1])
            days = sorted(rng.randint(1, calendar.monthrange(first.year, first.month)[1]) for _ in range(count))
            for seq, (day, part) in enumerate(zip(days, parts)):
                merchant = rng.choice(s.at)
                drafts.append(Draft(first.replace(day=day), 1, f"spending:{s.budget}", month * 100 + seq, s.on,
                                    merchant, -part, h.accounts[merchant].name.lower(), [(part, s.budget, None)]))
    return drafts


def _card_payments(h: Household, drafts: List[Draft]) -> List[Draft]:
    """Each card is paid in full on its day for what was charged since the last payment."""
    payments = []
    for card in h.accounts.values():
        if card.type != "credit-card":
            continue
        since = h.start
        for month in range(1, h.months + 1):
            day = month_day(h.start, month, card.paid_on)
            charged = sum((d.amount for d in drafts if d.account == card.key and since <= d.posted_on < day),
                          Decimal("0"))
            since = day
            if charged < 0:
                payments.append(Draft(day, 1, f"card:{card.key}", month, card.paid_from, card.key, charged,
                                      f"{card.name.lower()} payment"))
    return payments


def _ids(items) -> Dict[str, int]:
    return {key: number for number, key in enumerate(items, start=1)}


def generate(h: Household) -> Rows:
    account_ids = _ids(h.accounts)
    budget_ids = _ids([TO_ASSIGN, *h.budgets])
    expected_ids = _ids(h.expected)

    drafts = _from_expected(h) + _from_moments(h) + _purchases(h)
    drafts += _card_payments(h, drafts)
    drafts.sort(key=lambda d: (d.posted_on, d.rank, d.source, d.seq))

    transactions, lines = [], []
    for number, d in enumerate(drafts, start=1):
        transactions.append({
            "id": number, "account_id": account_ids[d.account],
            "counterparty_account_id": account_ids[d.counterparty] if d.counterparty else None,
            "posted_on": d.posted_on, "amount": d.amount, "description": d.description})
        for amount, budget, expected in d.lines:
            lines.append({"id": len(lines) + 1, "transaction_id": number, "amount": amount,
                          "budget_id": budget_ids[budget] if budget else None,
                          "expected_transaction_id": expected_ids[expected] if expected else None})

    rows: Rows = {
        "users": [{"id": 1, "slug": h.key, "display_name": h.name}],
        "accounts": [{
            "id": account_ids[a.key], "key": a.key, "parent_id": account_ids.get(a.parent), "name": a.name,
            "class": a.cls, "type": a.type, "on_budget": a.on_budget,
            "default_budget_id": budget_ids.get(a.default_budget), "url": a.url,
            "opening_balance": a.opening_balance, "opening_date": a.opening_date} for a in h.accounts.values()],
        "budgets": [{
            "id": 1, "key": TO_ASSIGN, "parent_id": None, "name": "to assign", "priority": None, "fill_basis": None,
            "fill_value": None, "fill_rrule": None, "target_amount": None, "target_date": None, "rolls_over": True,
            "backing_account_id": None}] + [{
            "id": budget_ids[b.key], "key": b.key, "parent_id": budget_ids.get(b.parent), "name": b.name,
            "priority": b.priority, "fill_basis": b.fill.basis if b.fill else None,
            "fill_value": b.fill.value if b.fill else None, "fill_rrule": b.fill.every if b.fill else None,
            "target_amount": b.target, "target_date": b.target_date, "rolls_over": b.rolls_over,
            "backing_account_id": None} for b in h.budgets.values()],
        "expected_transactions": [{
            "id": expected_ids[e.key], "key": e.key, "name": e.name, "type": e.type,
            "from_account_id": account_ids[e.from_account], "to_account_id": account_ids[e.to_account],
            "budget_id": budget_ids.get(e.budget), "amount": e.amount, "starts_at": e.starts, "ends_at": e.ends,
            "rrule": e.repeats} for e in h.expected.values()],
        "transactions": transactions,
        "transaction_lines": lines,
    }
    # Transactions and lines have their ids first; the engine replays them in id order.
    rows["assignments"] = [{"id": number, **a} for number, a in enumerate(run_assignments(h, rows), start=1)]
    check_rows(rows, h)
    return rows
