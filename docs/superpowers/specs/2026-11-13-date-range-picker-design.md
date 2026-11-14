# Date range picker

Replaces the header's date range row and presets with one picker in the header row
on every signed-in page. Covers #72, as redirected by the owner on 2026-11-13.

## Why

The saved date range drives Dashboard, Ledger, Budget and Review, but today it is a
second header row that only some pages show, picked through presets that don't match
how the owner thinks about ranges. The owner wants to pick a range by its natural
unit (days, months, years, decades), either as fixed dates or as a rule that slides
forward with the calendar ("3 months back to 6 months ahead"), and to see the whole
range at a glance before applying it.

## Goals

- One range picker in the header row, between the nav and the hide-amounts button,
  on every signed-in page.
- Pick a range by **grain** (day, month, year, decade) in **absolute** or
  **relative** mode.
- Relative ranges **slide forward**: Metis saves the rule, not two dates, and works
  out the dates from today on every page load.
- A preview of every month in the range that fits the popup without scrolling.

## Non-goals

- Grain does **not** change how pages group their numbers. It is only how the range
  is picked. A page-level grouping setting may come later, separately.
- No presets. This Month, This Year, YTD, Rolling 12M, Rolling 6M and Next 90D all go.
- The preview is never clickable. All input happens in the panel.

## Anti-goals

- A link carrying `?start=&end=` silently replacing a saved relative rule.
- A preview that scrolls, or that rearranges months so the eye loses its place.

## Approach

Considered:

1. **Save the rule (chosen).** One saved setting holds `grain`, `mode`, `start`,
   `end`. One shared function turns it into dates from today. Relative ranges slide
   forward for free, and every page reads the same function.
2. **Save two dates and recompute relative ranges in the browser.** Pages loaded
   without the picker (or a new day without a click) would show stale dates. Fails
   the "slides forward" requirement.
3. **Keep presets and add a custom range.** The owner removed presets outright.

## How it works

### The saved range

One setting per user, under the existing `forecast_window` key, as JSON:

```json
{"grain": "month", "mode": "relative", "start": -3, "end": 6}
```

| grain  | absolute `start`/`end`        | relative `start`/`end` (integers, from now) |
|--------|-------------------------------|---------------------------------------------|
| day    | ISO date, `2026-11-10`        | days from today                             |
| month  | `2026-11`                     | months from this month                      |
| year   | `2026`                        | years from this year                        |
| decade | first year, `2020` (= 2020s)  | decades from this decade                    |

Resolving to dates (start of the first unit to end of the last):

- day: the dates themselves.
- month: the 1st of the start month to the last day of the end month.
- year: Jan 1 of the start year to Dec 31 of the end year.
- decade: Jan 1 of the start decade's first year to Dec 31 of the end decade's last
  year.
- Relative `0` means the current unit: `0 → 0` months is this month, `0 → 0` years
  is this year.

Example: rule `month, relative, −3 → +6` resolves to Aug 1, 2026 – May 31, 2027 in
November 2026, and Sep 1, 2026 – Jun 30, 2027 in December 2026.

**One function** (in `services.py`) resolves a saved rule to `(start, end)` given
today. Dashboard, Ledger, Budget, Review and the header all go through it. It takes
`today` as an argument so tests can fix the date.

**Upgrade:** a saved value in the old `{"start", "end"}` shape (and the older
per-key fallback) reads as `day, absolute` with those dates, so nobody's view
changes. It is rewritten in the new shape the next time the user applies a range.

**Default** when nothing is saved: `month, relative, −3 → +3`. *(Builder's call; the
old default was today −90 days to +90 days, which this approximates in whole months.)*

### Links with `?start=&end=`

Dashboard, Ledger, Budget and Review still accept `?start=&end=`. Those dates show
on that page view only and are **not saved**, so they never replace the saved rule.
A page reached through a dated link stays on the link's dates until the user applies
a range from the picker; then that page shows the applied range too (confirmed by
the owner).
Today `resolve_forecast_window` saves whatever it is given; that stops. The manual
item edit round-trip (`routes/manual.py`, `ledger.html`, `manual_edit.html`) keeps
passing `start`/`end` and keeps working as a one-off view.

### Saving: `POST /window`

The popup posts `grain`, `mode`, `start`, `end` and `next` (the current path and
query) to `/window`. The route validates, saves, and redirects to `next` through
`resolve_safe_redirect_target`. Invalid input redirects back with an error message
and leaves the saved rule unchanged. Validation:

- `grain` and `mode` are one of the listed values.
- Absolute values parse for their grain; relative values are integers.
- Start is not after end (compared after resolving).
- Relative offsets are bounded to a sane span (*builder's call*: ±3650 days,
  ±240 months, ±50 years, ±10 decades).

### Shared header context

`template_context` puts the resolved range and the saved rule in every signed-in
page's context, so routes don't pass them. Routes that show a `?start=&end=`
override pass it and it wins. The `show_forecast_window_form` and
`window_form_action` flags are removed from `routes/overview.py`,
`routes/expected.py` and `routes/review.py`.

