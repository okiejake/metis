"""Small invented households and readers shared by the households tests."""

from decimal import Decimal
from pathlib import Path

from tools.households.load import load_household

MINIMAL = """
[household]
key = "mini"
name = "the Mini household"
shows = ["a test household"]
seed = 7
start = 2026-01-01
months = 2

[[account]]
key = "checking"
name = "Example Bank checking"
type = "checking"
opening_balance = 1000

[[account]]
key = "card"
name = "Example Card"
type = "credit-card"
paid_on = 25
paid_from = "checking"

[[account]]
key = "loan"
name = "Example Loan"
type = "loan"
opening_balance = -5000

[[account]]
key = "employer"
name = "Example Employer"
type = "employer"

[[account]]
key = "grocer"
name = "Acme Grocery"
type = "merchant"
default_budget = "groceries"

[[account]]
key = "landlord"
name = "Example Rentals"
type = "landlord"

[[budget]]
use = "groceries"
fill = { value = 300 }

[[budget]]
use = "housing"
fill = { value = 500 }

[[budget]]
use = "loans"
fill = { value = 100 }

[[expected]]
use = "pay-semimonthly"
from = "employer"
to = "checking"
amount = 1000.00
starts = 2026-01-01

[[expected]]
use = "rent"
from = "checking"
to = "landlord"
amount = 500.00
starts = 2026-01-01

[[expected]]
use = "student-loan"
from = "checking"
to = "loan"
amount = 100.00
starts = 2026-01-10
"""

# A card purchase at the grocer, month 1 day 5. Fill in {lines}.
MOMENT = """
[[moment]]
month = 1
day = 5
account = "card"
counterparty = "grocer"
amount = -50
description = "acme grocery"
lines = [{lines}]
"""

# Money moved from checking to another own account, month 1 day 6. Fill in {to} and {lines}.
TRANSFER = """
[[moment]]
month = 1
day = 6
account = "checking"
counterparty = "{to}"
amount = -50
description = "transfer"
lines = {lines}
"""


def household_from(tmp_path: Path, text: str):
    path = tmp_path / "household.toml"
    path.write_text(text)
    return load_household(path)


def key_id(rows, table: str, key: str) -> int:
    """The id of the row in `table` with this key."""
    return next(r["id"] for r in rows[table] if r.get("key") == key)


def lines_of(rows, transaction: dict) -> list:
    return [line for line in rows["transaction_lines"] if line["transaction_id"] == transaction["id"]]


# --- Fragments for the spending and assignment tests ---------------------------

SPENDING = """
[[spending]]
budget = "groceries"
at = ["grocer"]
on = "card"
per_month = 4
"""

# -350 at the grocer on month 1 day 5: 50 more than groceries' 300 fill.
BIG_GROCERY = """
[[moment]]
month = 1
day = 5
account = "card"
counterparty = "grocer"
amount = -350
description = "acme grocery"
lines = [{ budget = "groceries", amount = 350 }]
"""

# -5000 at the grocer on month 1 day 2: more than to assign can ever cover.
BIG_PURCHASE = """
[[moment]]
month = 1
day = 2
account = "card"
counterparty = "grocer"
amount = -5000
description = "acme grocery"
lines = [{ budget = "groceries", amount = 5000 }]
"""

TAXES_25 = """
[[budget]]
use = "taxes"
fill = { value = 25 }
"""

# Client-style payments with no plan: 1000.00 and 333.33 into to assign.
PAYMENTS = """
[[moment]]
month = 1
day = 3
account = "checking"
counterparty = "employer"
amount = 1000
description = "example employer bonus"
lines = [{ budget = "to-assign", amount = 1000 }]

[[moment]]
month = 1
day = 20
account = "checking"
counterparty = "employer"
amount = 333.33
description = "example employer bonus"
lines = [{ budget = "to-assign", amount = 333.33 }]
"""

LONG_MINIMAL = MINIMAL.replace("months = 2", "months = 12")

KIDS_FUND = """
[[budget]]
key = "kids-fund"
name = "kids' fund"
priority = 2
fill = { basis = "amount", value = 50, every = "FREQ=MONTHLY" }
target = 500
rolls_over = true
"""

# 100 spent from the kids' fund on month 11 day 20.
DRAW = """
[[moment]]
month = 11
day = 20
account = "card"
counterparty = "grocer"
amount = -100
description = "acme grocery"
lines = [{ budget = "kids-fund", amount = 100 }]
"""

TWO_FUNDS = """
[[budget]]
key = "first"
name = "first"
priority = 1
fill = { basis = "amount", value = 900, every = "FREQ=MONTHLY" }
rolls_over = true

[[budget]]
key = "second"
name = "second"
priority = 2
fill = { basis = "amount", value = 900, every = "FREQ=MONTHLY" }
rolls_over = true
"""

EXTRA_BILL = """
[[expected]]
key = "extra"
name = "extra"
type = "subscription"
from = "checking"
to = "landlord"
budget = "housing"
amount = 10.00
starts = 2026-01-20
repeats = "FREQ=MONTHLY"
"""


# --- Readers ------------------------------------------------------------------

def _budget_key(rows, budget_id):
    return next(b["key"] for b in rows["budgets"] if b["id"] == budget_id)


def month_spend(rows, budget: str, year: int, month: int):
    """What was spent from a budget in a month: its lines on money going out."""
    budget_id = key_id(rows, "budgets", budget)
    out = {t["id"] for t in rows["transactions"]
           if t["amount"] < 0 and (t["posted_on"].year, t["posted_on"].month) == (year, month)}
    return sum((line["amount"] for line in rows["transaction_lines"]
                if line["budget_id"] == budget_id and line["transaction_id"] in out), Decimal("0"))


def fund_balances(rows, budget: str) -> list:
    """A budget's running balance after each change, in date order."""
    budget_id = key_id(rows, "budgets", budget)
    signs = {t["id"]: (t["posted_on"], 1 if t["amount"] > 0 else -1) for t in rows["transactions"]}
    events = [(a["assigned_on"], 0, a["amount"] if a["to_budget_id"] == budget_id else -a["amount"])
              for a in rows["assignments"] if budget_id in (a["to_budget_id"], a["from_budget_id"])]
    events += [(signs[line["transaction_id"]][0], 1, signs[line["transaction_id"]][1] * line["amount"])
               for line in rows["transaction_lines"] if line["budget_id"] == budget_id]
    balance, out = Decimal("0"), []
    for _day, _order, change in sorted(events, key=lambda e: (e[0], e[1])):
        balance += change
        out.append(balance)
    return out


def fund_total(rows, budget: str):
    budget_id = key_id(rows, "budgets", budget)
    return sum((a["amount"] for a in rows["assignments"] if a["to_budget_id"] == budget_id), Decimal("0"))


def transactions_by_source(rows) -> dict:
    """Each expected key, or spending budget, mapped to its (posted_on, amount) list. Card payments have no lines."""
    expected = {e["id"]: e["key"] for e in rows["expected_transactions"]}
    out: dict = {}
    for t in rows["transactions"]:
        lines = lines_of(rows, t)
        if not lines:
            continue
        line = lines[0]
        source = (expected[line["expected_transaction_id"]] if line["expected_transaction_id"]
                  else "spending:" + _budget_key(rows, line["budget_id"]))
        out.setdefault(source, []).append((t["posted_on"], t["amount"]))
    return out
