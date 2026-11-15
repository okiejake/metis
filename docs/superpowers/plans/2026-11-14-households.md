# Households Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three invented households, written as TOML against the Metis 1.0 data map, that a seeded generator turns into repeatable rows, plus a shared catalog that later feeds suggestions.

**Architecture:** `fixtures/households/` holds the catalog and one file per household. The package `tools/households/` loads and checks them, then generates plain rows per map table (no database). An assignment engine replays a household day by day, so every budget balance is accounted for. A summary command prints the numbers wiki pages use.

**Tech Stack:** Python 3.12, `tomllib` (standard library), `python-dateutil` for iCalendar repeat rules, pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-households-design.md`

## Global Constraints

- Python 3.12. The only new dependency is `python-dateutil==2.9.0.post0`, added to `requirements.txt`.
- `tools/households/` imports nothing from the 0.x app (`services`, `app`, `routes`, `web`, `inbox`, `migrations`).
- All data is invented. Names follow the `Example`, `Acme`, `Sample`, `Demo` pattern. Every name and description passes `tools.privacy_guard.scan_text`.
- Money is `Decimal`, quantized to `0.01` with `ROUND_HALF_UP`. TOML is read with `parse_float=Decimal`. Never `float`.
- Catalog display names are lower case. The catalog holds no amounts.
- Randomness: `random.Random(f"{seed}:{kind}:{key}")` per item, never the global generator. String seeds are stable across runs and processes.
- Same files and seed give identical rows.
- Commit messages follow the repo's style (a plain sentence, no `feat:` prefix) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `.venv/bin/python -m pytest`.

## Review Focus

1. A bill due on the 29th–31st should land on the last day of shorter months, not skip them (Task 3).
2. A story moment's `month` counts from the household's start, across a year boundary: month 3 of a November start is January next year (Task 2).
3. A lean month where spending outruns a budget's fill: topped up from to assign when it can be, and a clear error naming the budget and date when to assign has run out (Task 5).
4. Percent-of-income fills on uneven income take exactly the percent of each payment, to the cent (Task 5).
5. A fund stops exactly at its target and refills after money is drawn from it (Task 5).

---

### Task 1: The catalog

**Files:**
- Modify: `requirements.txt` (add `python-dateutil==2.9.0.post0`)
- Create: `tools/households/__init__.py`, `tools/households/errors.py`, `tools/households/catalog.py`
- Create: `fixtures/households/catalog.toml`
- Test: `tests/test_households_catalog.py`

**Interfaces:**
- Produces:
  - `class HouseholdError(ValueError)` in `errors.py`, built as `HouseholdError(item: str, message: str)`, with `str(e) == f"{item}: {message}"` and attributes `item` and `message`.
  - `read_toml(path: Path) -> dict` in `catalog.py`: `tomllib` with `parse_float=Decimal`.
  - `CATALOG_PATH`, `HOUSEHOLDS_DIR` (`<repo>/fixtures/households`).
  - `@dataclass(frozen=True) class Catalog` with `account_types`, `budgets`, `bills` and `incomes`, each a `dict[str, dict]` keyed by item key.
  - `load_catalog(path: Path = CATALOG_PATH) -> Catalog`.
  - `__init__.py` re-exports everything public from the package as later tasks add it.

Catalog contents (TOML sections `account_type`, `budget`, `bill`, `income`):

| account_type | class | on_budget |
| --- | --- | --- |
| checking, savings | asset | true |
| credit-card | liability | true |
| loan | liability | false |
| brokerage | asset | false |
| employer, client | income | false |
| merchant, service-provider, landlord, tax-agency | expense | false |

| budget | fill | rolls_over |
| --- | --- | --- |
| groceries, gas, household, fun-money, housing, phone, subscriptions, transit, loans, coworking, software | `{ basis = "amount", every = "FREQ=MONTHLY" }` | false |
| car, insurance, emergency-fund | same | true |
| taxes | `{ basis = "income_percent" }` | true |

| bill | type | budget | repeats |
| --- | --- | --- | --- |
| rent | rent | housing | `FREQ=MONTHLY` |
| phone | subscription | phone | `FREQ=MONTHLY` |
| streaming | subscription | subscriptions | `FREQ=MONTHLY` |
| software | subscription | software | `FREQ=MONTHLY` |
| car-loan | loan-payment | car | `FREQ=MONTHLY` |
| student-loan | loan-payment | loans | `FREQ=MONTHLY` |
| car-insurance | insurance | car | `FREQ=MONTHLY;INTERVAL=6` |
| renters-insurance | insurance | insurance | `FREQ=YEARLY` |
| health-insurance | insurance | insurance | `FREQ=MONTHLY` |
| estimated-taxes | tax | taxes | `FREQ=MONTHLY;INTERVAL=3` |

| income | type | repeats |
| --- | --- | --- |
| pay-biweekly | pay | `FREQ=WEEKLY;INTERVAL=2` |
| pay-semimonthly | pay | `FREQ=MONTHLY;BYMONTHDAY=1,15` |
| interest | interest | `FREQ=MONTHLY` |

Every item, account types included, has a `name`: its key with `-` replaced by a space (`fun-money` → `fun money`).

- [ ] **Step 1: Write the failing tests**

```python
def test_catalog_loads_every_section():
    c = load_catalog()
    assert c.account_types["credit-card"] == {"key": "credit-card", "name": "credit card", "class": "liability", "on_budget": True}
    assert c.budgets["taxes"]["fill"] == {"basis": "income_percent"}
    assert c.bills["car-insurance"]["repeats"] == "FREQ=MONTHLY;INTERVAL=6"
    assert c.incomes["pay-semimonthly"]["type"] == "pay"

