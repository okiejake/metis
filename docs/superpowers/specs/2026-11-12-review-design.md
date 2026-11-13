# Review: design

Date: 2026-11-12
Status: awaiting owner approval
All names and figures in this document are invented.

## Why

Metis cannot yet answer "where did the money go". Most imported spending has no
category, a charge can only get one by being matched to an expected item or a
rule, and there is no screen for giving a charge a category by hand. Splitting a
charge across categories is built but nothing links to it. Money moved between
the owner's own accounts is counted as spending whenever the import did not
recognize it: a card payment leaving a checking account is counted on top of the
card charges it pays for.

Every later project (the decision dashboard, trends, recommendations) reads
these same numbers. Until the data is defined, those screens would be confident
and wrong. Review is the place where the owner defines it, quickly.

## What it is

One page, **Review**, that lists everything Metis cannot yet account for, with
the largest amounts first, and lets the owner settle each item with one decision.
A decision made for a merchant covers that merchant's past and future charges.

It is project 1 of 6. The others, in order, each get their own design: decision
dashboard, recurring detection, phone and home-server hosting, recommendations
and journal, bank connections.

## Goals

1. The owner can take a backlog of uncategorized spending to mostly categorized
   in one sitting, deciding by merchant instead of by charge.
2. Transfers between the owner's own accounts stop counting as spending.
3. Every category with spending can get a budget in one step.
4. Splitting a charge is reachable from where the charge is shown.
5. The page works without instructions. The layout says what to do.

## Non-goals

- Charts, trends, predictions and recommendations (project 2 and 5). This
  project adds one line to the dashboard and nothing else there.
- Better recurring detection (project 3). Review shows what today's detector
  finds. The only change to the detector is the shared merchant key described
  under "One definition each".
- Making the app's header and navigation phone-ready (project 4).
- Categorizing income. Review deals with money going out.
- Rewriting the copy or layout of existing pages, and the "lower case
  everything" goal. Both are app-wide changes for another day.
