# v0.5.1: the ledger, one account at a time

Date: 2026-11-15. Status: draft for the owner's approval.

## Why

The ledger is where you watch one account's money move, and where you settle
what Metis can't account for yet. Today it falls short in five ways:

- It shows checking only.
- It hides a card statement once the statement is paid.
- It shows a bill's name in place of what the bank wrote.
- It can't settle a row.
- It can't narrow down to what you're looking for.

In the owner's words:

> I always want to see credit card statements that will be coming out of a
> checking account, and right now I am only seeing them if they are not
> calculated as fully satisfied.

> ledger is currently built to implicitly only show a checking account but I
> want to be able to switch between accounts.

> idk how we have account payment source set up, but that might need to be
> worked out so that credit card statements show in the correct account
> ledgers, this will align with 1.0 when we get there.

## Goals

1. The ledger shows one account at a time, and any account can be shown:
   checking or a card.
2. A card's statements always show on the ledger of the account that pays them,
   paid or not.
3. Every imported row shows what the bank wrote, the bill it's assigned to and
   its category, and it can be settled in place: categorize, split, match to a
   bill, transfer, note.
4. A filter narrows the rows by categories (with uncategorized) and by a search
   of descriptions, and switches the account. One click applies it.
5. One button jumps to where the forecast begins.
6. The rest of the v0.5.0 ledger phase ships too: row lines, no table scroll,
   no page reloads, transfers by merchant.

## Non-goals

- Several accounts on one ledger. The account column planned in #98 is dropped.
- Opening balances per account. 1.0 gives accounts an opening balance.
- Universal search. That's epic #120.
- Credit limits and credit utilization. That's #121.
- Account types beyond checking and credit card.

## Anti-goals

- A filtered view that shows a running balance which isn't the account's real
  run-down.
- A statement counted twice. A paid statement's row adds nothing.
- For a user with one checking account, the checking ledger's balances or the
  dashboard's figures changing in any way other than the changes described
  here. A user with several checking accounts sees each on its own ledger, and
  the dashboard follows the default one.
- A second code path for card ledgers.
- Real data in tests, docs, commits or the PR. Examples are invented.

## Decided by the owner