def test_catalog_names_are_lower_case_and_bills_use_known_budgets():
    c = load_catalog()
    for section in (c.budgets, c.bills, c.incomes):
        for item in section.values():
            assert item["name"] == item["name"].lower()
    assert {b["budget"] for b in c.bills.values()} <= set(c.budgets)

def test_catalog_holds_no_amounts():
    c = load_catalog()
    for section in (c.account_types, c.budgets, c.bills, c.incomes):
        for item in section.values():
            assert "amount" not in item and "value" not in item.get("fill", {})

def test_duplicate_key_names_the_item(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[budget]]\nkey = "gas"\nname = "gas"\n[[budget]]\nkey = "gas"\nname = "gas"\n')
    with pytest.raises(HouseholdError, match="catalog budget gas: appears twice"):
        load_catalog(p)

def test_package_stands_alone():
    out = subprocess.run([sys.executable, "-c",
        "import sys, tools.households; print(any(m in sys.modules for m in ('services', 'app', 'routes', 'web')))"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
```

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_catalog.py -v`. Expected: FAIL (`ModuleNotFoundError: tools.households`).

- [ ] **Step 3: Add the dependency, the package, `HouseholdError`, `read_toml`, `load_catalog` and `catalog.toml`.** Run `.venv/bin/pip install -r requirements.txt`. `load_catalog` rejects unknown top-level sections with `HouseholdError("catalog", "unknown section <name>")`.

- [ ] **Step 4: Run them and confirm they pass.** Same command. Expected: 5 passed.

- [ ] **Step 5: Commit.** "Add the households catalog: account types, budgets, bills and income"

---

### Task 2: Household files and their checks

**Files:**
- Create: `tools/households/model.py`, `tools/households/load.py`, `tools/households/check.py`
- Create: `tests/households_support.py` (test helper), `tests/test_households_load.py`

**Interfaces:**
- Consumes: `read_toml`, `load_catalog`, `HouseholdError`.
- Produces, in `model.py`:
  - `TO_ASSIGN = "to-assign"` and `OWN_CLASSES = {"asset", "liability"}`.
  - `Rows = dict[str, list[dict]]`.
  - Frozen dataclasses:
    - `Account(key, name, type, cls, on_budget, parent, default_budget, url, opening_balance, opening_date, paid_on, paid_from)`. `opening_date` defaults to the day before `start`.
    - `Fill(basis, value, every)`.
    - `Budget(key, name, parent, priority, fill, target, target_date, rolls_over)`.
    - `Expected(key, name, type, from_account, to_account, budget, amount, starts, ends, repeats)`.
    - `Spending(budget, at, on, per_month)`.
    - `Line(amount, budget, expected)`.
    - `Moment(key, on, account, counterparty, amount, description, lines, overspend)`, where `on` is a `date` and `key` is `"moment-<n>"` in file order.
    - `StoryAssignment(on, from_budget, to_budget, amount)`.
    - `Household(key, name, shows, seed, start, months, accounts, budgets, expected, spending, moments, assignments)`. `accounts`, `budgets` and `expected` are dicts keyed by key, in file order.
  - `Household.end` (the last day of the last month), `Household.own(account_key) -> bool` (class in `OWN_CLASSES`), and `Household.on_budget(account_key) -> bool`.
  - `month_day(start: date, month: int, day: int) -> date`. Month 1 is the start's month.
- Produces, in `load.py`: `load_household(path: Path, catalog: Catalog | None = None) -> Household`. It resolves `use` and runs `check_household`.
- Produces, in `check.py`: `check_household(h: Household) -> None`.
- Produces, in `tests/households_support.py`: `MINIMAL: str`, a valid household TOML, and `household_from(tmp_path, text: str) -> Household`.

`MINIMAL`: key `mini`, seed 7, start 2026-01-01, months 2. Accounts:
- checking (opening 1000)
- card (credit-card, `paid_on = 25`, `paid_from = "checking"`)
- loan (loan, opening -5000)
- employer
- grocer (merchant, `default_budget = "groceries"`)
- landlord

Budgets: `use = "groceries"` fill 300; `use = "housing"` fill 500; `use = "loans"` fill 100. Expected:
- pay-semimonthly 1000.00 from employer to checking, starting 2026-01-01;
- rent 500.00 from checking to landlord, starting 2026-01-01;
- student-loan 100.00 from checking to loan, starting 2026-01-10.

Merging: an item with `use` starts from a copy of the catalog item, and the file's fields override it. `fill` merges key by key. `[[expected]]` looks `use` up in `bills`, then `incomes`. Its `key` defaults to the `use` key, and its `budget` is `None` for income. An account's `type` supplies `cls` and `on_budget`.

`check_household` raises `HouseholdError(f"{h.key}: <section> <key>", ...)` when:
- a reference doesn't resolve (account parent, default budget, budget parent, expected from/to/budget, spending budget/at/on, moment account/counterparty/line targets, story assignment budgets; `to-assign` is allowed as a line or assignment target);
- an expected transaction has no own account on either side, or income has a budget;
- a spending entry's budget isn't filled by `amount`, or its `on` account isn't on budget;
- a moment's line has both or neither of `budget`/`expected`;
- a moment's lines don't add up to its amount without its sign;
- a moment between two on-budget accounts has lines;
- a credit card lacks `paid_on` (1–28) or an own on-budget `paid_from`;
- a moment's month or day falls outside the household;
- any name, description or household name gives a finding from `scan_text`.

- [ ] **Step 1: Write the failing tests**

```python
def test_minimal_household_loads(tmp_path):
    h = household_from(tmp_path, MINIMAL)
    assert h.end == date(2026, 2, 28)
    assert h.expected["rent"].budget == "housing" and h.expected["rent"].repeats == "FREQ=MONTHLY"
    assert h.expected["pay-semimonthly"].budget is None
    assert h.accounts["card"].cls == "liability" and h.accounts["card"].on_budget is True
    assert h.accounts["checking"].opening_date == date(2025, 12, 31)
    assert h.budgets["groceries"].fill == Fill("amount", Decimal("300.00"), "FREQ=MONTHLY")

def test_month_counts_across_a_year_boundary():                    # Review Focus 2
    assert month_day(date(2025, 11, 1), 3, 14) == date(2026, 1, 14)

def test_unknown_account_names_the_item(tmp_path):
    bad = MINIMAL.replace('to = "landlord"', 'to = "nobody"')
    with pytest.raises(HouseholdError, match=r"mini: expected rent: unknown account 'nobody'"):
        household_from(tmp_path, bad)

def test_line_with_two_targets_fails(tmp_path):
    with pytest.raises(HouseholdError, match="exactly one"):
        household_from(tmp_path, MINIMAL + MOMENT.format(lines='{ budget = "groceries", expected = "rent", amount = 50 }'))

def test_lines_must_add_up(tmp_path):
    with pytest.raises(HouseholdError, match="add up"):
        household_from(tmp_path, MINIMAL + MOMENT.format(lines='{ budget = "groceries", amount = 40 }'))

def test_card_payment_moment_with_lines_fails(tmp_path):
    with pytest.raises(HouseholdError, match="on-budget accounts"):
        household_from(tmp_path, MINIMAL + TRANSFER.format(to="card", lines='[{ budget = "groceries", amount = 50 }]'))

def test_loan_payment_moment_needs_its_line(tmp_path):
    h = household_from(tmp_path, MINIMAL + TRANSFER.format(to="loan", lines='[{ budget = "loans", amount = 50 }]'))
    assert h.moments[0].lines[0].budget == "loans"

def test_private_looking_text_fails(tmp_path):
    with pytest.raises(HouseholdError, match="privacy"):
        household_from(tmp_path, MINIMAL.replace('name = "Example Employer"', f'name = "Employer {PRIVATE}"'))
```

`MOMENT` (in the helper) is a `[[moment]]` on the card at the grocer, month 1, day 5, amount -50, `lines = [{lines}]`. `TRANSFER` is a `[[moment]]` from checking to `{to}`, month 1, day 6, amount -50, `lines = {lines}`. A privacy finding raises `HouseholdError(item, f"privacy guard: {finding}")`. `PRIVATE` is a private address that `scan_text` flags, built in two pieces (`"10." + ...`) as `tests/test_no_real_data.py` does, so no tracked file holds one.

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_load.py -v`. Expected: FAIL (import errors).
- [ ] **Step 3: Implement `model.py`, `load.py`, `check.py` and the helper**, with the signatures above.
- [ ] **Step 4: Run them and confirm they pass.** Expected: 8 passed.
- [ ] **Step 5: Commit.** "Load and check household files against the catalog"

---

### Task 3: Schedules and shifting dates

**Files:**
- Create: `tools/households/schedules.py`
- Test: `tests/test_households_schedules.py`

**Interfaces:**
- Produces:
  - `occurrences(repeats: str, starts: date, until: date, ends: date | None = None) -> list[date]`: every date from `starts` through `min(until, ends)`, via `dateutil.rrule.rrulestr(repeats, dtstart=...)`.
  - `shift(rows: Rows, end: date, today: date) -> Rows`: a new `Rows` with every date column moved by `today - end`. Date columns:
    - `accounts.opening_date`
    - `budgets.target_date`
    - `expected_transactions.starts_at` and `ends_at`
    - `transactions.posted_on`
    - `assignments.assigned_on`

  `None` stays `None`.

Clamping (Review Focus 1): when a `MONTHLY` or `YEARLY` rule has no `BYMONTHDAY`, `BYDAY` or `BYSETPOS`, and `starts.day > 28`, add `bymonthday=range(28, starts.day + 1)` and `bysetpos=-1` (plus `bymonth=starts.month` for `YEARLY`). The date is then the last of those days the month has.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_other_friday():
    days = occurrences("FREQ=WEEKLY;INTERVAL=2", date(2025, 11, 7), date(2026, 10, 31))
    assert len(days) == 26 and all(d.weekday() == 4 for d in days)

def test_first_and_fifteenth():
    days = occurrences("FREQ=MONTHLY;BYMONTHDAY=1,15", date(2026, 5, 1), date(2026, 10, 31))
    assert len(days) == 12 and days[:2] == [date(2026, 5, 1), date(2026, 5, 15)]

def test_the_31st_lands_on_each_months_last_day():                    # Review Focus 1
    days = occurrences("FREQ=MONTHLY", date(2026, 1, 31), date(2026, 12, 31))
    assert len(days) == 12 and date(2026, 2, 28) in days and date(2026, 4, 30) in days

def test_end_date_is_respected():
    assert occurrences("FREQ=MONTHLY", date(2026, 1, 5), date(2026, 12, 31), ends=date(2026, 3, 5))[-1] == date(2026, 3, 5)

def test_shift_moves_every_date_and_nothing_else():
    rows = {"transactions": [{"id": 1, "posted_on": date(2026, 2, 28), "amount": Decimal("-5.00")}],
            "assignments": [{"id": 1, "assigned_on": date(2026, 2, 1), "amount": Decimal("5.00")}],
            "budgets": [{"id": 1, "target_date": None}]}
    out = shift(rows, end=date(2026, 2, 28), today=date(2026, 11, 14))
    assert out["transactions"][0]["posted_on"] == date(2026, 11, 14)
    assert out["assignments"][0]["assigned_on"] == date(2026, 10, 18)
    assert out["transactions"][0]["amount"] == Decimal("-5.00") and out["budgets"][0]["target_date"] is None
    assert rows["transactions"][0]["posted_on"] == date(2026, 2, 28)
```

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_schedules.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement `occurrences` and `shift`.**
- [ ] **Step 4: Run them and confirm they pass.** Expected: 5 passed.
- [ ] **Step 5: Commit.** "Expand repeat rules and shift household dates"

---

### Task 4: Generating rows: bills, income, cards and story moments

**Files:**
- Create: `tools/households/generate.py`
- Modify: `tools/households/check.py` (add `check_rows`)
- Test: `tests/test_households_generate.py`

**Interfaces:**
- Consumes: `Household`, `occurrences`, `month_day`, `HouseholdError`.
- Produces:
  - `generate(h: Household) -> Rows`, with these tables and columns (ids are 1-based integers per table, assigned in the order given):

| table | columns | order |
| --- | --- | --- |
| `users` | `id, slug, display_name` | one row |
| `accounts` | `id, key, parent_id, name, class, type, on_budget, default_budget_id, url, opening_balance, opening_date` | file order |
| `budgets` | `id, key, parent_id, name, priority, fill_basis, fill_value, fill_rrule, target_amount, target_date, rolls_over, backing_account_id` | `to-assign` first (name `to assign`), then file order |
| `expected_transactions` | `id, key, name, type, from_account_id, to_account_id, budget_id, amount, starts_at, ends_at, rrule` | file order |
| `transactions` | `id, account_id, counterparty_account_id, posted_on, amount, description` | sorted by `(posted_on, rank, source key, sequence)`, rank: income 0, everything else 1 |
| `transaction_lines` | `id, transaction_id, amount, budget_id, expected_transaction_id` | by transaction, then line order |
| `assignments` | `id, from_budget_id, to_budget_id, amount, assigned_on, note` | date order (story assignments only in this task) |

  - `check_rows(rows: Rows, h: Household) -> None`. It raises `HouseholdError` when a transaction's lines don't add up, a line has both or neither target, or a transfer between two on-budget accounts has lines.

Rules:
- **Recording.** If the `from` account is own, the transaction is recorded on it, with the `to` account as counterparty and a negative amount. Otherwise it's recorded on `to`, with `from` as counterparty and a positive amount.
- **Bills** (expected with a budget) post `Random(f"{seed}:expected:{key}").randint(0, 2)` days late, one draw per occurrence in order. Anything past `h.end` is dropped. Each gets one line to its expected transaction.
- **Income** posts on schedule, with one line to its expected transaction.
- **Moments and story assignments** are added as written.
- **Card payments** run last. On each card's `paid_on` day, for each month, the card is paid for the card's transactions since the previous payment (sum of their amounts, negated). The payment is recorded on `paid_from`, with the card as counterparty and no lines. A total ≤ 0 makes no payment.
- `generate` ends by calling `check_rows`.

- [ ] **Step 1: Write the failing tests** (all on `household_from(tmp_path, MINIMAL)`)

```python
def test_bills_post_up_to_two_days_late_with_a_line_to_the_expected(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    rent_id = key_id(rows, "expected_transactions", "rent")
    rent = [t for t in rows["transactions"] if lines_of(rows, t)[0]["expected_transaction_id"] == rent_id]
    assert [t["amount"] for t in rent] == [Decimal("-500.00")] * 2
    assert all(0 <= t["posted_on"].day - 1 <= 2 for t in rent)

def test_income_is_recorded_on_the_own_account_as_money_in(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    pay = [t for t in rows["transactions"] if t["amount"] == Decimal("1000.00")]
    assert len(pay) == 4 and {t["account_id"] for t in pay} == {key_id(rows, "accounts", "checking")}

def test_loan_payment_is_a_line_not_a_bare_transfer(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    loan = key_id(rows, "accounts", "loan")
    payments = [t for t in rows["transactions"] if t["counterparty_account_id"] == loan]
    assert payments and all(len(lines_of(rows, t)) == 1 for t in payments)

def test_card_is_paid_for_what_was_charged(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + MOMENT.format(lines='{ budget = "groceries", amount = 50 }')))
    card = key_id(rows, "accounts", "card")
    pay = [t for t in rows["transactions"] if t["counterparty_account_id"] == card]
    assert [(t["posted_on"], t["amount"]) for t in pay] == [(date(2026, 1, 25), Decimal("-50.00"))]
    assert lines_of(rows, pay[0]) == []

def test_to_assign_is_the_first_budget(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    assert rows["budgets"][0]["key"] == "to-assign" and rows["budgets"][0]["name"] == "to assign"

def test_same_household_same_rows(tmp_path):
    h = household_from(tmp_path, MINIMAL)
    assert generate(h) == generate(h)
```

`key_id(rows, table, key)` and `lines_of(rows, transaction)` go in `tests/households_support.py`.

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_generate.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement `generate` (this task's parts) and `check_rows`.**
- [ ] **Step 4: Run them and confirm they pass.** Expected: 6 passed.
- [ ] **Step 5: Commit.** "Generate household rows for bills, income, cards and story moments"

---

### Task 5: Everyday spending and assignments

**Files:**
- Create: `tools/households/assign.py`
- Modify: `tools/households/generate.py` (add purchases, then call the engine before ids are final)
- Test: `tests/test_households_assign.py`

**Interfaces:**
- Consumes: Task 4's rows (with transactions and lines), `Household`, `occurrences`.
- Produces: `run_assignments(h: Household, rows: Rows) -> list[dict]`. It returns assignment rows (without ids, with `note` set to `"opening"`, `"month start"`, `"income"`, `"top up"`, `"leftover"` or `"story"`). It raises `HouseholdError(f"{h.key}: budget {key}", f"goes below zero on {date}")` when to assign can't cover a shortfall and the moment isn't `overspend`.

**Purchases**, per spending entry and month, from `Random(f"{seed}:spending:{budget}:{YYYY-MM}")`:
- `total = (fill − that month's moment lines to the budget) × uniform(0.85, 1.05)`, quantized. Skip if ≤ 0.
- Split into `per_month` parts with weights `uniform(0.5, 1.5)`, each quantized; the last part takes the rounding.
- Days are `randint(1, last day)`, sorted. Merchants are `choice(at)`.
- Each purchase is recorded on `on`, with the merchant as counterparty, description = the merchant's name in lower case, and one line to the budget.

Purchases are generated before card payments, so cards pay for them.

**The engine** replays the household one day at a time, keeping a balance per budget (to assign included). A spending budget means no priority and filled by `amount`. Each day, in this order:
1. **On `start`:** the opening balances of on-budget accounts go to to assign (`"opening"`).
2. **On the 1st:** each spending budget that doesn't roll over returns a positive balance to to assign (`"leftover"`, not on `start`). Then each spending budget in file order gets its monthly fill from to assign, as far as the money goes (`"month start"`).
3. **Story assignments** for the day (`"story"`).
4. **The day's transactions, in id order.** A line's budget is its own, or its expected transaction's. A line to income or to `to-assign` adds to to assign. For a negative transaction, each line subtracts from its budget. When a spending budget lacks the money, it's first topped up from to assign by the shortfall (`"top up"`). For a positive transaction with a budget line (a refund), the line adds to the budget.
5. **After each income transaction** (any line adding to to assign):
   1. spending budgets not yet given this month's full fill are topped up;
   2. budgets without a priority that fill by `income_percent` get `amount × value / 100`;
   3. budgets with a priority, in ascending priority then file order, get either their `amount` fill (once per period, where the period starts at the latest `fill.every` occurrence from `start`, and any part not yet given this period counts) or their percent of this income.

   Every fill is capped by the target's headroom and by what's in to assign (`"income"`).

- [ ] **Step 1: Write the failing tests**

```python
def test_purchases_track_the_budget(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + SPENDING))   # groceries at grocer on card, 4 per month
    jan = month_spend(rows, "groceries", 2026, 1)
    assert Decimal("255.00") <= jan <= Decimal("315.00")

def test_opening_cash_fills_spending_budgets_on_day_one(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    first = [a for a in rows["assignments"] if a["assigned_on"] == date(2026, 1, 1) and a["note"] == "month start"]
    assert {a["amount"] for a in first} == {Decimal("300.00"), Decimal("500.00"), Decimal("100.00")}

def test_leftover_returns_on_the_first(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL))
    assert any(a["note"] == "leftover" and a["assigned_on"] == date(2026, 2, 1) for a in rows["assignments"])

def test_shortfall_is_topped_up_from_to_assign(tmp_path):                     # Review Focus 3
    rows = generate(household_from(tmp_path, MINIMAL + BIG_GROCERY))            # -350 at the grocer, month 1 day 5, line groceries 350
    assert any(a["note"] == "top up" and a["amount"] == Decimal("50.00") for a in rows["assignments"])

def test_overspending_past_to_assign_names_the_budget(tmp_path):              # Review Focus 3
    broke = MINIMAL.replace("opening_balance = 1000", "opening_balance = 0") + BIG_PURCHASE   # -5000 on groceries, month 1 day 2
    with pytest.raises(HouseholdError, match=r"mini: budget groceries: goes below zero on 2026-01-02"):
        generate(household_from(tmp_path, broke))

def test_percent_fill_takes_exactly_its_share(tmp_path):                       # Review Focus 4
    rows = generate(household_from(tmp_path, MINIMAL + TAXES_25 + PAYMENTS))    # client moments of 1000.00 and 333.33
    taxes = key_id(rows, "budgets", "taxes")
    got = sorted(a["amount"] for a in rows["assignments"] if a["to_budget_id"] == taxes and a["note"] == "income")
    assert Decimal("83.33") in got and Decimal("250.00") in got

def test_fund_stops_at_its_target_and_refills(tmp_path):                        # Review Focus 5
    rows = generate(household_from(tmp_path, LONG_MINIMAL + KIDS_FUND + DRAW))  # 50/month to 500, 12 months, draw 100 in month 11
    balances = fund_balances(rows, "kids-fund")
    assert max(balances) == Decimal("500.00") and balances[-1] == Decimal("450.00")

def test_priority_one_fills_first(tmp_path):
    rows = generate(household_from(tmp_path, MINIMAL + TWO_FUNDS))                # p1 and p2 each 900/month, income covers one
    assert fund_total(rows, "first") > fund_total(rows, "second")

def test_adding_an_item_leaves_other_items_rows_alone(tmp_path):
    a = generate(household_from(tmp_path, MINIMAL + SPENDING))
    b = generate(household_from(tmp_path, MINIMAL + SPENDING + EXTRA_BILL))
    assert transactions_by_source(a) == {k: v for k, v in transactions_by_source(b).items() if k != "extra"}
```

The fragments (`SPENDING`, `BIG_GROCERY`, `BIG_PURCHASE`, `TAXES_25`, `PAYMENTS`, `LONG_MINIMAL`, `KIDS_FUND`, `DRAW`, `TWO_FUNDS`, `EXTRA_BILL`) and the readers (`month_spend`, `fund_balances`, `fund_total`, `transactions_by_source`) go in `tests/households_support.py`. Their values are in the comments above. `transactions_by_source` maps each expected or spending key to its list of `(posted_on, amount)`, excluding card payments.

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_assign.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement purchases in `generate.py` and `run_assignments` in `assign.py`.**
- [ ] **Step 4: Run the new and earlier household tests and confirm they pass.** Run `.venv/bin/python -m pytest tests/test_households_*.py -v`. Expected: all passed.
- [ ] **Step 5: Commit.** "Generate everyday spending and assign money the way Metis would propose"

---

### Task 6: The three households

**Files:**
- Create: `fixtures/households/example-family.toml`, `sam-sample.toml`, `dana-demo.toml`
- Modify: `tools/households/load.py` (add `load_all() -> list[Household]` for every `*.toml` in `HOUSEHOLDS_DIR` except the catalog, sorted by key, and `find(key: str) -> Household`)
- Test: `tests/test_households.py`

Amounts are 2-decimal; dates are ISO. Lines in moments use positive amounts.

**The Example family:** `seed = 1`, `start = 2025-11-01`, `months = 12`, shows as in the spec.

| account key | name | type | extra |
| --- | --- | --- | --- |
| checking | Example Bank checking | checking | opening 4000 |
| savings | Example Bank savings | savings | opening 2000 |
| card | Example Card | credit-card | paid_on 25, paid_from checking |
| auto-loan | Example Auto loan | loan | opening -12000 |
| employer | Example Employer | employer | |
| grocer, fuel, warehouse, auto-shop, arcade | Acme Grocery, Acme Fuel, Acme Warehouse, Acme Auto Repair, Acme Arcade | merchant | default budgets groceries, gas, household, car, fun-money |
| landlord | Example Rentals | landlord | |
| phone-co, streaming, insurer | Example Phone Co., Example Streaming, Example Insurance | service-provider | |

Budgets (fills): groceries 600, gas 200, household 100, fun-money 150, housing 1800, phone 65, subscriptions 20, car 450. Plus:
- `kids-fund` "kids' fund": amount 50 monthly, priority 2, target 500, rolls over;
- `college` "college": income_percent 5, priority 1, target 20000, target_date 2040-09-01, rolls over;
- `use = "emergency-fund"`: amount 300, priority 3, target 10000.

Expected:

| use | from → to | amount | starts |
| --- | --- | --- | --- |
| pay-biweekly | employer → checking | 2400 | 2025-11-07 |
| rent | checking → landlord | 1800 | 2025-11-01 |
| car-loan | checking → auto-loan | 350 | 2025-11-15 |
| phone | card → phone-co | 65 | 2025-11-12 |
| streaming | card → streaming | 20 | 2025-11-03 |
| car-insurance | checking → insurer | 600 | 2025-12-10 |

Spending: groceries at grocer, 6 a month; gas at fuel, 4; household at warehouse, 2; fun-money at arcade, 3. All on card.

Moments:
- month 5, day 14: card at warehouse, -180, "acme warehouse #12", lines groceries 120 and household 60;
- month 6, day 9: card at grocer, +45, "acme grocery refund", line groceries 45;
- month 8, day 20: checking at auto-shop, -900, "acme auto repair", line car 900.

Assignment: month 8, day 19, emergency-fund → car, 900.

**Sam Sample:** `seed = 2`, `start = 2026-05-01`, `months = 6`.

Accounts:
- checking "Example Credit Union checking" (opening 1500);
- savings "Example Credit Union savings" (opening 500);
- card "Example Card" (paid_on 20, paid_from checking);
- student-loan "Example Student Loan", type loan (opening -18000);
- employer "Example Employer";
- grocer "Acme Grocery" (groceries) and arcade "Acme Cinema" (fun-money), merchants;
- transit "Example Transit", merchant (transit);
- landlord "Example Rent Co.";
- phone-co, streaming, insurer, vet and cloud: "Example Phone Co.", "Example Streaming", "Example Insurance", "Example Vet" and "Example Cloud Storage", service providers.

Budgets: groceries 300, transit 90, fun-money 100, housing 1100, phone 50, subscriptions 15, loans 250, insurance 15. Plus `use = "emergency-fund"`: income_percent 10, priority 1, target 6000.

Expected:
- pay-semimonthly 1600, employer → checking, 2026-05-01;
- rent 1100, checking → landlord, 2026-05-01;
- student-loan 250, checking → student-loan, 2026-05-10;
- phone 50, card → phone-co, 2026-05-08;
- streaming 15, card → streaming, 2026-05-05;
- renters-insurance 180, checking → insurer, 2026-06-01.

Spending: groceries at grocer, 5 a month; transit at transit, 4; fun-money at arcade, 2. All on card.

Moments:
- month 2, day 3: checking from landlord, +1000, "example rent co. deposit returned", line to-assign 1000;
- month 3, day 18: card at vet, -400, "example vet", line emergency-fund 400;
- month 4, day 2: card at cloud, -99, "example cloud storage yearly plan", line subscriptions 99.

**Dana Demo:** `seed = 3`, `start = 2025-11-01`, `months = 12`.

Accounts:
- checking "Example Bank checking" (opening 9000);
- savings "Example Bank savings" (opening 3000);
- card "Example Card" (paid_on 20, paid_from checking);
- brokerage "Example Brokerage", type brokerage (opening 20000);
- client-a "Acme Design Co." and client-b "Example Studio", clients;
- coworking "Acme Coworking", service-provider (coworking);
- tax-agency "Example Tax Agency";
- landlord "Example Rentals";
- insurer "Example Health" and software-co "Example Software", service providers;
- grocer "Acme Grocery", merchant (groceries).

Budgets: groceries 400, coworking 250, software 60, housing 1400, insurance 450, `use = "taxes"` value 25. Plus:
- `buffer` "buffer": amount 500 monthly, priority 1, target 8000, rolls over;
- `travel` "travel": amount 300 monthly, priority 3, target 3000, target_date 2026-07-01, rolls over.

Expected:
- rent 1400, checking → landlord, 2025-11-01;
- health-insurance 450, checking → insurer, 2025-11-05;
- software 60, card → software-co, 2025-11-11;
- estimated-taxes 2400, checking → tax-agency, 2026-01-15.

Spending: groceries at grocer, 5 a month; coworking at coworking, 1 a month. Both on card.

Moments: client payments into checking, each with one line to to-assign. The description is the client's name in lower case plus " invoice" unless given.

| month/day | client | amount | description |
| --- | --- | --- | --- |
| 1/10 | client-a | 5000 | |
| 2/10 | client-a | 6500 | |
| 3/12 | client-b | 2500 | |
| 4/10 | client-a | 7000 | |
| 5/22 | client-b | 12000 | example studio project |
| 6/10 | client-a | 3000 | |
| 8/04 | client-a | 8000 | acme design co. two invoices, one late |
| 9/10 | client-a | 6000 | |
| 10/10 | client-b | 5500 | |
| 11/12 | client-a | 2500 | |
| 12/10 | client-a | 7500 | |

Add a TOML comment at the top of Dana's file: client income varies, so it's story moments until the map can express expected income that varies.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture(scope="module")
def rows():
    return {h.key: (h, generate(h)) for h in load_all()}

def test_all_three_load_and_generate(rows):
    assert sorted(rows) == ["dana-demo", "example-family", "sam-sample"]

def test_family_split_refund_and_covered_repair(rows):
    h, r = rows["example-family"]
    assert any(len(lines_of(r, t)) == 2 for t in r["transactions"])
    assert any(t["amount"] == Decimal("45.00") for t in r["transactions"])
    assert any(a["note"] == "story" and a["amount"] == Decimal("900.00") for a in r["assignments"])
    assert max(fund_balances(r, "kids-fund")) == Decimal("500.00")

def test_sam_starts_out(rows):
    h, r = rows["sam-sample"]
    assert h.end == date(2026, 10, 31)
    assert lines_to(r, "emergency-fund", negative=True) == [Decimal("400.00")]
    assert lines_to(r, "to-assign") == [Decimal("1000.00")]

def test_dana_income_is_uneven_and_taxes_take_a_quarter(rows):
    h, r = rows["dana-demo"]
    income = monthly_income(r)
    assert income.get("2026-05", Decimal("0")) == 0 and income["2026-06"] == Decimal("8000.00")
    taxes = key_id(r, "budgets", "taxes")
    assert sum(a["amount"] for a in r["assignments"] if a["to_budget_id"] == taxes) == Decimal("16375.00")

def test_households_are_repeatable(rows):
    assert all(generate(h) == r for h, r in rows.values())
```

`lines_to` and `monthly_income` go in the helper. `monthly_income` sums positive transactions with lines to to assign, by `YYYY-MM`.

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households.py -v`. Expected: FAIL (no files, `load_all` missing).
- [ ] **Step 3: Write the three files and `load_all`/`find`.** If a check fails, fix the file to the values above. The check error names what's wrong.
- [ ] **Step 4: Run all tests and confirm they pass.** Run `.venv/bin/python -m pytest -q`. Expected: all passed, including `tests/test_no_real_data.py` over the new files (stage them with `git add` first, since the guard reads tracked files).
- [ ] **Step 5: Commit.** "Add the Example family, Sam Sample and Dana Demo households"

---

### Task 7: The summary command

**Files:**
- Create: `tools/households/summary.py`, `tools/households/__main__.py`, `fixtures/households/README.md`
- Test: `tests/test_households_summary.py`

**Interfaces:**
- Consumes: `find`, `generate`, `shift`.
- Produces: `summary(h: Household, rows: Rows) -> str`, and the CLI `python -m tools.households summary <household-key> [--today YYYY-MM-DD]`. With `--today`, rows are shifted so `h.end` lands on that day before summarizing.

Output (plain text, amounts with thousands separators and 2 decimals):
1. a title line: `<household name>  <start> to <end>`;
2. one line per month: `YYYY-MM  income <x>  spent <y>`, then an indented line of spending by budget, largest first;
3. `balances` with each budget's balance at the end, to assign included;
4. `funds` with each budget that has a target: `<name>  <balance> of <target>  reached <date>` when the target was met, else `projected <YYYY-MM>`. The projection takes the average monthly amount assigned to it over the last 3 months, or `not on track` when that average is 0.

`README.md` covers:
- what the households are for (the three jobs);
- the data rule (invented only);
- how to add a household (a new file, items by catalog key);
- the summary command;
- that the files target Metis 1.0, not the 0.x database.

- [ ] **Step 1: Write the failing tests**

```python
def test_family_summary_tells_its_year():
    h = find("example-family")
    text = summary(h, generate(h))
    assert text.splitlines()[0].startswith("the Example family  2025-11-01 to 2026-10-31")
    assert sum(1 for line in text.splitlines() if line[:7].count("-") == 1 and line[:4].isdigit()) == 12
    assert "kids' fund  500.00 of 500.00  reached" in text

def test_cli_runs_and_shifts():
    out = subprocess.run([sys.executable, "-m", "tools.households", "summary", "sam-sample", "--today", "2026-11-14"],
                         capture_output=True, text=True, check=True).stdout
    assert "2026-05-15 to 2026-11-14" in out.splitlines()[0]

def test_unknown_household_says_so():
    out = subprocess.run([sys.executable, "-m", "tools.households", "summary", "nobody"], capture_output=True, text=True)
    assert out.returncode == 2 and "unknown household 'nobody'" in out.stderr
```

With `--today`, the title shows the shifted start and end (`start + (today − end)` to `today`).

- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/bin/python -m pytest tests/test_households_summary.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `summary`, the CLI and the README.**
- [ ] **Step 4: Run the full suite and confirm it passes.** Run `.venv/bin/python -m pytest -q`. Expected: all passed.
- [ ] **Step 5: Commit.** "Add the household summary command and the households README"