- Categorizing inline in the Ledger (issue #27). It should later reuse the
  category picker built here.

## Anti-goals

These make the project a failure even if everything works.

- Real financial or personal data anywhere in code, tests, fixtures, commit
  messages or docs. See the data policy in `CLAUDE.md`.
- Two screens that disagree. Review, Budget and the dashboard's Review line must
  report the same spending and the same uncategorized amount for the same dates.
- A page that needs paragraphs of explanation to operate.
- One-off parts: a control built for one list that another list or page then
  needs a second version of.
- A decision that cannot be undone, or one that overwrites a category the owner
  picked by hand.

## Constraints from the owner

Stated by the owner, in their words where quoted.

- Naming and copy: "Simplify, simplify, simplify. If it's not obvious what to do
  based on the layout and minimal explanation it's going to lead to user
  fatigue. Keep things simple and holistic in terms of naming and layout."
- Code: "follow software design best principles, DRY code, shared elements of
  the app should be references instead of hard coded."
- Scope: "Don't make sweeping changes here." Existing pages change only where
  they share a part with Review.
- The owner approved a mockup with this layout: a line on the dashboard, a
  Review page with a meter, a tile per list, and lists of rows. The mockup is
  not in the repository. This document describes that layout and replaces the
  mockup's names and list order, so build from this document.
- Working through a list: the full list on a wide screen, one item at a time on
  a phone (option C in the mockup).
- If something is not intuitive in use, the owner will flag it and direct the
  change. Do not pre-empt that with extra explanation text or special cases.

## Names

One word per thing, and the same word everywhere it appears: navigation,
headings, buttons, URLs and identifiers in code.

| Thing | Name | Already used in Metis |
| --- | --- | --- |
| The page | Review (`/review`) | new |
| Its four lists | Transfers, Categories, Budgets, Recurring | Import, Budget |
| Accept what a row shows | Confirm | Reconcile |
| Reject a suggestion for good | Dismiss | Suggested recurring |
| Leave a row for later (phone view) | Skip | new |
| The measure | Categorized, as a percent | Budget's "Uncategorized" |

The mockup's "No category", "No budget", "Repeats, not tracked" and "Might not
be spending" are replaced by the four list names above.

Copy rules:

- A list is a heading, a count and a total. No description under it.
- A row is a name and one line of facts. No sentence.
- No tooltip is needed to operate anything.
- An empty list is not shown at all. When all four are empty the page says
  "Nothing to review."
- Review has no tagline or intro paragraph. Existing pages keep theirs for now.
- New copy follows the casing existing pages use today.

## The page

Top to bottom:

1. **Meter.** "Categorized 40%" with the amount still uncategorized.
2. **Tiles.** One per list: its name, its total, its count. A tile jumps to its
   list. A tile for an empty list is not shown.
3. **The lists**, always in this order: Transfers, Categories, Budgets,
   Recurring. The order is the order to work in. Each step makes the next one
   more accurate: remove what is not spending, name what is, budget the names,
   then track what repeats.
4. A quiet "Restore dismissed (N)" link when anything has been dismissed.

Review uses the date range in the header, the same one Dashboard, Ledger and
Budget share. What is listed and counted is what falls inside that range. A
decision applies to all of a merchant's history, inside the range or not.

### Row anatomy

Every row in every list is the same part:

`name · facts · one input · Confirm · Dismiss`

- The input arrives filled in when Metis has a suggestion.
- Confirm accepts the row as shown. Dismiss appears only where the row is
  Metis's own suggestion and could be wrong.
- A row that stands for several charges can be expanded to show them, using the
  expand control the Budget page already has.
- After Confirm or Dismiss the row is gone and the owner is at the same place in
  the same list. Settling 25 rows in a row must not involve scrolling back.

### Transfers

- **Listed:** money going out that is not marked as a transfer, where an equal
  amount arrived in another of the owner's accounts within the window Metis
  already uses to pair card payments. Charges are grouped by merchant. A group
  is listed when at least half of its charges are paired this way.
- **Facts:** how many, the total, and the account the money arrived in.
- **Confirm:** every charge in the group, past and future, is a transfer and
  stops counting as spending.
- **Dismiss:** it is spending. It is not suggested again, and it moves to
  Categories.

Anything else that is really a transfer, such as money sent to an account Metis
does not import, shows up under Categories. The category picker there offers
"Transfer" as a choice, with the same effect as Confirm here.

There is one answer to "is this a transfer": a single function, used by import
and by Review, that reads both the cards' existing payment patterns and the
decisions made here. The payment patterns stay where they are, on the Accounts
page, and Confirm does not write to them. Confirm also marks the charges already
stored, which the payment patterns alone never do.

Confirm marks the money going out. The matching arrival is also marked when it
landed in an account that is not a card, so it does not count as income. An
arrival on a card is left as the import marked it, so statements are untouched.

Marking a row as a transfer changes what counts as spending and income. It must
not change any account's balance or the cash forecast.

### Categories

- **Listed:** uncategorized spending, grouped by merchant, largest total first.
  A group that is listed under Transfers is not listed here.
- **Facts:** how many charges and the total.
- **Input:** a category picker. It is filled in when the same merchant already
  has categorized charges, with the category most of them have. Its choices are
  the owner's categories, then "Transfer", then "New category", which takes a
  name and creates the category as part of Confirm.
- **Confirm:** every uncategorized charge from that merchant gets the category,
  and so does every future one.
- **Expanded:** each charge with its own picker and a Split link. A category
  picked for one charge applies to that charge only. Split opens the existing
  split page and returns to Review afterwards.

Rules for what Confirm may touch:

- The charges changed are the charges the row counted, across all history.
- A category picked by hand for a single charge is never overwritten.
- A split charge counts each part in its own category. Only the part left over
  is uncategorized.

### Budgets

- **Listed:** categories with spending in the range and no budget, where "no
  budget" means what the Budget page already shows as "no budget".
- **Facts:** the category's typical monthly spending.
- **Input:** a monthly amount, filled in with a suggestion: the median of up to
  the last six complete months, rounded up to the nearest 10.
- **Confirm:** sets the category's monthly budget.
- **Dismiss:** this category has no budget on purpose. It is not listed again.

### Recurring

- **Listed:** what the existing detector suggests, the same suggestions the
  Budget page shows today under "Suggested Recurring". As today, the detector
  looks at all history regardless of the date range, and may suggest income.
- **Facts:** how often and how much.
- **Confirm:** adds it to recurring items as detected.
- **Expanded:** the fields the Budget page's suggestion card has today, for
  changing a value before Confirm.
- **Dismiss:** as today.

The suggestions move from Budget to Review. They are not shown in both places.
Budget keeps its "N suggested" chip, which becomes a link to this list.

### One list at a time

A charge or group appears in exactly one list. Transfers takes precedence over
Categories. A category appears under Budgets only once it exists and has
spending.

### Undo

Every Confirm and Dismiss can be undone. At minimum the message shown after the
action carries an Undo that reverses that one action completely: categories go
back to what they were, a transfer counts as spending again, and future imports
are no longer affected.

### Dashboard and navigation

- Navigation gets "Review" after "Dashboard", with the number of rows waiting.
  No number when there is nothing waiting.
- The dashboard gets one line: "Categorized 40%" and a link to Review, for the
  dashboard's own date range. It is not shown when nothing is waiting.

### Narrow screens

Below phone width the same page shows each list one row at a time, as a card,
with Confirm, Dismiss and Skip and a position such as "3 of 45". Skip moves on
and saves nothing. This is a different presentation of the same rows and the
same actions, not a second page or a second set of routes. Only Review's own
content has to work at that width in this project.

## One definition each

These are computed in one place and read by every screen that needs them.

- **Spending:** money going out that is not a transfer, less refunds. A refund
  is money arriving on a card that is not a payment. It reduces spending in its
  own category. Money arriving in any other account is income and never reduces
  spending.
- **A charge's category:** what Budget uses today. The category of the expected
  item the charge is matched to, otherwise the charge's own category, with split
  parts counted in the categories they were given.
- **Uncategorized:** spending with no category by that definition. For any date
  range, Review's uncategorized total equals the "Uncategorized" line on Budget.
- **Categorized percent:** spending that has a category, divided by all
  spending, for the date range.
- **Same merchant:** one grouping key, shared by Review and the recurring
  detector, so both agree on which charges are one merchant. It starts from the
  detector's key with one correction: today everything after a `*` or `_` is
  dropped, so every merchant billed through the same payment processor becomes
  one merchant ("PP *ACME COFFEE" and "PP *EXAMPLE BAKERY" both become "PP").
  The shared key keeps what follows the processor prefix, so those are two
  merchants. Suggestions dismissed under the old key stay dismissed.

Two things change on existing screens because of these definitions, and both
are corrections:

- Budget today nets all money in against all money out within a category, so an
  uncategorized deposit hides uncategorized spending. Budget moves to the
  definition of spending above. Its figures change wherever that netting was
  happening.
- The detector's suggestions can change where the old key had merged different
  merchants. Its thresholds and logic are otherwise untouched.

The dashboard's existing cash totals (money in and out of checking, with the
forecast) are a different measure and do not change in this project.

