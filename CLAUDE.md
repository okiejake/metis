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

## Development cycle

**Release tag format:** `v0.0.0`

**Feature branches:** `[release-tag]/dev/feature-description`
- Branch off of `main`.
- Merged into `qa` rough-and-wild — informal, fast iteration.

**QA:** `qa` always has exactly **one `[release-tag]` in flight at a time**.
- For each fix during QA, write a `[release-tag]/qa/fix-description` branch.

**Release:** when the QA fixes are done, PR `qa` → `main` and **squash-and-merge**.
- PR **title** = the `[release-tag]`.
- PR **description** = a summary of the features and the QA commits.
- Keeping features and QA commits distinct helps us categorize the type of work: quick
  iteration vs. what needed feedback to get right.

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