- **Scope:** the whole v0.5.0 ledger phase (#98, #107, #27, #29), plus the
  requests below. Left out: the account column, and several accounts at once.
- **Approach:** A, one ledger builder for any account (see Approaches).
- **Card statements** always show in the ledger of the account that pays them,
  even when fully paid.
- **description** shows the original description. A new **bill** column shows
  the bill the transaction is assigned to.
- A transaction with no bill or no category can be assigned one in the ledger.
- The **source** column is renamed **type**.
- The ledger shows the selected account. The account is chosen in the filter.
- **The filter:** the account; uncategorized or specific categories, from a list
  with "all" at the top as the default, searchable, with several picks allowed;
  and a search of descriptions. An apply button submits it in one click.
- **Filtering hides the balance column and the summary cards**, because the rows
  shown are no longer the account's run-down.
- **The same columns on every account:** `payments`, `charges` and `balance`.
- **Credit cards, and any other debt account, show a negative balance** while
  money is owed.
- The "forecast from" pill becomes a **jump to forecast** button that scrolls to
  the forecast-begins row.

## What this changes in the v0.5.0 roadmap

This design replaces section 6 (Ledger) of
`docs/superpowers/specs/2026-11-14-v0-5-0-roadmap-design.md`, and the ledger
line of its Testing section. That section stands where this one doesn't change
it. The differences:

- The old `source` column becomes `type`, not `item type`, and there is no new
  account column.
- A matched row shows the bank's original text in `description` and the bill in
  `bill`. It no longer shows the bill's name with the bank's text on hover.

## Design

### 1. Accounts gain "paid from"

Today a card has no paying account. Every statement is assumed to come out of
checking, and a card payment is recognized by a text pattern on the card plus
matching amounts and dates.

- `accounts.paid_from_account_id` (BIGINT, may be empty): for a credit card, the
  checking account that pays its statements. It must be one of the same user's
  checking accounts. Checking accounts leave it empty.
- **Migration:** on every card, set it to the user's checking account when the
  user has exactly one. Otherwise leave it empty.
- **Empty means the default checking account:** the user's checking account with
  the lowest id. Nothing moves for anyone, since today every statement lands in
  checking.
- The account edit page gains a **paid from** select for cards, listing the
  user's checking accounts.
- Card payment matching (`match_card_payments`) pairs a card's payment only with
  a debit from its paying account.
- This is the 1.0 households' `paid_from`, so the move to 1.0 carries it over.

### 2. One ledger builder for any account

One function builds an account's ledger rows for a window. It replaces the
checking-only path in `collect_blended_transactions`. The dashboard, and
`first_projected_negative`, call it for the default checking account, so they
read the same rows the ledger shows.

**Actuals.** The account's imported rows, up to the account's own cutover: the
latest date imported for that account.

**Forecast, from the cutover:**

- **Checking:** the bills, income and one-time items on this account, plus
  those with no account when this is the default checking account (as today).
  Then one statement row for each statement due on every card this account pays
  (section 3).
- **A card:** its expected charges (bills on the card), and on each due date the
  statement payment coming in from its paying account. Both come from
  `build_card_statements`, which already works them out. Nothing is computed a
  second time.

**Balance.** Payments raise it and charges lower it, on every account.

- **Checking:** cash. The default checking account starts from the
  `starting_balance` setting, as today. Any other checking account starts at 0
  until 1.0's opening balances.
- **A card, or any debt account:** negative while money is owed. It starts from
  the card's balance at the window's start. A charge takes it further below
  zero, and a payment brings it back toward zero.

**Types on a card's ledger:** an expected charge is `bill`, and a statement
payment coming in is `card`.

### 3. Card statements always show

- On the paying account's ledger there is one row for each statement whose due
  date falls inside the window and after that account's cutover, whatever is
  left to pay on it.
- The row's expense is what's left to pay. A statement paid in full shows $0
  with its status pill, such as `paid` or `no balance`, so the balance isn't
  counted twice.
- The expand toggle and the link to the statement stay.
- Statements due on or before the cutover still don't show. The account's
  actual payment rows already cover them.
- Only what's shown changes. Which statements are assumed paid, and for how
  much, stays as it is.

### 4. Columns

`date · description · bill · category · type · payments · charges · balance`,
then the row menu. Every account has the same columns. `payments` is money in,
`charges` is money out, and `balance` is the running balance (section 2).

| Column | Imported rows | Forecast rows |
| --- | --- | --- |
| description | the bank's text as imported (`imported_transactions.description`), not the cleaned merchant name | the bill's, one-time item's or statement's name, as today |
| bill | the bill, income or one-time item it's matched to; when empty, the match action | blank: the row is the bill |
| category | as today; when empty, a click opens the shared picker (#27) | as today |
| type | `actual` | `bill`, `income`, `one-time` or `card` |

- `type` is the old `source`. In code it's the row key `type` and the classes
  `type-*`. This applies to ledger rows only. Budget line items keep their own
  `source` key.
- The summary cards read the same on every account: `current balance`,
  `payments`, `charges`, `projected balance`.

### 5. Row lines (#98)

As in the roadmap: green when matched, red when not settled (uncategorized or
unbudgeted), amber when marked unexpected, and none for transfers, which card
payments are. Forecast rows keep their line.

### 6. Settle a row (#107, #27, #29)

As in the roadmap's section 6, on any account's ledger. The panel for an action
opens in a row under the transaction.

- **categorize:** clicking the category cell opens the shared picker. The row
  menu offers the same. The rule prompt follows.
- **split:** the split form from `split.html`, made a shared part. A split row
  shows a pill for each of its categories.
- **match:** the shared `settle` part: match to a bill, new bill, one-time,
  unexpected. A matched row's menu offers `unmatch`.
- **transfer:** marks the row a transfer.
- **note:** shows and edits the transaction's note.

### 7. Filter and search

A **filter** button in the page bar, next to **jump to forecast**, opens a
dialog built on the date range dialog's pattern: cancel, apply, the same frame.

```
ledger · Example Checking   [groceries ×] [search: acme ×] clear   [filter] [jump to forecast]

┌ filter ─────────────────────────────── cancel  apply ┐
│ account     [Example Checking ▾]                     │
│ search      [description, bill or note             ] │
│ categories  [search categories                     ] │
│             [x] all                                  │
│             [ ] uncategorized                        │
│             [ ] groceries                            │
│             [ ]   dining                             │
└──────────────────────────────────────────────────────┘
```

**Fields:**

- **account:** every account the user has. Defaults to the default checking
  account.
- **search:** matches the original description, the bill's name and the note.
  It ignores case and matches part of a word.
- **categories:** "all" comes first and is the default, then "uncategorized",
  then each category grouped under its parent. A search box narrows the list,
  and several can be picked. Picking "all" clears the others, and picking any
  other clears "all". Picking a parent includes its children.

**Applying:**

- Apply submits once. The filters live in the address:
  `/ledger?account=<id>&q=<text>&category=<id>&category=uncategorized`.
- The page bar names the account, and shows a chip for each active filter.
  Each chip can be removed, and **clear** resets everything to the defaults.

**What a filter does.** A search, a category pick other than "all", or a
merchant filter:

- hides the rows that don't match;
- hides the `balance` column, the month summary rows and the summary cards;
- keeps the forecast-begins row.

**What a category matches:**

- An imported row by its category. A split row matches any of its parts.
- A forecast row by its bill's category.
- Statement rows show only under "all".
- "Uncategorized" means what budget means by it: imported spending with no
  category, transfers excluded. Forecast rows never count as uncategorized.
  Both pages read one definition.

**Switching the account alone isn't a filter.** That account's balance is its
real run-down, so the balance column and the summary cards stay.

**Transfers by merchant** (roadmap pick 6) are one more filter:
`merchant=<token>` shows as a chip. That view offers one action: mark every
debit from this merchant as a transfer, now and on later imports
(`POST /merchants/transfer`).

### 8. Jump to forecast

It replaces the "forecast from" pill. It scrolls to the forecast-begins row,
smoothly unless the browser asks for reduced motion. It shows only when that
row is on the page.

### 9. No page reloads, no scroll (#98)

- An action replaces the ledger table with the server's fresh copy from
  `GET /ledger/table`, keeping the current account and filters.
- No `.table-wrap` scrolls, on any page, at any width. Popups open over the
  table, and long text wraps inside its cell.
- At 760px and below, each ledger row takes two lines. The first has date,
  description and amount. The second has bill, category, type, and the balance
  when it's shown.

## Shared parts

- **Reused:** `category_picker`, `settle`, `rule_prompt`, `partial.js`, and the
  date range dialog's frame.
- **New, built once in `_parts.html` and `static/`:**
  - `split_form`, used by the split page and the ledger;
  - `note_form`, used by the ledger now and the inbox later;
  - the filter dialog.

## Routes

| Route | Change |
| --- | --- |
| `GET /ledger` | adds `account`, `q`, `category` (repeatable), `merchant` |
| `GET /ledger/table` | new: the table alone, with the same parameters |
| `POST /transactions/{id}/note`, `POST /transactions/{id}/transfer` | new |
| `POST /merchants/transfer` | new |
| account edit | gains `paid_from` |

These rules hold for every route:

- It stays behind the session check.
- Every `next` goes through `resolve_safe_redirect_target`.
- Every query is scoped to the signed-in user.
- An `account` that isn't the user's falls back to the default checking account
  with a message.

## Migrations

One step adds `accounts.paid_from_account_id` and sets it as in section 1.
Starting from the v0.4.0 fixture, it is tested to survive a second run as a
no-op.

## Testing

Built test-first, with synthetic data only.

- **Builder:**
  - With one checking account, the checking ledger gives the same rows and
    balances as today for the same data, apart from the added $0 statement rows.
  - With one checking account, the dashboard's figures don't change.
  - A card's ledger has its imported rows, its expected charges and the
    payments coming in on due dates. Its balance is negative while money is
    owed: a charge lowers it, and a payment raises it.
- **Paid from:**
  - Statements show only on the paying account's ledger.
  - Payment matching uses only the paying account.
  - The migration sets it when there is one checking account, and leaves it
    empty otherwise.
- **Statements:**
  - A statement paid early shows as $0 with its status.
  - An unpaid one shows what's left.
  - None due on or before the cutover show.
- **Columns:**
  - The same header order and labels on checking and on a card, and the same
    summary card labels.
  - Imported rows show the original description.
  - Matched rows show the bill, and forecast rows leave `bill` blank.
- **Filter:**
  - Each field alone and together.
  - "all" excludes the other picks.
  - A parent includes its children.
  - Uncategorized uses budget's definition.
  - Search covers description, bill and note.
  - The balance column, the month summary rows and the summary cards hide
    under a filter, and stay when only the account changes.
  - The address round-trips, and chips clear.
- **Settle:**
  - Each action works from a row, on checking and on a card.
  - The refreshed table keeps the filters.
- **Jump to forecast** shows only when a forecast-begins row is on the page.
- **Layout:** lines by state, no scroll at any width, the two-line phone row.
- **Browser:** the visual check, the filter dialog, and one background action.
- **Rehearsal:** before the PR, the migration runs against a scratch copy of the
  local database, and the checking ledger and dashboard are compared before and
  after. Only what was proven is reported, never figures.

## Approaches considered

| Approach | Trade-off | Outcome |
| --- | --- | --- |
| **A. One ledger builder for any account** | One code path for every account, so a later ledger change is made once. Matches 1.0, where accounts are at the center. More work up front. | Picked |
| B. Keep the checking ledger, add a card ledger built from the statement code | Faster to build, but two code paths for one screen, and every later change made twice | Not picked |
| C. Render every account's rows and filter in the browser | Each account has its own balances, and pages get heavier (#111) | Dropped |

## Calls made in design (the owner can overturn any)

- An empty "paid from" means the default checking account, so nothing moves on
  upgrade.
- Expected items with no account stay on the default checking account, as
  today.
- The `starting_balance` setting belongs to the default checking account. Any
  other checking account starts at 0 until 1.0.
- Filters live in the address, so back works and a filtered view can be linked.
- Search covers the original description, the bill's name and the note.
- "Original description" is the bank's text as imported.
- `bill` is blank on forecast rows, because the row is the bill.
- The summary cards read `current balance`, `payments`, `charges` and
  `projected balance` on every account, matching the columns.
- Picking a parent category includes its children.
- Statement rows show only under the "all" category.
- The ledger opens on the default checking account each visit, unless the
  address names another.
- The dashboard, and the first-negative-balance warning, follow the default
  checking account. For a user with one checking account that's no change.

## Left to the builder

- The names of the builder function and the filter dialog part.
- Keeping a card's balance on the ledger, and what the statement page says is
  owed after each due date, from disagreeing where an issuer balance has been
  entered.
- Whether "due, not yet posted" applies on a card's cutover as it does on
  checking's.
- How the filter dialog behaves without JavaScript, within "apply submits once".
- Phone layout details, within the two-line rule.

## Shipping

- Branch `feat/v0.5.1`, one PR into `main`, squash-merged.
- Run the privacy guard and the leak check before opening the PR.
- Issues on milestone v0.5.1:
  - #98, edited to drop the account column and rename `source` to `type`;
  - #107, #27 and #29;
  - new: one account at a time with "paid from"; statements always shown;
    filter and search; jump to forecast.
- Tag v0.5.1 after the merge.
