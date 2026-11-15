"""Small invented households and readers shared by the households tests."""

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
