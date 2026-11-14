# v0.5.0: one model for budgets, bills and settling

Status: the decisions below were approved in conversation on 2026-11-14. This
document awaits the owner's approval. After that the owner reviews the build by
testing QA, and changes come from that testing.

Closes #108, #107, #106, #105, #104, #103, #102, #101, #100, #99, #98, #97, #95,
#94, #69, #31, #29, #27 and #23. Leaves #92 open (blocked by its own text) and
#21 open (the ongoing epic, which gets a progress note).

All examples in this document are invented.

## Why

Budgets, categories, expected transactions and reconciling were built
separately and now overlap (#99). One-time expected transactions have four
names and two add paths. Budget does four jobs. Three rule systems map
transactions to things. Settling a transaction means leaving the screen that
shows it.

v0.5.0 merges these into one model, gives each thing one name, and makes each
screen a place to settle what it shows. It also finishes the open work on the
ledger, inbox, dashboard, accounts and visual style, so that one release clears
the open issue list.

## Goals

- **One name per thing**, in the UI, routes, code and data.
- **A category's budget is exactly its expected transactions.** Nothing is set
  by hand.
- **Settle where you look.** A transaction can be categorized, split, matched to
  a bill, turned into a one-time item or marked unexpected from budget and from
  the ledger, without leaving the page.
- **The inbox is a to-do list.** Each item links to the page that settles it.
- **Bills** can carry a balance, interest, a biller link and a charge schedule.
- **Every migration is rehearsed** on a copy of real data in QA before it reaches
  production.

## Non-goals

- Sharing templates with `metis_hq` (#92), Plaid, or any live connection.
- More than one level of sub-categories.
- A balance per checking account. Checking accounts stay combined, as today.
- QIF import. OFX and QFX only.
- Rewriting git history.

## Anti-goals

- Real financial or personal data in the repo, an issue, a PR or a commit
  message.
- A second form, list or rule system for a job that already has one.
- Text that explains a screen instead of a layout that doesn't need it.
- A migration that changes what a bill forecasts, or loses a link, a category or
  a rule.
- A table that scrolls sideways, or a control whose label wraps.

## Decided by the owner

1. **Bills.** `bill` means any scheduled money out, everyday spending included
   (a weekly groceries bill with a match rule). Scheduled income is called
   `income`. Both use the same form and the same table.
2. **One-time.** The one name for one-time expected transactions is `one-time`.
3. **Unexpected.** The word means only the explicit mark (#105, #106). On the
   ledger: green for matched, red for not settled yet, amber for marked
   unexpected. Transfers and card payments get no line.
4. **One category picker.** One shared type-ahead picker, grouped by parent,
   used on the ledger, budget, the inbox and forms.
5. **Rules.** The inbox's per-merchant category decisions fold into category
   rules. Two rule systems remain, each with its own job: category rules and
   bill match rules.
6. **Transfers** are marked on the ledger. An inbox transfer suggestion opens
   the ledger filtered to that merchant.
7. **Bill details.** A bill with a charge schedule covers the period ending on
   its due date. Card payments stay separate and are forecast from statements.
8. **Dashboard chart.** The account toggle shows checking or one card's balance
   owed. The category filter shows cumulative spending in the chosen categories.
9. **Import formats.** Add OFX and QFX import, a search box and illustrations to
   the import method step.

Also agreed:

- One prefillable form for expected transactions, on its own page, used by
  budget, the inbox and the "new bill" actions (#97, #101, #104, #105, #107).
- A parent category's budget and actual roll up its children's (#100).
- One-time items are added from budget, not from the ledger (#101's comment and
  #107).
- Splits may leave a remainder on the transaction's own category. They are not
  forced to total the transaction (#29).
- A rule's date range means active from and to (#103). Notes show on the
  transaction's ledger row (#106). Semi-monthly days stay as two explicit days
  (#69). The `review_actions` table is renamed with a migration (#95).
- The restyle (#94) goes last. #102 folds into #104.
- A test workflow runs on every PR. "Approve" is the owner's OK in conversation,
  because GitHub does not let a PR's author approve it.
- Everything lands in one branch and one PR.
- **A visual check at the end**: no text wraps in a way that makes the UI look
  bad.

## Vocabulary

| Thing | Name in the UI | Name in code and data | Was |
| --- | --- | --- | --- |
| Scheduled money out | bill | `bill`, table `bills`, `/bills` | recurring (expense) |
| Scheduled money in | income | a `bills` row with `kind = 'income'` | recurring (income) |
| A single expected transaction | one-time | `one_time`, table `one_time_items`, `/one-time` | manual, one-time, one_time, one-off |
| Bills, income and one-time together | expected transactions | `expected` | same |
| An imported row | transaction (`actual` as a ledger item type) | `imported_transactions` | same |
| The to-do page | inbox | `inbox`, table `inbox_actions` | review |
| Transaction to category rule | match rule (on a category) | `category_rules` | category rules and merchant decisions |
| Transaction to bill rule | auto-match rule (on a bill) | `expected_match_rules` | same |
| Merchant is a transfer | transfer rule | table `transfer_rules` | merchant decisions (transfer) |

Ledger item types are `actual`, `bill`, `income`, `one-time` and `card`.

## A transaction's state

| State | Meaning | Ledger line | Settled from |
| --- | --- | --- | --- |
| matched | linked to an expected transaction | green | nothing to do |
| uncategorized | spending with no category | red | budget's `uncategorized` list, the ledger, the inbox |
| unbudgeted | has a category, no expected transaction | red | its category on budget, the ledger |
| unexpected | marked unexpected, with a note | amber | the inbox (for the note) |
| transfer | money moved between the owner's accounts | none | the ledger |

Uncategorized and unbudgeted are both "not settled yet", which is what red means.

## Roadmap

One branch, `feat/v0.5.0`. One commit per phase. Each phase is built test-first
and leaves the suite green.

| Phase | What | Issues |
| --- | --- | --- |
| 0 | Groundwork: test workflow, privacy guard, migration harness | none |
| 1 | Names and data: inbox, bills, day from the due date, one-time | #95, #108 (rename), #69, #101 |
| 2 | Categories and rules: sub-categories, the picker, merged rules | #100, #27 (control), #103 (rule dates) |
| 3 | Budget: one section, uncategorized and unbudgeted lists | #104, #102, #103, #105 |
| 4 | Ledger: columns, lines, no scroll, row actions, transfers | #98, #107, #27, #29 |
| 5 | Inbox: links out, unexpected notes, uncertain matches | #97, #106, #101 |
| 6 | Bills in depth: balance, interest, biller link, charge schedule | #108 |
| 7 | Dashboard chart and accounts | #31, #23 |
| 8 | Restyle existing screens | #94 |
| 9 | Visual check: no bad wrapping, no sideways scroll | owner's request |
| 10 | Ship: privacy scan, security review, docs, PR, merge, QA | none |

## Design

### 1. Groundwork (phase 0)

- **Test workflow.** `.github/workflows/tests.yml` runs on pull requests and on
  pushes to `main`: Python 3.12, `pip install -r requirements-dev.txt`, `pytest`.
  A second job runs the visual check (section 12) in headless Chromium.
- **Privacy guard.** `tests/test_no_real_data.py` reads every tracked file and
  fails on: an email address outside the `example.*` domains and tool no-reply
  addresses; a private or tailnet IP address; a `*.ts.net` host other than the
  documented placeholder; an absolute home path; a tracked database or statement
  file; a name from a short list of well-known banks, card products and
  brokerages.
- **Leak check.** `tools/leak_check.py` compares the working tree and a text
  file (a PR body, for example) against the names, merchants, accounts and
  amounts in the local database, and prints only masked hits. It runs before
  the PR is opened. It reads the database read-only and holds no data itself.
- **Migration harness.** Each one-time data step is a named function, recorded
  in `applied_migrations`, and runs inside a transaction so a failure leaves the
  database as it was. `tests/fixtures/schema_v0_4_0.sql` is the v0.4.0 schema
  with synthetic rows. Tests start from it, run `init_db()`, and check the new
  shape and that nothing was lost.

### 2. Names and data (phase 1)

**Inbox (#95).** `review.py` becomes `inbox.py`, `routes/review.py` becomes
`routes/inbox.py`, `review.html` becomes `inbox.html`. Functions, context keys
(`inbox_waiting`, `inbox_line`), `inbox-*` CSS classes, `data-inbox-*`
attributes and test files follow. The `review_actions` table becomes
`inbox_actions`. `/review` still redirects to `/inbox`. No behaviour or copy
changes.

**Bills (#108 rename).** `recurring` becomes `bill` in UI copy, routes, code and
data:

- Table `recurring_items` becomes `bills`. `amount_overrides.recurring_item_id`
  becomes `bill_id`.
- `source_type = 'recurring'` becomes `'bill'` in `expected_reconciliations` and
  `expected_match_rules`.
- Routes move from `/recurring` to `/bills`. The `GET /recurring` redirect is
  dropped.
- A row with `kind = 'income'` is called `income` wherever it is shown.

**Day from the due date (#69).** `start_date` becomes `first_due_date` and
`day_of_month` is removed. Monthly, every-N-months and yearly bills recur on the
day of `first_due_date`, clamped to the last day of shorter months as
`iter_monthly` does today.

- The Day of month input and its hidden "touched" field leave every form.
- Suggestions set `first_due_date` to the most recent occurrence that falls on
  the modal day.
- Migration: a row whose `day_of_month` differs from its start date's day moves
  its date to the first date on or after it that falls on `day_of_month`. When
  that month is too short to hold the day, the nearest earlier date that holds
  it is used instead, so the day survives.

**One-time (#101).**

- Table `manual_transactions` becomes `one_time_items`, and `tx_date` becomes
  `expected_date`. `source_type = 'one_time'` is unchanged.
- One form and one set of routes: `GET /one-time/new`, `POST /one-time`,
  `GET` and `POST /one-time/{id}/edit`, `POST /one-time/{id}/delete`. `/manual/*`
  and `/expected/one-time*` are removed.
- **Settles itself.** After an import, and when a one-time item is added or
  edited, Metis looks for the actual that pays it:
  - Candidates are unlinked, non-transfer actuals with the item's sign, dated
    within 10 days of its expected date, on the item's account if it has one.
  - Exactly one candidate with the same amount to the cent is certain. Metis
    links it (`matched_via = 'auto'`), and the item stops counting in the
    forecast.
  - Several exact candidates, or one within 2% but not exact, is uncertain.
    So is a candidate that is also the exact match of another unsettled
    one-time item. Nothing is linked, and the item appears in the inbox
    `matches` list.

**The expected form.** One template, `expected_form.html`, serves bills, income
and one-time items, for add and edit:

```
add bill
name [               ]   type [expense v]   amount [        ]
category [picker     ]   account [          v]
repeats [monthly    v]   first due date [    ]   end date [    ]
charged [as billed  v]   balance [        ]  as of [    ]   interest rate % [   ]
biller link [                         ]
[save]  [cancel]
```

- `repeats` offers `one-time` and each frequency on add. On edit the type of
  item is fixed.
- Fields that don't apply are hidden: `every x months` and the semi-monthly days
  as today, `first charge` only with a charge schedule, `as of` only with a
  balance, and the bill-only fields for income and one-time.
- On edit, a changed amount still asks "all occurrences" or "from today
  forward".
- The sketch shows the finished form. `charged`, `balance`, `interest rate` and
  `biller link` arrive in phase 6.
- The form opens prefilled from ids and enums in the query string, never from
  names or amounts: `category_id`, `kind`, `repeats`, `from_tx` (an imported
  transaction), `from_suggestion` (a short hash of the suggestion's key) and
  `next`. Saving with `from_tx` also links that transaction. Saving with
  `from_suggestion` marks the suggestion added, as today.

### 3. Categories (phase 2)

**Sub-categories (#100).** `categories.parent_id` is optional. One level only: a
category with children can't take a parent, and a sub-category can't have
children. Existing categories stay top-level.

- A parent's budget and actual are its own plus its children's.
- An expected transaction, a rule or a split can target a parent or a
  sub-category.
- A sub-category's pill reads with its parent: `food › groceries`.
- A new sub-category starts with its parent's color.
- Category edit gains a `parent` field, which moves a sub-category or promotes
  it to top-level.
- Deleting a parent that has children asks: move them to top-level, or delete
  them too. One must be chosen. Deleting a sub-category behaves as deleting a
  category does today.

**The picker (#27, pick 4).** `parts.category_picker` stays a `<select>`, now
with an `<optgroup>` per parent, so it works without script. One shared script,
`static/category-picker.js`, turns each into a type-ahead box:

- Typing narrows the list. Sub-categories stay under their parent.
- When nothing matches, the list offers to add what was typed. Adding creates
  the category and assigns it in one step.
- In a list row (budget, the inbox, the ledger's category cell) choosing a
  category submits the row. In a form it only sets the field.
- The `transfer` choice leaves the picker. Transfers have their own action.

### 4. Rules (phase 2)

Category rules and the inbox's per-merchant category decisions become one table
and one list.

- `category_rules` gains `match_on` (`description` or `merchant`), `date_from`
  and `date_to`.
- A `description` rule matches when the description contains the pattern, as
  today. A `merchant` rule matches when the transaction's merchant key equals
  the pattern, and only on spending, as a merchant decision does today.
- Both kinds honor the amount bounds and the date range. An empty bound is open.
- A row a rule categorized carries `category_via = 'rule'` and the rule's id in
  `category_rule_id` (renamed from `decision_id`).
- A category picked by hand is never changed by a rule.
- When both kinds match a row, the merchant rule wins. Among description rules
  the newest wins, as today.
- Rule-set categories are worked out again from all rules whenever a rule is
  added, changed or deleted, and after each import.
- Confirming a merchant in the inbox creates a `merchant` rule. Undo deletes the
  rule and clears the rows it set.
- A category's rules are listed on its edit page: what they match on, amount and
  dates.
- `merchant_decisions` keeps only transfers and becomes `transfer_rules`.
  `transfer_decision_id` becomes `transfer_rule_id`.

**The rule prompt (#103, #107).** After a category is assigned to one
transaction, or a transaction is matched to a bill, a prompt offers a rule:

- For a category: name pattern (from the transaction's name), amount range
  (10% either side of this amount), dates (from the start of the page's date
  range, no end). Every field can be edited or cleared. The prompt shows how
  many other uncategorized transactions the rule would match, and updates as
  the fields change.
- For a bill: the same pattern, any amount.
- Confirming saves the rule, which applies to existing transactions. Skipping
  keeps only the one assignment.

### 5. Budget (phase 3)

Budget becomes one section, named `budget` (#104).

```
[2 suggested] [14 bills] [3 one-time]

budget
[budgeted] [actual] [difference] [income]
[active | completed | paused | all]  [expand all] [collapse all]  [new category]

> income                                  $4,000.00 / $4,000.00
v housing                                 $1,240.00 / $1,300.00   ▼ $60.00
    rent          monthly · next 12/01     $1,200.00 / $1,200.00  active  [reconcile] [menu]
    repair        one-time · 11/20            $40.00 /   $100.00          [reconcile] [menu]
    [add bill] [add one-time] [edit category]
    unbudgeted (12)
      11/02  acme hardware    $40.00   [bill v] [new bill] [one-time] [unexpected]
      ...                                                        < 1 / 2 >
    > housing › utilities                   $90.00 /   $100.00
> uncategorized                              $52.00   no budget
```

- **No hand-set budgets.** A category's budget is the sum of its bills' and
  one-time items' occurrences in the date range. `categories.budget_amount`, the
  monthly budget inputs, `POST /categories/{id}/budget`, `explicit_monthly`, the
  proration note and the inbox `budgets` list are removed.
- **Rows.** Every category is listed, so each one can be added to. `income` is
  the first row and lists scheduled and one-time income. `uncategorized` holds
  expected transactions with no category. A sub-category's row sits inside its
  parent's, and each list sits under the exact category it belongs to.
- **Inside a category:** its bills and its one-time items dated in the range,
  each with its figures, `reconcile`, and a menu (edit, pause or resume,
  delete). A bill also shows its next due date, its remaining balance and
  payoff date when it has a balance, and its biller link. Then `add bill`,
  `add one-time` and `edit category`, which open the expected form with the
  category set.
- **Status filter** (active, completed, paused, all) sits at the top and filters
  bills in every category.
- **The `today` chip is removed (#102).** The other chips stay, with the new
  names.
- The `add expected transaction`, `recurring` and `one-time` panels are removed.

**Uncategorized transactions (#103).** Expanding `uncategorized` lists its
transactions in the date range, 10 per page: date, name, amount and the picker.
Picking a category assigns it, the row leaves the list, and the rule prompt
opens.

**Unbudgeted transactions (#105).** Each category lists its spending in the
range that has a category, no link to an expected transaction and no unexpected
mark, 10 per page. `income` lists unlinked deposits the same way: money arriving
in a non-card account that is not a transfer. Each row offers:

| Choice | What happens |
| --- | --- |
| bill (the bills of the same kind, nearest matches first) | links the transaction to that bill, then offers an auto-match rule |
| new bill | opens the expected form prefilled from the transaction; saving links it |
| one-time | creates a one-time item with the same name, amount, date, account and category, and links it |
| unexpected | marks it; it leaves the list and appears in the inbox |

Every choice can be undone from the message it leaves, as inbox actions are.

- A linked transaction takes its bill's category, as today, so it may move to
  another category's row.
- A transaction marked unexpected is skipped by auto-match rules. Matching it by
  hand clears the mark.

**No page reloads.** Each action posts in the background and the budget section
is replaced with the server's fresh copy, so totals are always right and the
page keeps its place. Which rows are open, the page of each list and the status
filter travel with the request. Without script the same forms reload the page.

### 6. Ledger (phase 4)

**Columns (#98).** `date · description · category · item type · source ·
income · expense · running balance`, then the row menu. `item type` is the old
`source` column (`row.item_type`, `item-type-*` classes). `source` is the
account: the one an actual or card row was imported from, or the one linked to
a bill or one-time item, or `-`.

**Lines (#98, pick 3).** On actual rows: green when matched, red when not
settled, amber when marked unexpected, none for rows marked as transfers, which
is what card payments are. Forecast rows keep their line.

**No + button (#107).** The insert rows, the inline form, their script and
styles, and `POST /manual` are removed. One-time forecast rows keep an edit and
delete menu, which uses the one-time routes.

**Settle a row (#107, #27).** Every actual row can be settled in place. The
panel for an action opens in a row under the transaction.

```
11/02  acme hardware  [home]  actual  example checking   -   $40.00   $2,310.00  [menu]
  | categorize | split | match | transfer | note |
  | split:  [home v] [$25.00]   [garden v] [$15.00]   [add part]   [save] [clear]
```

- **categorize**: clicking the category cell opens the picker (#27). The menu
  offers the same. The rule prompt follows.
- **split**: the split form from `split.html`, as a shared part. A split row
  shows a pill for each of its categories.
- **match**: the same four choices as an unbudgeted row on budget. A matched
  row shows the expected name, the actual name on hover and the green line, and
  its menu offers `unmatch`.
- **transfer**: marks this row a transfer.
- **note**: shows and edits the transaction's note.

**Transfers by merchant (pick 6).** `/ledger?merchant=<token>` lists only that
merchant's actual rows, with a chip to clear the filter and one action: mark
every debit from this merchant as a transfer, now and on later imports. That
action is today's inbox transfer confirm. The token is a short hash of the
merchant key, so no name is in the URL.

**No page reloads.** As on budget, an action replaces the ledger table with the
server's fresh copy.

**No scroll (#98).** No `.table-wrap` scrolls, on any page, at any width.

- `.table-wrap` stops being a scroll container. Popups such as the row menu
  open over it.
- Long text wraps inside its cell instead of widening the table.
- At 760px and below every table drops its header row and shows each row as
  stacked label and value pairs. The ledger gets its own two-line row: date,
  description and amount on the first line; category, item type, source and
  running balance on the second.

### 7. Inbox (phase 5)

The lists, in order: `imports`, `transfers`, `categories`, `matches`,
`unexpected`, `bills`.

- **bills (#97).** Each suggestion links to the expected form, prefilled.
  Saving removes the suggestion and returns to `/inbox#bills`. `dismiss` stays
  on the row. The inline `recurring_fields` form is removed.
- **transfers (#97).** Each merchant links to the ledger filtered to it.
  `dismiss` stays on the row. The inline confirm is removed.
- **categories.** Unchanged in layout. It uses the shared picker, and each row
  gains a `ledger` link for a merchant that is really a transfer.
- **matches (#101).** One row per one-time item with an uncertain match,
  linking to its reconcile page.
- **unexpected (#106).** One row per transaction marked unexpected: date, name,
  amount, account, category, a notes field and `save`. Saving the note settles
  the entry and it leaves the inbox. `dismiss` hides it without a note. `unmark`
  sends the transaction back to its category's unbudgeted list.

```
unexpected  2 · $95.00
  11/04  acme diner    $55.00  example card  dining   [note            ] [save] [dismiss] [unmark]
```

- **budgets** is removed (#104).
- A transaction gains `unexpected` and `note`. The note is stored with the
  transaction and shows on its ledger row and its statement line.
- Inbox actions use the same background replace as budget and the ledger.

### 8. Bills in depth (phase 6)

**Schedule.** Due dates run from `first_due_date` by the bill's frequency until
its end date, or indefinitely. A bill shows its **next due date**: the first due
date from today on that no linked payment has settled.

**Amount.** Without a charge schedule, a due date's amount is the bill's amount
in effect that day, as today.

**Charge schedule.** `charge_frequency` (weekly, bi-weekly or monthly) and
`charge_first_date`. The weekday or day of month comes from that date.

- The bill's amount becomes the amount per charge, and the form labels it so.
- A due date's amount is the sum of the charges dated after the previous due
  date, up to and including this one. For the first due date, the previous one
  is taken to be one period earlier. A month holding five charge days expects
  more than a month holding four.
- A due date with no charges is not a due date.
- A change to the amount "from today forward" applies to charges from that day.
- Suggested matches on the reconcile page, and the ranking of bills in the match
  list, use the computed amount of the nearest due date.

**Balance.** `balance` and `balance_as_of`. Metis runs the balance forward from
the as-of date, in date order:

- A payment linked to the bill reduces the balance by the amount that stayed on
  the bill (the transaction less any split parts).
- At each due date, interest is added first when the bill has a rate.
- A due date that is still forecast pays the smaller of its amount and the
  balance. The last payment is whatever is left.
- "Still forecast" means what it means on the ledger: after the last imported
  checking date, or on it and not yet posted, and not already settled by a
  payment made early. With nothing imported, it means after the as-of date.
- When the balance reaches zero, that date is the **payoff date**, and the bill
  has no later due dates.
- A due date on or before the last imported date with no linked payment pays
  nothing.
- Due dates on or before the as-of date keep their entered amount.

The bill shows its remaining balance as of today and its payoff date. Entering a
new balance and as-of date at any time restarts the run from there. A bill with
no balance is ongoing.

**Interest.** `interest_rate` is a yearly percentage and needs a balance. The
rate per due date is the yearly rate divided by the due dates in a year (52
weekly, 26 bi-weekly, 24 semi-monthly, 12 monthly, 12 ÷ N every N months, 1
yearly). When a payment doesn't exceed its interest, the bill shows no payoff
date.

**Biller link.** `biller_url`, `http` or `https` only, shown as a link that
opens in a new tab.

**One definition.** Budget, the ledger forecast, card statements, the negative
balance alert and the reconcile page all read a bill's due dates and amounts
from one function.

### 9. Dashboard chart (phase 7, #31)

Two controls above the chart, sent as query parameters so a view can be linked:

- **account**: `checking` (all checking accounts combined, as today) or one
  card. A card shows its balance owed over time: statement activity up to its
  last import, then expected charges and assumed payments.
- **category**: one or more categories. The chart then shows cumulative spending
  in them over the range, actual up to the last import and expected after, and
  its heading says so. A parent includes its children.

In every view a vertical dotted line marks today when the range includes it.
The area left of the line has no gradient. The area right of it keeps it.

### 10. Accounts (phase 7, #23)

**Import method step.** A search box, and a card per method with an
illustration: `csv`, `ofx / qfx` and `plaid` (greyed, coming soon). Typing
narrows the cards by name and keyword. The illustrations are original inline
SVGs.

**OFX and QFX.** A file with known fields needs no template.

- `ofx_import.py` reads OFX 1 (SGML) and OFX 2 (XML) bank and card statements:
  date posted, amount, type, name and memo.
- It builds the same rows a template does and hands them to the existing import
  path, so flow, transfer detection, merchant names, dedup and the rules all
  behave as they do for CSV.
- The import pop-up accepts `.csv`, `.ofx` and `.qfx`. A template is required
  only for CSV.
- Choosing `ofx / qfx` when adding an account skips the template step.
- It is a small tag scanner, not an XML parser, so entity and DTD tricks have
  nothing to act on.

`#23`'s open question, whether account types need different properties, is
answered no for now and noted on the issue.

### 11. Restyle (phase 8, #94)

- The `--neu-*` tokens are mapped onto the existing token names, in light and
  dark.
- The frame and controls on every screen follow the shared rules: cards and
  panels, buttons, inputs and selects, chips and badges, the menu, dialogs.
- Tables, lists of transactions, figures and charts stay flat inside their
  cards.
- Control edges reach 3:1 against the page. Every focusable control shows the
  accent focus outline. Nothing moves under `prefers-reduced-motion`.
- No template carries its own `<style>` block.
- Visual design only: no layout, content or behaviour changes in this phase.

### 12. Visual check (phase 9)

A check in a real browser, run locally and in the test workflow.

- It starts Metis on a synthetic database that fills every screen, signs in,
  and opens every page at 390px, 768px and 1280px wide.
- It fails when:
  - a page scrolls sideways;
  - a `.table-wrap` can scroll;
  - text that belongs on one line takes two: buttons, chips, pills, tabs, menu
    items, table headers, card headings and amounts;
  - text is cut off by its container.
- It saves a screenshot of every page at every width, in light and dark. The
  builder looks through all of them before the PR.
- Whatever it finds is fixed in the shared rules, not per page.

## Migrations

Each step is named, runs once, and runs inside a transaction.

| Step | Change |
| --- | --- |
| `inbox_actions` | copy `review_actions` into `inbox_actions` |
| `bills` | copy `recurring_items` into `bills`; `start_date` to `first_due_date`, moved as #69 requires; drop `day_of_month`; add `balance`, `balance_as_of`, `interest_rate`, `biller_url`, `charge_frequency`, `charge_first_date` |
| `bill_overrides` | `amount_overrides.recurring_item_id` to `bill_id` |
| `one_time_items` | copy `manual_transactions` into `one_time_items`; `tx_date` to `expected_date` |
| `bill_source_type` | `'recurring'` to `'bill'` in `expected_reconciliations` and `expected_match_rules` |
| `category_parents` | add `categories.parent_id`; drop `categories.budget_amount` |
| `merged_rules` | add `match_on`, `date_from`, `date_to` to `category_rules`; turn each category decision into a `merchant` rule that owns the rows it set; copy transfer decisions into `transfer_rules` |
| `transaction_marks` | add `unexpected` and `note` to `imported_transactions`; rename `decision_id` and `transfer_decision_id` |

- A copy keeps every row's id, and the new table's sequence continues after
  the highest one, so links, rules and overrides stay valid.
- `bills` gets all of its new columns in the one copy, in phase 1. Phase 6
  starts using them.
- Rows a category decision set change from `category_via = 'merchant'` to
  `'rule'`.
- Recorded actions keep working under the new names. Those that can no longer
  be undone, such as a hand-set budget, are dropped.
- A database older than v0.4.0 is first brought to the v0.4.0 shape by the
  existing steps, which keep the old table names for that purpose only.
- Running `init_db()` twice changes nothing the second time.

## Shared parts

Built once, in `_parts.html` and `static/`:

- `category_picker`, with `category-picker.js`.
- `transaction_list`: a paged list of transactions, 10 per page, whose row
  actions are supplied by the caller. Used by the uncategorized and unbudgeted
  lists.
- `settle`: the four choices for a transaction with no expected transaction.
  Used by budget and the ledger.
- `rule_prompt`: for a category rule or an auto-match rule.
- `split_form`: used by the split page and the ledger.
- `note_form`: used by the inbox and the ledger.
- `partial.js`: posts a form in the background and replaces its section with
  the server's response.

## Routes

| Added | Purpose |
| --- | --- |
| `GET /bills/new`, `POST /bills`, `GET` and `POST /bills/{id}/edit`, `POST /bills/{id}/toggle`, `POST /bills/{id}/delete`, `GET /bills/{id}/reconcile` | bills and income |
| `GET /one-time/new`, `POST /one-time`, `GET` and `POST /one-time/{id}/edit`, `POST /one-time/{id}/delete`, `GET /one-time/{id}/reconcile` | one-time items |
| `POST /transactions/{id}/category`, `/match`, `/unmatch`, `/one-time`, `/unexpected`, `/unexpected/clear`, `/note`, `/transfer` | settle one transaction |
| `GET /rules/preview` | how many transactions a rule would match |
| `POST /merchants/transfer` | mark a merchant's debits as transfers |
| `GET /budget/section`, `GET /ledger/table`, `GET /inbox/lists` | the section a background action replaces |

Removed: `/recurring*`, `/manual*`, `/expected/one-time*`,
`/expected/{type}/{id}/reconcile`, `/categories/{id}/budget`,
`/inbox/budgets/*`, `/inbox/charges/{id}`, and `/expected/suggestions/*` once
nothing uses them. Kept: `/expected` and `/review` redirects, `/expected/rules`,
`/expected/reconcile/confirm` and `/expected/reconcile/{id}/unlink`.

Every route stays behind the session check. Every `next` goes through
`resolve_safe_redirect_target`. Every query is scoped to the signed-in user.

## Testing

Built test-first. Every acceptance line in the closed issues becomes a test.
All test data is synthetic.

- **Migrations:** from the v0.4.0 fixture: every row survives; a bill forecasts
  the same dates before and after; links, rules, categories and inbox undo
  still work; a second run is a no-op.
- **Schedules:** day from the due date, clamping on the 29th to 31st, charge
  counts in four- and five-charge months, balance run-down with and without
  interest, the final partial payment, and no payoff when interest outruns the
  payment.
- **Budget:** a budget equals its expected transactions; parents roll up and
  totals add up; each settle choice moves the right amount and can be undone.
- **Rules:** merchant and description rules, amount and date bounds, precedence,
  hand-picked categories left alone, the preview count equals what a saved rule
  categorizes.
- **Ledger:** the header order, the account under `source`, line colors by
  state, no `POST /manual`, settle actions from a row.
- **Inbox:** each list links to its page, notes save and settle, `unmark`
  returns the transaction, no `budgets` list.
- **Imports:** OFX 1 and OFX 2, bank and card, signs, and the same result as the
  matching CSV.
- **Conventions:** the existing lower-case, page copy, shared parts and style
  tests keep passing and are extended to the new parts.
- **Browser:** the visual check, plus the picker and a background action.
- **Rehearsal:** before the PR, the migrations run against a scratch copy of the
  local database, and the forecast and totals are compared before and after.
  Only what was proven is reported, never figures.

## Privacy and security

- The data policy in `CLAUDE.md` holds for code, tests, commit messages, this
  document, the PR and issue comments. The privacy guard and the leak check
  enforce it.
- Prefill and filters pass ids, enums and hashes in URLs, never names or
  amounts.
- Notes, names and biller links are escaped by the templates. A biller link
  must be `http` or `https`.
- Uploads are capped in size. The OFX reader does no entity or DTD processing.
- The icon script is pinned to an exact version with an integrity hash instead
  of `@latest`.
- Before the PR: a full security review of the branch. On the PR: CodeQL and the
  test workflow.

## Shipping

1. Open one PR from `feat/v0.5.0` into `main`. Its description says what was
   proven, with no data, and lists the issues it closes.
2. Checks: CodeQL, tests and the visual check. If they pass, the owner gives the
   OK and the PR is squash-merged.
3. Merging builds the `:main` image. QA is then switched on at the home server,
   which runs every migration on a copy of production's data. Production's own
   data is untouched until a release.
4. The owner tests QA for a few days. Changes come from that testing.
5. Release `v0.5.0` is cut from `main` after QA is signed off. It is a separate
   step.
6. #21 gets a progress note. `README.md` and the goals in `CLAUDE.md` are
   updated to the new names.

## Approaches considered

**How a page updates after an action.**

- Reload the whole page, as today. Simple, but slow and it loses the user's
  place, which is #97's complaint.
- **Replace the section with the server's fresh copy (chosen).** Rendering
  stays on the server, there is one copy of each list, totals are always right,
  and it degrades to a reload without script.
- A JSON API with lists drawn in script. It feels fastest but needs a second
  copy of every list, against the shared-parts rule and the rest of the app.

**How tables are renamed.**

- Rename in the UI and code only. Cheapest, but it leaves two names, against #95
  and #108.
- Rename in place. DuckDB refuses to rename a table or column while an index
  depends on the table, and cannot rename a sequence.
- **Copy into a new table inside a transaction, then drop the old one
  (chosen).** Clean names, sequences included, and a failed step changes
  nothing.

**The category picker.**

- Keep the plain select. No type-ahead, so #27 is not met.
- A text box with a datalist. Type-ahead, but no grouping and no clean "add
  new".
- **A select turned into a type-ahead box by one shared script (chosen).**
  Grouped, adds new, and still a working select without script.

**How it ships.** One branch and one PR, as the owner decided, with one commit
per phase so the branch reads in order. The alternative, a PR per phase, would
give smaller reviews and QA after each step.

## Calls made by the builder (the owner can overturn any)

- Budget lists **every** category, empty ones included, so each can be added
  to.
- `income` is a row at the top of budget, with the same lists and actions as a
  category.
- Inside a category, **one-time items are limited to the date range**. Bills are
  shown by the status filter whatever the range.
- **Refunds** reduce a category's actual, as today, and are not listed as
  unbudgeted.
- The ledger's `match` offers **all four** settle choices, not only bills, so
  one part serves both pages.
- **Category names stay unique per user**, sub-categories included.
- A category rule's default dates are **from the start of the page's range,
  with no end**, and its default amount range is 10% either side.
- **Merchant rules beat description rules** when both match.
- The transfer **merchant filter replaces per-row checkboxes**: the rows listed
  are the rows marked.
- A balance and a charge schedule **can be combined**.
- Interest uses a **simple per-period rate**, the yearly rate divided by the due
  dates in a year.
- **Next due date** skips a due date already paid early.
- When a start date can't hold its day of month, the migration moves it
  **earlier**, which can add one due date before the old start, instead of
  dropping a due date that was paid.
- Importing the same period once as CSV and once as OFX can duplicate rows when
  the two files word a description differently. Dedup stays as it is.
- Uncertain one-time matches get **their own inbox list**, `matches`.
- The `GET /recurring` redirect is dropped. `/review` and `/expected` keep
  theirs.
- A scheduled income row's ledger item type is `income`.
- The icon script is pinned, as a security fix found while reading the base
  template.

## Left to the builder

- File and function names not given here, and how `services.py` is divided.
- The exact markup and CSS of the new parts, within the design principles and
  `docs/design/neumorphism.md`.
- The upload size cap.
- The synthetic data set used by the visual check.
- The wording of messages, in lower case and without explanation text.
