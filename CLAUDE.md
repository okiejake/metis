# Metis

## What Metis is

Metis is a community-driven, open-source, easily customizable personal finance app. Its
purpose is to improve financial literacy and budgeting, and — in a future state — to
surface suggestions for improving financial moves.

Its core objective is to give you a **single lens into your entire financial picture**:

- Checking-account cash flow
- Credit card statements — and **predicting future credit card statements** from budgets
  and predictive analysis
- Pulling in **live data** (e.g. from your Robinhood account)
- **Auditing** your finances: finding stray transactions and helping you categorize, tag,
  and associate them
- Surfacing **subscriptions you didn't know you had**, and calling out transactions that
  aren't yet reconciled or that you may not recognize

As a user works with Metis, things become **more connected across accounts**, forecasting
becomes **more accurate**, and suggestions on what to do or plan for come into play. For
example:

> "It's predicted that you will spend X amount on Y this year. Making a small change to
> this budget, or considering a different Y, may help your budget."

> "You're forecasted to have X amount above your $5,000 cash buffer in your checking
> account. Consider additional investments to help keep your money growing."

## Data policy — read this first

**Never** put real financial or personal data anywhere in this repository. This is a
public repo, and the app is built around one of the most sensitive datasets a person
has.

This applies to **committed code, commit messages, code comments, PR titles and
descriptions, issues, and documentation** — everywhere, without exception:

- No real transaction amounts, balances, or dollar figures taken from actual data.
- No merchant, vendor, payee, employer, bank, or account names from actual data.
- No account numbers, routing numbers, card numbers, or transaction identifiers.
- No names, addresses, emails, phone numbers, or any other personally identifiable
  information.
- No real CSV exports, database files, or fixtures derived from them.

**When describing verification or debugging**, say what was proven, not what the data
said. Write "splitting a portion off an expense moved exactly that amount between
categories with the total unchanged" — never the actual figures, and never the vendor
whose bill it was.

**Test data must be synthetic.** If an example is needed, invent it: round numbers,
obviously fake names ("Acme Utilities", "Example Bank").

Real data lives only in the local, gitignored `finance.duckdb`. If real data does reach
a commit or a published description, treat it as an incident: scrub it before pushing
where possible, and raise it immediately if it is already public.

## Development cycle

**Release tag format:** `v0.0.0`

**Feature branches:** `type/short-description` (e.g. `feat/import-templates`,
`fix/ledger-double-count`, `chore/repo-policy`).
- Branch off of `main`.
- PR straight into `main` — there is no `qa` branch.
- Squash-and-merge, so `main` keeps one clean commit per change.

**Releases:** cut a **GitHub release** off `main`, tagged `v0.0.0`.
- The tag is created from `main` at the point being released.
- Release notes summarize the changes since the previous tag.
- Releasing is a separate act from merging: `main` is always shippable, and a release
  marks a point worth naming.

## Long-term goals to build toward

Mark these off as we complete them.

- [ ] **Simple yet customizable UI, DRY code.** The UI is simple yet customizable and the
      code is DRY — elements that can be shared are shared instead of duplicated.

- [ ] **Import templates as a first-class system.** The app ships with built-in import
      templates, but users can still add their own. A user can also choose to **share a
      template with `metis_hq`** to have it added to the app. `metis_hq` support has to wait
      until we work through security, hosting, and marketing — but we can build out the
      backend and UI to support better template organization now.

- [ ] **Fold imports into accounts.** Flow for adding a new account:
      1. Name the account.
      2. Select an import method (default to CSV). Offer several others plus a search
         option for standard finance formats, and — soon to come — Secure API integration
         (maybe Plaid?). Add images to make this selection area beautiful and really sell
         this part of the app.
      3. If the user selects CSV: create a new template or select an existing CSV template.
         "Create new" shows the upload-CSV flow; once submitted, a column-matching flow
         shows which columns are required and which are optional to map to the built-in
         Metis properties.
         - Open question: do we need different properties for different account types?

- [ ] **Lower case everything.**
