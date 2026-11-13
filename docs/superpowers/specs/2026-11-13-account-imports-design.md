# Accounts and imports, unified

Status: approved in conversation 2026-11-13. The owner reviews it by testing the
deployed build, and changes come from that testing.

Closes most of #23 (fold imports into accounts) and #22 (import templates).

## Why

Metis currently guesses. A checking export uploaded by a brand-new user turned
into an account Metis created by itself, typed by a guess, with its rows under
"Unlinked imported accounts", and no step where the owner could see or check how
the columns were read. The owner wants **no app-managed CSV formats**: every
template is made, named and checked by the user, and every import goes into an
account the user created.

## Goals

- One guided flow to add an account: name, type, import method, template.
- Templates the user makes by mapping a sample of their own file, with a live
  preview of how Metis will record the rows before anything is saved.
- An **Import** button on each account, opening a pop-up: template (the
  account's default preselected) and file.
- Review flags accounts that look stale and prompts an import.

## Non-goals

- Plaid or any live connection. Its card is shown, greyed, "Coming soon".
- Sharing templates with `metis_hq` (#22's blocked item).
- Editing a saved template's mapping. Delete and re-create for now.

## Anti-goals

- Metis inferring an account, account type or format from a file's shape.
- Explanation text standing in for a clear layout (see CLAUDE.md design
  principles).

## Decided by the owner

1. **Add account** flow, in order:
   1. Name the account.
   2. Choose its type (checking or credit card; card cycle days as today).
   3. Choose an import method from two cards: **Manual Import** and **Plaid**
      (greyed, "Coming soon").
   4. Manual Import shows a card per existing template plus **Create new**.
   5. Create new asks for a file. After upload: a sample of the file with its
      column names, and a form mapping columns to Metis's transaction fields.
2. **Remove all built-in CSV recognition.** The three built-in formats go, and so
   does creating accounts from imported labels.
3. **No Import page.** Each account on Accounts has an **Import** button that
   opens a pop-up: choose a template, upload a file.
4. **Templates** can be set as an account's **default template**. A template is
   saved and listed with its name and created date, formatted `2026-11-13`.
5. **Stale accounts in Review.** Baseline: the average gap in days between an
   account's transactions. If the days since its last imported transaction are
   more than 20% above that baseline, the account is flagged as possibly stale,
   with a prompt to import.
6. Accepted from the playback: a template stands on its own and can be shared by
   several accounts; existing accounts keep their transactions; #22's "ship
   built-in templates" is dropped.

## Calls made by the builder (the owner can overturn any)

- **Mapping form fields:** date column; optional second date column used when the
  first is blank (card exports that carry a clearing date); date format (read
  from the sample, correctable); description column; optional merchant column.
- **Amounts**, one of three layouts: one signed column (with a "flip signs"
  switch), separate money-in and money-out columns, or one amount column plus a
  type column whose named value means money in.
- **Live preview** under the form, rendered by the same parser the import uses,
  so what you see is what gets recorded.
- **Column suggestions:** the form preselects a column whose name contains the
  field's name (a column called "Posting Date" for the date). The preview shows
  the result; nothing is saved until the owner saves.
- **A file missing a template's columns** stops the import and names the missing
  columns.
- **Card payments:** on a card, money in whose description or type says PAYMENT
  or THANK YOU is a transfer; other money in is a refund. On checking, money out
  matching a card's payment pattern is a transfer, as today.
- **Merchant without a merchant column:** the payee from an `ACH … TYPE:`
  description, else the description.
- **Templates section on Accounts:** name, created date, accounts using it as
  default, Delete. A "New template" button there runs the upload and mapping
  steps without an account.
- **Saving a template from the add-account flow** sets it as that account's
  default and imports the uploaded file into the account.
- **Uploaded file between steps** is held in the database for the mapping step,
  then deleted when the template is saved, or after a day.
- **Existing users:** on a database's first start with this change, each user
  who already has imported rows gets the old built-in layouts as templates they
  own: "Checking and Visa export" (those two exports shared one layout) and
  "Card export". Each existing account's default is set from the rows it holds.
  This runs once per database, so later users never get them and a deleted
  template stays deleted.
- **Rows with no account** (left over from label guessing) are listed on Accounts
  with an account picker, only while any exist.
- **Stale rule details:** an account needs at least 5 transactions for a
  baseline; one with no transactions is listed as "No imports yet". Dismiss hides
  the flag until the account's next import. The list is called **Imports** and
  comes first in Review.
- **The Imported Transactions table** on the old Import page is not rebuilt; the
  Ledger already lists every transaction.

## Constraints

- Public repo: synthetic test data only, no institution names.
- Shared parts, not copies: the import pop-up is one part used by Accounts and
  Review.
- Existing imported rows, their dedup keys and Review decisions keep working.
