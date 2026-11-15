"""What a household file describes, once loaded: the Metis 1.0 objects it uses."""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

TO_ASSIGN = "to-assign"
OWN_CLASSES = {"asset", "liability"}
CENT = Decimal("0.01")

# Plain rows per table of the data map, keyed by column name.
Rows = Dict[str, List[dict]]


def money(value) -> Decimal:
    """An amount, exact to the cent."""
    return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


def _year_month(start: date, month: int) -> Tuple[int, int]:
    index = start.month - 1 + month - 1
    return start.year + index // 12, index % 12 + 1


def month_day(start: date, month: int, day: int) -> date:
    """A day in the household's `month`, counting the start's month as 1."""
    year, mon = _year_month(start, month)
    return date(year, mon, day)


@dataclass(frozen=True)
class Account:
    key: str
    name: str
    type: str
    cls: str
    on_budget: bool
    parent: Optional[str] = None
    default_budget: Optional[str] = None
    url: Optional[str] = None
    opening_balance: Optional[Decimal] = None
    opening_date: Optional[date] = None
    paid_on: Optional[int] = None
    paid_from: Optional[str] = None


@dataclass(frozen=True)
class Fill:
    basis: str                      # "amount" or "income_percent"
    value: Optional[Decimal]
    every: Optional[str]            # repeat rule for "amount" fills


@dataclass(frozen=True)
class Budget:
    key: str
    name: str
    parent: Optional[str] = None
    priority: Optional[int] = None
    fill: Optional[Fill] = None
    target: Optional[Decimal] = None
    target_date: Optional[date] = None
    rolls_over: bool = False

    @property
    def spending(self) -> bool:
        """Filled by amount with no priority: groceries, rent, the car."""
        return self.priority is None and self.fill is not None and self.fill.basis == "amount"


@dataclass(frozen=True)
class Expected:
    key: str
    name: str
    type: str
    from_account: str
    to_account: str
    budget: Optional[str]
    amount: Decimal
    starts: date
    ends: Optional[date]
    repeats: str


@dataclass(frozen=True)
class Spending:
    budget: str
    at: Tuple[str, ...]
    on: str
    per_month: int


@dataclass(frozen=True)
class Line:
    amount: Decimal
    budget: Optional[str] = None
    expected: Optional[str] = None


@dataclass(frozen=True)
class Moment:
    key: str
    on: date
    account: str
    counterparty: Optional[str]
    amount: Decimal
    description: str
    lines: Tuple[Line, ...]
    overspend: bool = False


@dataclass(frozen=True)
class StoryAssignment:
    on: date
    from_budget: str
    to_budget: str
    amount: Decimal


@dataclass(frozen=True)
class Household:
    key: str
    name: str
    shows: Tuple[str, ...]
    seed: int
    start: date
    months: int
    accounts: Dict[str, Account]
    budgets: Dict[str, Budget]
    expected: Dict[str, Expected]
    spending: Tuple[Spending, ...]
    moments: Tuple[Moment, ...]
    assignments: Tuple[StoryAssignment, ...]

    @property
    def end(self) -> date:
        year, mon = _year_month(self.start, self.months)
        return date(year, mon, calendar.monthrange(year, mon)[1])

    def own(self, account: str) -> bool:
        return self.accounts[account].cls in OWN_CLASSES

    def on_budget(self, account: str) -> bool:
        return self.accounts[account].on_budget
