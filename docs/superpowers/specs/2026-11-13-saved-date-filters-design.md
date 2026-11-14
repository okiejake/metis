# Saved date filters

Extends the date range picker (`2026-11-13-date-range-picker-design.md`).

## Why

The owner reuses a handful of ranges. Saving one by name, exactly as set, makes it
one click to get back to.

## Goals

- A **save** button in the picker's left panel asks for a name and saves the range
  being edited exactly as set: grain, absolute or relative, start and end. A
  relative filter keeps sliding forward. Each saved filter records its created date.
- Saved filters are listed in the left panel, above grain. Clicking one loads it
  into the panel and preview; **Apply** still commits it, as today.
- Each row shows the name and created date (`2026-11-13`), and has delete.

## Non-goals

- A default filter (dropped by the owner: Apply already persists across sessions).
- Editing or renaming a saved filter. Delete and save again.
- Sharing filters between users.

## Approach

One sensible way: a `saved_ranges` table holding the same rule shape the picker
already saves (`rule_to_saved` JSON), read through `rule_from_saved`, so no new date
logic. Save and delete go through small JSON endpoints called with `fetch`, so the
popup stays open with the user's unapplied edits; a full-page form post would close
it and lose them.

## How it works

- **Table** `saved_ranges(id, user_id, name, rule, created_at)`; `rule` is the JSON
  rule. Deleting a user deletes their saved filters. Rows whose rule no longer
  parses are skipped when listed.
- **Endpoints**
  - `POST /window/saved` (form: `name`, `grain`, `mode`, `start`, `end`) → `201`
    JSON `{id, name, created, rule}`. Name is trimmed, 1–60 characters, unique per
    user (case-insensitive); the rule is checked by `rule_from_form` and the
    100-year span limit. Failures → `400` JSON `{error}` with a lowercase message.
  - `POST /window/saved/{id}/delete` → `204`; another user's id or a missing id →
    `404`.
- **Page context**: `range_saved`, a list of `{id, name, created, rule}`, newest
  first, on every signed-in page (with the other `range_*` keys), rendered as
  `data-saved` on the dialog.
- **Panel**
  - "saved" list above grain; hidden when empty. Each row: the name as a button
    (loads the rule; `aria-pressed` when it matches the rule being edited), the
    created date, and a delete icon button (`aria-label` "delete <name>", with a
    confirm).
  - **save** button at the foot of the panel: `prompt("name this filter")`;
    cancel or blank does nothing; an error from the server shows in an `alert`.
    On success the new row appears at the top, pressed.
- Copy is lowercase. Style follows `docs/design/neumorphism.md` (rows and buttons
  are `neu-press`).

## Testing

All synthetic data.
- Services: save, list newest first, delete only your own, skip unreadable rows,
  user deletion removes them.
- Endpoints: valid save returns the stored rule exactly (absolute and relative);
  blank, too long and duplicate names, a bad rule and an over-long span are
  rejected; delete of someone else's id is `404`.
- Context: `range_saved` on every signed-in page; `data-saved` on the dialog.
- JS: `sameRule` (pure) matches equal rules and tells relative from absolute.
- Browser: save with a name, load it, apply, delete; Cancel on the prompt saves
  nothing.
