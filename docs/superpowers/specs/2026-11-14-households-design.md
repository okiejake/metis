# Households: test data, wiki examples and the suggestions catalog

Status: approach approved in conversation on 2026-11-14. This document awaits
the owner's approval.

All names, amounts and dates in this document are invented.

## Why

Metis 1.0 needs one set of made-up data that serves three jobs:

1. **Test data.** It fills every table and every screen, so tests and the visual
   check work through realistic cases rather than scattered rows.
2. **Wiki examples.** Each feature page in the project wiki tells its story with
   one household, and its numbers come from that household's data, so the
   pages agree with each other and with the app.
3. **Suggestions.** The generic parts (common budgets, typical bills and how
   often they come, account types) become the catalog Metis offers a new user
   at setup.

The data is written against the Metis 1.0 data map (the second draft, where
accounts are where money is and budgets are what it's for), not against the
0.x database.

## Goals

- Three households, each there to show a different part of Metis.
- Repeatable: the same files and seed give the same rows on every run.
- One catalog, shared by the households and by suggestions.
- A new household is a new file. Changing a budget is a one-line edit.
- Everything is readable as text and reviewable in a pull request.

## Non-goals

- Loading into the 0.x database. `tools/demo_data.py` keeps that job for 0.x.
- Writing the wiki pages themselves. That is separate work, which uses this.
- Real-world prices. The catalog suggests what to track, not what it costs.
- Rules. The rules table is designed next. Household files write the lines a
  rule would write directly.

## Anti-goals

- Anything that resembles a real person, merchant, institution or amount. The
  privacy guard must pass on every file.
- A catalog item copied into a household file. Households refer to it by key.
- Randomness that changes between runs, or that reshuffles every row when one
  item is added.

## The model the files use

As drawn in the data map on 2026-11-14. Where the map changes, the files and
the generator follow.

| Object | What the files give it |
| --- | --- |
| household | one Metis user |
| account | key, name, type (from the catalog, which sets `class` and `on_budget`), optional parent, optional default budget, optional url, optional opening balance and date |
| budget | key, name, optional parent, priority, fill (basis `amount` or `income_percent`, value, schedule), optional target amount and date, rolls over or not |
| to assign | one budget row per household, made by the generator, never written in a file |
| assignment | made by the generator when income lands, following priority |
| expected transaction | key, name, type, from account, to account, budget (none for income), amount, start, optional end, schedule |
| transaction | account, counterparty, posted date, signed amount (negative is money out of the account), description |
| transaction line | a positive amount, and exactly one of a budget or an expected transaction. A transaction's lines add up to its amount without its sign |

Three map choices are still proposals, and the files assume them: a line points
at exactly one of a budget or an expected transaction; to assign is a real
budget row; budgets have `priority` and a fill basis instead of a fixed kind.

One field is new to the map: an account's **opening balance** and its date. A
loan needs its starting balance, and checking needs its starting cash. The map
gains the two columns, and the households use them.

## Files

```
fixtures/households/
  catalog.toml            generic items: account types, budgets, bills
  example-family.toml
  sam-sample.toml
  dana-demo.toml
tools/households.py       reads and checks the files, generates the rows
tests/test_households.py
```

They land on `main` through a pull request. They depend on no 0.x code, so the
v1 branch inherits them when it starts.

## The catalog

Each item has a stable key, a display name in lower case, and defaults. It
holds no amounts.

```toml
[[account_type]]
key = "checking"
class = "asset"
on_budget = true

[[account_type]]
key = "merchant"
class = "expense"
on_budget = false

[[budget]]
key = "groceries"
name = "groceries"
fill = { basis = "amount", every = "FREQ=MONTHLY" }

[[bill]]
key = "phone"
name = "phone"
type = "subscription"
budget = "phone"
repeats = "FREQ=MONTHLY"

[[bill]]
key = "car-insurance"
name = "car insurance"
type = "insurance"
budget = "car"
repeats = "FREQ=MONTHLY;INTERVAL=6"
```

Sections: `account_type`, `budget`, `bill` (anything scheduled: subscriptions,
insurance, loan payments, rent, taxes) and `income` (pay, interest). Schedules
use iCalendar repeat rules (RFC 5545), as expected transactions do.

## A household file

```toml
[household]
key = "example-family"
name = "the Example family"
shows = ["funds and goals by priority", "splits", "a loan with a balance", "a bill every six months"]
seed = 1
start = 2025-11-01
months = 12

[[account]]
key = "checking"
name = "Example Bank checking"
type = "checking"

[[account]]
key = "grocer"
name = "Acme Grocery"
type = "merchant"
default_budget = "groceries"

[[budget]]
use = "groceries"                     # catalog item; only what differs is given
fill = { value = 600 }

[[budget]]
key = "kids-fund"
name = "kids' fund"
priority = 2
fill = { basis = "amount", value = 50, every = "FREQ=MONTHLY" }
target = 500
rolls_over = true

[[expected]]
use = "phone"                         # catalog bill
from = "card"
to = "phone-co"
amount = 65
starts = 2025-11-12

[[spending]]                          # everyday purchases that draw on a budget
budget = "groceries"
at = ["grocer"]
on = "card"
per_month = 6

[[moment]]                            # a story moment, added exactly as written
month = 5
day = 14
account = "card"
counterparty = "warehouse"
amount = -180
description = "acme warehouse #12"
lines = [{ budget = "groceries", amount = 120 }, { budget = "household", amount = 60 }]
```

Items with `use` take the catalog item's fields, then the file's. Items with
`key` stand alone. Every reference is by key.

## Generating the rows

`tools/households.py` returns plain rows per table, keyed like the map. A loader
into the Metis 1.0 database comes with that database.

- **Expected transactions.** Each schedule runs from its start through the
  household's months. A bill's transaction posts on its due date, or up to 2
  days later, with its exact amount and one line pointing at the expected
  transaction.
- **Income.** Pay posts on schedule, with one line pointing at the expected
  income, so it lands in to assign.
- **Everyday spending.** Each `spending` entry makes `per_month` purchases at its
  merchants, totalling between 85% and 105% of the budget's fill for that month,
  each with one line to the budget.
- **Cards.** Each card is paid in full from checking on the day its file entry
  gives (`paid_on = 25`), for what was charged since the last payment. It's a
  transfer between the household's own accounts, with no lines.
- **Loans.** A loan starts at its opening balance and goes down by each payment.
  It has no interest until the map gives loans an interest rate.
- **Assignments.** When income lands, the generator assigns it the way Metis
  would propose and the household would confirm. Spending budgets get their
  monthly fill first. Funds and goals then fill in priority order, by amount or
  by percent of that income. Anything left stays in to assign.
- **Story moments** are added exactly as written.
- **Seeds.** Every item draws from its own random stream, seeded by the
  household's seed and the item's key, so adding an item never changes another
  item's rows.
- **Dates.** Files use fixed dates, so tests are stable. `shift(today)` moves a
  household's dates so its last month ends at `today`, for screens and
  screenshots.

`python -m tools.households summary <household>` prints each month's income,
spending by budget, budget balances, and each fund's projected completion
date. Wiki pages take their numbers from it.

## Checks

Loading a household fails, naming the item, when:

- a key it refers to doesn't exist, in the file or in the catalog;
- a transaction's lines don't add up to its amount;
- a line points at both a budget and an expected transaction, or at neither;
- a transfer between the household's own accounts has lines;
- a budget goes below zero, unless a story moment is marked `overspend = true`;
- a name or description doesn't pass the privacy guard's checks.

## The households

### The Example family: two adults, two kids

Shows: funds and goals competing by priority, splits, a car loan with a
balance, a bill every six months. 12 months.

- **Accounts:** Example Bank checking and savings, Example Card, Example Auto
  loan, Example Employer, Acme Grocery, Acme Fuel, Acme Warehouse, Example
  Phone Co., Example Streaming.
- **Budgets:** groceries 600 a month, gas 200 a month, household 100 a month,
  fun money 150 a month, car. Kids' fund: holds 500, 50 a month, priority 2.
  College: 20,000 by 2040, 5% of income, priority 1. Emergency fund: holds
  10,000, priority 3.
- **Expected:** pay every other Friday, rent on the 1st, the car loan monthly
  with a balance, phone, streaming, car insurance every 6 months.
- **Moments:** the warehouse run split across groceries and household, a car
  repair paid from the emergency fund through an assignment, and a refund.

### Sam Sample: single, renting, first full-time job

Shows: starting out, suggestions at setup, a first budget, building an
emergency fund, a subscription Sam forgot about. 6 months.

- **Accounts:** Example Credit Union checking and savings, Example Card, Example
  Student Loan, Example Employer, Acme Grocery, Example Transit, Example Rent
  Co.
- **Budgets:** groceries 300 a month, transit 90 a month, fun money 100 a month.
  Emergency fund: holds 6,000 (about three months of expenses), 10% of income,
  priority 1.
- **Expected:** pay on the 1st and 15th, rent on the 1st, the student loan with a
  balance, phone, streaming, renter's insurance yearly.
- **Moments:** the security deposit returned, a vet bill, and a yearly
  subscription that renews without Sam expecting it.

### Dana Demo: freelancer, income arrives unevenly

Shows: irregular income, percent-of-income funds, quarterly estimated taxes,
lean and big months, business spending kept apart from personal. 12 months.

- **Accounts:** Example Bank checking and savings, Example Card, Example
  Brokerage (off budget), clients Acme Design Co. and Example Studio, Acme
  Coworking, Example Tax Agency.
- **Budgets:** groceries 400 a month, coworking 250 a month, software 60 a month,
  taxes 25% of income. Buffer: holds 8,000 (about two lean months), priority 1.
  Travel: 3,000 by 2026-07-01, priority 3.
- **Expected:** estimated taxes quarterly, rent, health insurance, software
  subscriptions.
- **Moments:** each client payment, including a late one and one large one-off
  project.

**Dana tests the design.** An expected transaction has one fixed amount, and
client income that varies doesn't fit. Until the map says how expected income
that varies works, Dana's client payments are story moments, and her file notes
the gap.

## How the wiki uses them

- Each household gets a wiki page that introduces it.
- Each feature page tells its story with one household, using numbers from the
  summary command.
- Screenshots come from Metis 1.0 screens loaded with a household. Until those
  screens exist, mockups use the same numbers.

## How suggestions use them

At setup, Metis offers catalog items: account types, common budgets, common
bills and income. The catalog holds no prices. A suggestion fills in the name,
type, budget and schedule. The user gives the amount.

## Approaches considered

- **Household files plus a generator (chosen).** Short, readable files.
  Repeatable rows. One catalog for test data and suggestions.
- **Every row written out by hand.** Exact, but thousands of lines. One budget
  change touches dozens of rows, and the catalog becomes a second copy.
- **Recorded from real use.** Realistic, but it can't start until Metis 1.0 has
  screens, and the result can't be reviewed as text.

## Calls made by the builder (the owner can overturn any)

- TOML files, read by Python's standard library.
- The catalog holds no amounts.
- Each item has its own random stream, so adding one never reshuffles the rest.
- Cards are paid in full each month.
- In the test data, the household always confirms what Metis proposes.
- Spending budgets fill before funds and goals, and funds and goals then fill
  in priority order.
- A percent of income means a percent of what actually arrives (net), since
  that is what Metis sees. Gross pay would need pay breakdowns Metis doesn't
  import.
- Dana's client income is story moments until expected income that varies is
  designed.
- The generator returns plain rows, not database writes, until the Metis 1.0
  database exists.
- Fixed start dates: the family and Dana from 2025-11-01 for 12 months, Sam from
  2026-05-01 for 6 months.
- The files land on `main`, and the v1 branch inherits them.
- Accounts gain an opening balance and date in the map.
- A card's payment day is given in the household file.
- Loans have no interest until the map gives them a rate.
- Targets are fixed amounts. "Three months of expenses" is only how a target was
  chosen.

## Open

- From the map: a line pointing at exactly one target, to assign as a row, and
  budget priority with a fill basis. All are assumed here.
- How expected income that varies is modelled (Dana).
- Gross or net income for percent fills (net assumed).

## Left to the builder

- The exact TOML layout within the shapes above.
- Variation ranges beyond those given, and the number of purchases per month.
- Invented merchant and institution names beyond those listed, following the
  `Example`, `Acme`, `Sample`, `Demo` pattern.
