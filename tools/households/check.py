"""Checks that fail loudly, naming the household item at fault."""

from typing import Iterable, Tuple

from tools.privacy_guard import scan_text

from .errors import HouseholdError
from .model import TO_ASSIGN, Household


def _texts(h: Household) -> Iterable[Tuple[str, str]]:
    yield f"{h.key}: household", h.name
    for a in h.accounts.values():
        yield f"{h.key}: account {a.key}", a.name
    for b in h.budgets.values():
        yield f"{h.key}: budget {b.key}", b.name
    for e in h.expected.values():
        yield f"{h.key}: expected {e.key}", e.name
    for m in h.moments:
        yield f"{h.key}: {m.key}", m.description


def check_household(h: Household) -> None:
    def account(where, key, own=False):
        if key not in h.accounts:
            raise HouseholdError(where, f"unknown account '{key}'")
        if own and not h.own(key):
            raise HouseholdError(where, f"'{key}' isn't one of the household's own accounts")

    def budget(where, key, allow_to_assign=False):
        if key not in h.budgets and not (allow_to_assign and key == TO_ASSIGN):
            raise HouseholdError(where, f"unknown budget '{key}'")

    for a in h.accounts.values():
        where = f"{h.key}: account {a.key}"
        if a.parent is not None:
            account(where, a.parent)
        if a.default_budget is not None:
            budget(where, a.default_budget)
        if a.type == "credit-card":
            if not isinstance(a.paid_on, int) or not 1 <= a.paid_on <= 28:
                raise HouseholdError(where, "a card needs paid_on, a day from 1 to 28")
            if a.paid_from is None or a.paid_from not in h.accounts or not (h.own(a.paid_from) and h.on_budget(a.paid_from)):
                raise HouseholdError(where, "a card needs paid_from, one of the household's on-budget accounts")

    for b in h.budgets.values():
        where = f"{h.key}: budget {b.key}"
        if b.parent is not None:
            budget(where, b.parent)
        if b.fill is not None:
            if b.fill.basis not in ("amount", "income_percent"):
                raise HouseholdError(where, f"unknown fill basis '{b.fill.basis}'")
            if b.fill.value is None:
                raise HouseholdError(where, "its fill needs a value")
            if b.fill.basis == "amount" and not b.fill.every:
                raise HouseholdError(where, "an amount fill needs a schedule (every)")

    for e in h.expected.values():
        where = f"{h.key}: expected {e.key}"
        account(where, e.from_account)
        account(where, e.to_account)
        if not (h.own(e.from_account) or h.own(e.to_account)):
            raise HouseholdError(where, "has none of the household's own accounts on either side")
        if e.budget is not None:
            budget(where, e.budget)
            if h.accounts[e.from_account].cls == "income":
                raise HouseholdError(where, "income has no budget; it lands in to assign")

    for s in h.spending:
        where = f"{h.key}: spending {s.budget}"
        budget(where, s.budget)
        if not h.budgets[s.budget].spending:
            raise HouseholdError(where, "everyday spending needs a budget filled by amount, with no priority")
        for merchant in s.at:
            account(where, merchant)
        account(where, s.on, own=True)
        if not h.on_budget(s.on):
            raise HouseholdError(where, f"'{s.on}' isn't on budget")

    for m in h.moments:
        where = f"{h.key}: {m.key}"
        account(where, m.account, own=True)
        if m.counterparty is not None:
            account(where, m.counterparty)
        for line in m.lines:
            if (line.budget is None) == (line.expected is None):
                raise HouseholdError(where, "a line points at exactly one of a budget or an expected transaction")
            if line.budget is not None:
                budget(where, line.budget, allow_to_assign=True)
            elif line.expected not in h.expected:
                raise HouseholdError(where, f"unknown expected transaction '{line.expected}'")
        transfer = (m.counterparty is not None and h.own(m.counterparty)
                    and h.on_budget(m.account) and h.on_budget(m.counterparty))
        if transfer:
            if m.lines:
                raise HouseholdError(where, "a transfer between on-budget accounts has no lines")
        elif sum(line.amount for line in m.lines) != abs(m.amount):
            raise HouseholdError(where, "its lines don't add up to its amount")

    for number, a in enumerate(h.assignments, start=1):
        where = f"{h.key}: assignment {number}"
        budget(where, a.from_budget, allow_to_assign=True)
        budget(where, a.to_budget, allow_to_assign=True)
        if a.amount <= 0:
            raise HouseholdError(where, "moves a positive amount")

    for where, text in _texts(h):
        for finding in scan_text(text):
            raise HouseholdError(where, f"privacy guard: {finding.rule}")