### Header

- Collapsed: a button showing the range in effect on this page, e.g.
  `Jan 1, 2026 – Dec 31, 2026`, in the header row after the nav. Clicking opens the
  popup.
- On narrow screens it sits with `metis` and the action buttons on the top line; the
  nav keeps its own scrolling line (as built in #71).

### Popup

A modal `<dialog>` (the existing calendar dialog is replaced). *(Builder's call: the
popup is too large for the `<details>` dropdown pattern #72 suggested.)*

**Top bar:** funnel button (shows/hides the panel; `aria-label` "Show filters" /
"Hide filters", `aria-expanded`), the range being edited, **Cancel**, **Apply**.
Escape and Cancel close without saving. Apply posts to `/window`. Nothing saves
until Apply.

**Left panel:**

- **Grain:** segmented control, Day · Month · Year · Decade. Month is the default
  for a new rule.
- **Mode:** radio buttons, Absolute · Relative.
- **Start** and **End**, one control each, by grain and mode:

| grain  | absolute                                          | relative                       |
|--------|---------------------------------------------------|--------------------------------|
| day    | standard date field (`<input type="date">`)       | −/+ stepper, days from today   |
| month  | dropdown of months, grouped by year (`<optgroup>`) | −/+ stepper, months            |
| year   | dropdown of years                                 | −/+ stepper, years             |
| decade | dropdown of decades (2020s…)                      | −/+ stepper, decades           |

- Each relative control is a small −/+ pair around a typed number field (accepts `3`, `+3`, `-3`, `−3`; Enter sets it without applying; anything else reverts), with what the offset lands on beside it (`−3  Aug 2026`). A typed value past the other end moves that end to meet it.
- Steppers keep start ≤ end: start's + stops at end, end's − stops at start.
- Dropdown span (*builder's call*): from the earlier of the user's first
  transaction year and ten years ago, to ten years ahead.
- Switching grain or mode converts the current range to the nearest equivalent in
  the new setting (e.g. Aug 10 – May 3 at day grain becomes Aug – May at month
  grain), so the preview doesn't jump. *(Builder's call.)*
- Panel visibility is remembered per browser (`localStorage`, wrapped in try/catch);
  shown by default.

**Preview:** shows every month the range touches. A month counts if any day of the
range falls in it (Nov 10 – Jan 20 counts as 3). The view depends **only on that
count**, never on the grain:

| months | view |
|--------|------|
| 1–3    | **Full calendars**, one column: month name, weekday letters, day numbers. Every day in range is filled the same colour; today is filled orange, as in the other views. The month before and the month after show at the top and bottom, faded out with a gradient mask, like a wall calendar. Cells have a maximum size, so short ranges show more of the faded neighbours rather than stretching. |
| 4–24   | **Mini months**, 3 columns, one row per quarter so **January is always in column 1**. Each month is a small grid of day squares in week rows, no numbers, with its short name and year. Months in the first and last rows that fall outside the range are dimmed. The quarter before and after show at top and bottom, faded out. |
| 25+    | **Year rows**: one row per year, a labelled block per month (J F M …), January in column 1. No days. |

- Today is marked (orange) in every view.
- The preview sizes itself from the space available (JS reads the container size
  on open and on resize), so it never scrolls.
- Weeks start on Sunday, as today.

Reference mockups: `.superpowers/brainstorm/` (local, not committed), screens
`preview-stages-v2` and `preview-stages-v3`.

### Code layout

- Picker script moves out of `templates/base.html` into `static/date-range.js`
  (loaded with a version query like `styles.css`).
- Styles in `static/styles.css`, one set of rules, following
  `docs/design/neumorphism.md` ("soft frame, flat data"): the header button, the
  dialog, the panel's controls, the segmented control and the steppers are
  neumorphic; the preview's day cells and month blocks are flat. The rest of the
  app is not restyled here.
- The old preset code (`rangeForPreset`, `updatePresetState`, the preset buttons),
  `calendar-nav`, the Back/Forward buttons and the `header-window-form` row are
  removed.

## Testing

All synthetic data.

- Resolver: each grain × mode resolves correctly for a fixed `today`; a relative
  rule resolves differently a month later (slides forward); month-end and leap-year
  edges; decade boundaries.
- Upgrade: an old `{"start","end"}` value reads as day/absolute with the same dates.
- `?start=&end=` shows that range and does not change the saved rule.
- `POST /window`: saves a valid rule and redirects to `next`; rejects bad grain,
  mode, values, start after end, and out-of-bounds offsets; ignores an off-site
  `next`.
- Every signed-in page renders the picker; no route passes the old flags.
- Inline scripts and `static/date-range.js` parse (`node --check`).
- In the browser: the three preview views at 3, 12 and 24 months, and year rows at
  120 months, fit without scrolling at desktop and phone widths.

## Left to the builder

- Exact sizing math for the preview views and the fade height.
- The stepper and dropdown markup, within the shared-parts rule.
- Phone layout of the popup (*builder's call*: full-width dialog, panel stacked above
  the preview, preview fills the remaining height).