## What exists to build on

- `transaction_splits`, the split page and its routes. Budget totals already
  honor splits.
- Category rules: created per category, applied to history and to new imports,
  never overwriting a hand-picked category. They match on a piece of the
  description, while merchant grouping uses a normalized key. Review's "the
  charges changed are the charges counted" rule has to hold across that
  difference.
- The per-category monthly budget and its route.
- The recurring detector, its dismissed list and its restore.
- The transfer flag on imported charges, set at import, and the per-card payment
  pattern that sets it for payments to cards.
- The shared date range.
- Serving under a path (`METIS_BASE_PATH`). Every link, form and script URL in
  a template is written after `{{ base_path() }}`; a test fails on any that
  isn't. Redirects go through `redirect_with_message`, which keeps them on this
  site, and routes use plain paths.
- Shared template parts in `_statement_macros.html`, the category pill, the
  expand control, cards and stat chips in `static/styles.css`.

## Shared parts

Build each of these once and use it everywhere it appears in Review. Where the
same thing exists inline on another page today, move it to the shared part and
have that page use it too:

- the review row
- the category picker
- the category pill
- the recurring suggestion fields
- the meter

The category picker is one part whose "Transfer" and "New category" choices are
optional. Review uses it, and so do the recurring suggestion fields that move
there. The category selects on other pages stay as they are until issue #27.

The meter, tiles and phone card are not new designs. The meter is the bar the
Budget page draws, the tiles are the app's existing compact cards, and the phone
card is the existing card. If one of them needs something the existing part
lacks, extend the part.

Do not refactor parts of other pages that Review does not use.

New logic goes in its own module rather than into `services.py`, and calls the
existing functions for rules, budgets, splits and suggestions instead of
repeating them.

## Proof

Written test-first, on synthetic data only ("Acme Grocer", round amounts).

- Review's uncategorized total equals Budget's for the same dates, including
  with split and partly split charges.
- Confirming a merchant categorizes exactly the charges the row counted, leaves
  hand-picked categories alone, and a later import from that merchant arrives
  categorized.
- Confirming a transfer removes it from spending on Review and Budget, a later
  import of the same thing arrives as a transfer, and account balances and the
  forecast do not change.
- Nothing appears in two lists.
- Dismiss holds across reloads. Restore brings rows back.
- Undo returns totals, categories and future-import behavior to what they were.
- The categorized percent is right for none, some and all categorized, and for
  a range with no spending.
- The existing suite still passes.

## Left to the builder

- How a confirmed transfer or merchant category is stored and applied to future
  imports, within the rules above.
- How Undo is recorded.
- Whether actions reload the page or update it in place.
- Module, route, template and style names, following the names table.
- How the waiting count in the navigation is kept cheap to compute.
- The order to build in. Suggested: the shared definitions and the Categories
  list first, since that is what the owner most needs to try, then Transfers,
  Budgets, Recurring, the dashboard line, and the narrow-screen view last.

## Calls made in this design

Not decided by the owner. Each can be changed without disturbing the rest.

1. The names in the table, and Confirm / Dismiss / Skip as the only actions.
2. Lists in working order with Transfers first. The mockup had it last.
3. Transfers lists only charges paired with an arrival in another account.
   Everything else is reached through "Transfer" in the category picker.
4. Review follows the shared date range instead of always showing all history.
5. A category suggestion comes only from the same merchant's own categorized
   charges. Nothing is guessed from a merchant's name.
6. "New category" inside the picker.
7. The budget suggestion: median of the last six complete months, rounded up to
   the nearest 10.
8. Recurring suggestions move from Budget to Review.
9. Dismiss on Budgets.
10. Undo on the message after each action.
11. The narrow-screen view switches by width, with no setting.
12. Review has no tagline or intro paragraph.
13. Spending is money out less card refunds, and Budget moves to that
    definition. Budget's figures change where deposits were cancelling spending.
14. The merchant key keeps what follows a payment-processor prefix, for Review
    and the detector alike.
15. Confirming a transfer marks the money going out, and the arrival only when
    it landed in an account that is not a card.
16. The category picker is shared by Review and the recurring fields only. Other
    pages' category selects wait for issue #27.
