# Date Range Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the header's date range row and presets with one picker in the header row on every signed-in page: grain and mode panel, relative ranges that slide forward, and a non-scrolling preview of every month in range.

**Architecture:** A pure module `date_range.py` defines the saved rule and resolves it to dates for a given `today`. `services.py` loads and saves the rule; `web.render` puts the rule and the range in effect into every page's context. A new `POST /window` route saves a rule. The popup is a `<dialog>` in `base.html`, driven by `static/date-range.js`, whose pure functions mirror the Python resolver and are pinned to the same table of cases.

**Tech Stack:** FastAPI, Jinja2, DuckDB, vanilla JS (no build step), pytest, Node (`node` already used by tests for `--check`).

**Spec:** `docs/superpowers/specs/2026-11-13-date-range-picker-design.md`. Visual style: `docs/design/neumorphism.md`.

## Global Constraints

- All test data is synthetic (CLAUDE.md data policy). No real amounts, merchants or names anywhere.
- Grains: `day`, `month`, `year`, `decade`. Modes: `absolute`, `relative`. Default for a new rule: `month, relative, −3 → +3`.
- Saved setting key stays `forecast_window`; new shape `{"grain","mode","start","end"}`.
- Absolute values: day `2026-11-10`, month `2026-11`, year `2026`, decade first year `2020`.
- Relative bounds: ±3650 days, ±240 months, ±50 years, ±10 decades.
- `?start=&end=` shows on that view only and is **never saved**.
- Preview view by months touched: 1–3 full, 4–24 mini (3 columns, January in column 1), 25+ year rows. Grain never decides it.
- Weeks start on Sunday. Preview is never clickable.
- No presets anywhere. Remove `show_forecast_window_form` and `window_form_action` everywhere.
- Style: soft frame, flat data. Picker controls neumorphic; preview cells flat. Focus outline `--neu-accent`; no motion under `prefers-reduced-motion`. Icon-only buttons have `aria-label`.
- Plan decision: the resolver lives in a new `date_range.py` (pattern of `merchants.py`), not inside `services.py`, which is already over 4000 lines. `services.py` keeps the load/save wrappers.
- Plan decision: the JS mirrors the Python resolver because the popup must update live; both are pinned to `tests/fixtures/date_range_cases.json` so they can't drift. The browser resolves with the server's `today` (`data-today`), never the browser clock.

## Review Focus

1. **Applying a range on a page opened from a `?start=&end=` link** must land on the new range, not the old override. `next` drops `start`, `end`, `msg`, `err`, `undo`. Test in Task 3.
2. **A corrupt or unknown saved setting** (bad JSON, unknown grain, wrong types) must not break any page; it reads as the default rule. Test in Task 2.
3. **Server and browser disagreeing on today** near midnight or across time zones: the popup resolves with the server's date. Test in Task 4 (page carries `data-today` equal to the server's date) and Task 5 (JS resolves from a passed `today`).
4. **An absurdly long absolute range** (a day range from 1900 to 2100) must be rejected, not drawn as hundreds of year rows. Limit: resolved span at most 100 years. Test in Task 3.
5. **A decade value that isn't a decade start** (`2025`) from a tampered form is rejected. Test in Task 1.

---

### Task 1: The range rule and its resolver (`date_range.py`)

**Files:**
- Create: `date_range.py`
- Create: `tests/fixtures/date_range_cases.json`
- Test: `tests/test_date_range.py`

**Interfaces:**
- Produces:
  - `GRAINS = ("day", "month", "year", "decade")`, `MODES = ("absolute", "relative")`
  - `RELATIVE_LIMITS = {"day": 3650, "month": 240, "year": 50, "decade": 10}`
  - `MAX_SPAN_DAYS = 36525`
  - `@dataclass(frozen=True) class RangeRule: grain: str; mode: str; start: str | int; end: str | int` (absolute → `str`, relative → `int`)
  - `DEFAULT_RULE = RangeRule("month", "relative", -3, 3)`
  - `rule_from_form(grain: str, mode: str, start: str, end: str) -> RangeRule`, raises `ValueError(message)` with a user-facing message
  - `rule_from_saved(value: object) -> RangeRule | None`: new shape, or legacy `{"start","end"}` ISO dates → `day/absolute`; anything malformed → `None`
  - `rule_to_saved(rule: RangeRule) -> dict`
  - `resolve_rule(rule: RangeRule, today: date) -> tuple[date, date]`

- [ ] **Step 1: Write the shared cases file** `tests/fixtures/date_range_cases.json`, a list of `{"rule": {...}, "today": "...", "start": "...", "end": "..."}`:

| rule (grain, mode, start, end) | today | start | end |
|---|---|---|---|
| month, relative, -3, 6 | 2026-11-13 | 2026-08-01 | 2027-05-31 |
| month, relative, -3, 6 | 2026-12-02 | 2026-09-01 | 2027-06-30 |
| month, relative, 0, 0 | 2026-11-13 | 2026-11-01 | 2026-11-30 |
| month, relative, -1, 1 | 2027-01-31 | 2026-12-01 | 2027-02-28 |
| month, absolute, "2024-02", "2024-02" | 2026-11-13 | 2024-02-01 | 2024-02-29 |
| year, relative, 0, 0 | 2026-11-13 | 2026-01-01 | 2026-12-31 |
| year, relative, -1, 1 | 2026-11-13 | 2025-01-01 | 2027-12-31 |
| year, absolute, "2024", "2026" | 2026-11-13 | 2024-01-01 | 2026-12-31 |
| decade, relative, 0, 0 | 2026-11-13 | 2020-01-01 | 2029-12-31 |
| decade, relative, -1, 0 | 2026-11-13 | 2010-01-01 | 2029-12-31 |
| decade, absolute, "2020", "2030" | 2026-11-13 | 2020-01-01 | 2039-12-31 |
| day, relative, -90, 90 | 2026-11-13 | 2026-08-15 | 2027-02-11 |
| day, absolute, "2026-11-10", "2027-01-20" | 2026-11-13 | 2026-11-10 | 2027-01-20 |

- [ ] **Step 2: Write the failing tests** in `tests/test_date_range.py`:

```python
CASES = json.loads((Path(__file__).parent / "fixtures" / "date_range_cases.json").read_text())

@pytest.mark.parametrize("case", CASES)
def test_resolve_rule_matches_the_shared_cases(case):
    rule = rule_from_saved(case["rule"])
    assert resolve_rule(rule, date.fromisoformat(case["today"])) == (
        date.fromisoformat(case["start"]), date.fromisoformat(case["end"]))

def test_a_relative_rule_slides_forward_a_month_later():
    rule = RangeRule("month", "relative", -3, 6)
    assert resolve_rule(rule, date(2026, 12, 2))[0] == date(2026, 9, 1)

def test_legacy_saved_dates_read_as_day_absolute():
    assert rule_from_saved({"start": "2026-02-01", "end": "2026-03-31"}) == RangeRule("day", "absolute", "2026-02-01", "2026-03-31")

@pytest.mark.parametrize("value", [None, "x", [], {"grain": "week", "mode": "relative", "start": 0, "end": 0},
                                   {"grain": "month", "mode": "relative", "start": "a", "end": 0}, {"start": "nope"}])
def test_malformed_saved_values_read_as_none(value):
    assert rule_from_saved(value) is None

def test_saved_round_trip():
    rule = RangeRule("year", "absolute", "2024", "2026")
    assert rule_from_saved(rule_to_saved(rule)) == rule

@pytest.mark.parametrize("args, message", [
    (("week", "relative", "0", "0"), "grain"),
    (("month", "sideways", "0", "0"), "mode"),
    (("month", "relative", "x", "0"), "number"),
    (("month", "relative", "-241", "0"), "too far"),
    (("month", "absolute", "2026-13", "2026-12"), "month"),
    (("decade", "absolute", "2025", "2030"), "decade"),   # Review Focus 5
    (("month", "relative", "2", "1"), "after"),
    (("day", "absolute", "2027-01-20", "2026-11-10"), "after"),
])
def test_rule_from_form_rejects(args, message):
    with pytest.raises(ValueError, match=message):
        rule_from_form(*args)

def test_rule_from_form_accepts_and_types_values():
    assert rule_from_form("month", "relative", "-3", "6") == RangeRule("month", "relative", -3, 6)
    assert rule_from_form("decade", "absolute", "2020", "2030") == RangeRule("decade", "absolute", "2020", "2030")
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_date_range.py -q`
Expected: collection error, `No module named 'date_range'`.

- [ ] **Step 4: Implement `date_range.py`** with the interfaces above. `rule_from_form` checks start ≤ end by resolving against `date.today()` (the order of two values doesn't depend on today for any grain). Month clamping uses `calendar.monthrange`. Error messages contain the matched words above ("grain", "mode", "number", "too far", "month", "decade", "after").

- [ ] **Step 5: Run to see them pass**

Run: `.venv/bin/python -m pytest tests/test_date_range.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add date_range.py tests/fixtures/date_range_cases.json tests/test_date_range.py
git commit -m "Add the date range rule and its resolver"
```

---

### Task 2: Load, save and resolve the range for pages

**Files:**
- Modify: `services.py:1346-1393` (`load_saved_forecast_window`, `save_forecast_window`, `resolve_forecast_window`)
- Modify: `web.py` (`render`)
- Modify: `routes/overview.py`, `routes/expected.py`, `routes/review.py` (drop the two flags)
- Modify: `tests/test_shared_date_window.py`
- Test: `tests/test_range_context.py`

**Interfaces:**
- Consumes: `RangeRule`, `DEFAULT_RULE`, `rule_from_saved`, `rule_to_saved`, `resolve_rule` (Task 1)
- Produces:
  - `load_range_rule(user_id: int) -> RangeRule` (saved, else legacy keys, else `DEFAULT_RULE`; never raises on bad data)
  - `save_range_rule(user_id: int, rule: RangeRule) -> None`
  - `save_forecast_window(user_id, start: date, end: date) -> None` kept as a wrapper that saves `day/absolute` (tests use it)
  - `resolve_forecast_window(user_id, start="", end="", today: date | None = None) -> tuple[date, date, bool]`: both params given and valid → that range, **not saved**; otherwise the saved rule resolved for `today`. Third value is the existing invalid-filter flag.
  - `range_first_year(user_id: int, today: date) -> int`: `min(year of earliest imported_transactions.tx_date, today.year - 10)`
  - `render` adds to every signed-in page's context: `range_rule` (dict from `rule_to_saved`), `range_start` / `range_end` (ISO; the route's `start`/`end` when it passed them, else the saved rule resolved for today), `range_today` (ISO server date), `range_first_year` (int)
  - `load_saved_forecast_window` is removed.

- [ ] **Step 1: Rewrite `tests/test_shared_date_window.py`** so the carry-over test saves through `save_range_rule` instead of a `?start=&end=` visit, and add:

```python
def test_a_start_end_link_shows_that_range_without_saving_it(client, db, user_id):
    db.save_range_rule(user_id, RangeRule("month", "relative", -3, 3))
    response = client.get("/ledger?start=2026-02-01&end=2026-03-31")
    assert (response.context["start"], response.context["end"]) == ("2026-02-01", "2026-03-31")
    assert db.load_range_rule(user_id) == RangeRule("month", "relative", -3, 3)
```

- [ ] **Step 2: Write `tests/test_range_context.py`:**

```python
@pytest.mark.parametrize("path", ["/dashboard", "/review", "/ledger", "/statements", "/budget", "/accounts", "/settings"])
def test_every_signed_in_page_gets_the_range(client, path):
    ctx = client.get(path).context
    assert {"range_rule", "range_start", "range_end", "range_today", "range_first_year"} <= ctx.keys()
    assert "show_forecast_window_form" not in ctx and "window_form_action" not in ctx

def test_the_header_shows_a_link_override(client):
    ctx = client.get("/dashboard?start=2026-02-01&end=2026-03-31").context
    assert (ctx["range_start"], ctx["range_end"]) == ("2026-02-01", "2026-03-31")

@pytest.mark.parametrize("raw", ["{not json", '{"grain":"week","mode":"relative","start":0,"end":0}', '"just a string"'])
def test_a_corrupt_saved_range_falls_back_to_the_default(client, db, user_id, raw):   # Review Focus 2
    db.set_setting_value(user_id, db.FORECAST_WINDOW_SETTINGS_KEY, raw)
    assert client.get("/dashboard").status_code == 200
    assert db.load_range_rule(user_id) == DEFAULT_RULE

def test_first_year_reaches_back_to_the_earliest_transaction(db, user_id, accounts):
    add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2009, 6, 1), "ACME GROCER", -10.0)
    assert db.range_first_year(user_id, date(2026, 11, 13)) == 2009

def test_first_year_defaults_to_ten_years_back(db, user_id):
    assert db.range_first_year(user_id, date(2026, 11, 13)) == 2016
```

(`client` fixture as in `tests/test_shared_date_window.py`.)

- [ ] **Step 3: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_shared_date_window.py tests/test_range_context.py -q`
Expected: FAIL (`load_range_rule` missing; flags still present).

- [ ] **Step 4: Implement** the services functions and the `render` additions. `render` computes the range keys only when a user is signed in, as it does for `review_waiting`. Remove `show_forecast_window_form=True` and `window_form_action=...` from the four routes. Leave the `{% if show_forecast_window_form %}` blocks in `base.html` for Task 4 (they simply stop rendering).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. Any other test that relied on `?start=&end=` saving is updated to save through `save_range_rule`.

- [ ] **Step 6: Commit**

```bash
git add services.py web.py routes/overview.py routes/expected.py routes/review.py tests/test_shared_date_window.py tests/test_range_context.py
git commit -m "Resolve the saved range rule for every page; links no longer save"
```

---

### Task 3: `POST /window`

**Files:**
- Create: `routes/window.py`
- Modify: `app.py` (include the router next to the others)
- Test: `tests/test_window_route.py`

**Interfaces:**
- Consumes: `rule_from_form`, `resolve_rule`, `MAX_SPAN_DAYS` (Task 1); `save_range_rule`, `load_range_rule` (Task 2); `resolve_safe_redirect_target`, `redirect_with_message` (services)
- Produces:
  - `POST /window` with form fields `grain`, `mode`, `start`, `end`, `next`. Success: save, 303 to the cleaned `next`. Failure: `redirect_with_message(cleaned_next, message, is_error=True)`, rule unchanged.
  - `clean_window_next(target: str) -> str`: `resolve_safe_redirect_target(target)`, then drop query keys `start`, `end`, `msg`, `err`, `undo`, keeping the rest and any `#fragment`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_apply_saves_the_rule_and_returns(client, db, user_id):
    r = client.post("/window", data={"grain": "year", "mode": "relative", "start": "-1", "end": "0", "next": "/budget"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/budget")
    assert db.load_range_rule(user_id) == RangeRule("year", "relative", -1, 0)

def test_apply_from_an_override_link_lands_on_the_new_range(client):   # Review Focus 1
    r = client.post("/window", data={"grain": "month", "mode": "relative", "start": "0", "end": "0",
                                     "next": "/ledger?start=2026-02-01&end=2026-03-31&msg=x&err=1&tab=a"}, follow_redirects=False)
    assert r.headers["location"].endswith("/ledger?tab=a")

def test_off_site_next_goes_to_the_dashboard(client):
    r = client.post("/window", data={"grain": "month", "mode": "relative", "start": "0", "end": "0", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"].endswith("/dashboard")

@pytest.mark.parametrize("data", [
    {"grain": "week", "mode": "relative", "start": "0", "end": "0"},
    {"grain": "month", "mode": "relative", "start": "3", "end": "1"},
    {"grain": "day", "mode": "absolute", "start": "1900-01-01", "end": "2100-12-31"},   # Review Focus 4
])
def test_bad_input_leaves_the_rule_and_shows_an_error(client, db, user_id, data):
    before = db.load_range_rule(user_id)
    r = client.post("/window", data={**data, "next": "/budget"}, follow_redirects=False)
    assert "err=1" in r.headers["location"]
    assert db.load_range_rule(user_id) == before
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_window_route.py -q`
Expected: 404s / FAIL.

- [ ] **Step 3: Implement** `routes/window.py` and register it in `app.py`. The span check: `(end - start).days > MAX_SPAN_DAYS` → "That range is too long" error.

- [ ] **Step 4: Run to see them pass**

Run: `.venv/bin/python -m pytest tests/test_window_route.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add routes/window.py app.py tests/test_window_route.py
git commit -m "Add POST /window to save the date range"
```

---

### Task 4: Header button, popup markup and the panel script

**Files:**
- Modify: `templates/base.html` (header row button; replace the `header-window-form` row, the old `<dialog>` and the old picker `<script>`)
- Create: `templates/_date_range.html` (the dialog, included from `base.html`)
- Create: `static/date-range.js` (pure functions + panel behaviour; preview rendering arrives in Task 5)
- Create: `tests/js/date_range_check.cjs`
- Test: `tests/test_date_range_js.py`, additions to `tests/test_range_context.py`

**Interfaces:**
- Consumes: the context keys from Task 2; `POST /window` from Task 3
- Produces (DOM): header button `#range-button` (`aria-haspopup="dialog"`, text = formatted `range_start – range_end`) placed between `.site-nav` and `.header-actions`; `<dialog id="range-dialog" data-rule='{{ range_rule|tojson }}' data-today="{{ range_today }}" data-first-year="{{ range_first_year }}" data-start="{{ range_start }}" data-end="{{ range_end }}">` containing a `<form method="post" action="{{ base_path() }}/window">` with hidden `grain`, `mode`, `start`, `end`, `next` (`next` = `request.url.path` plus query), the funnel button `#range-panel-toggle` (`aria-label` "Hide filters"/"Show filters", `aria-expanded`), `#range-label`, Cancel, Apply, the panel `#range-panel`, and an empty `#range-preview`.
- Produces (JS, exported via `module.exports` when `module` exists; DOM code runs only when `document` exists):
  - `resolveRule(rule, today: Date) -> [Date, Date]`
  - `convertRule(rule, grain, mode, today: Date) -> rule`: nearest equivalent covering the same resolved range
  - `stepLabel(grain, offset, today: Date) -> string` (e.g. `Aug 2026`, `2026`, `2020s`, `Nov 10, 2026`)
  - `formatRange(start: Date, end: Date) -> string` (`Jan 1, 2026 – Dec 31, 2026`)

- [ ] **Step 1: Write `tests/js/date_range_check.cjs`**: loads `../../static/date-range.js` with `require`, iterates `tests/fixtures/date_range_cases.json` asserting `resolveRule` matches each case (compare ISO strings), and asserts:
  - `convertRule({grain:"day",mode:"absolute",start:"2026-08-10",end:"2027-05-03"}, "month", "absolute", today)` → `{grain:"month",mode:"absolute",start:"2026-08",end:"2027-05"}`
  - `convertRule({grain:"month",mode:"absolute",start:"2026-08",end:"2027-05"}, "month", "relative", new Date(2026,10,13))` → `start:-3, end:6`
  - `stepLabel("month", -3, new Date(2026,10,13)) === "Aug 2026"`; `stepLabel("decade", 0, …) === "2020s"`
  - `formatRange(new Date(2026,0,1), new Date(2026,11,31)) === "Jan 1, 2026 – Dec 31, 2026"`
  Exits non-zero with a message on the first failure.

- [ ] **Step 2: Write `tests/test_date_range_js.py`**: skip if `node` missing; run `node tests/js/date_range_check.cjs`; assert return code 0. Add `node --check static/date-range.js`.

- [ ] **Step 3: Add page tests** to `tests/test_range_context.py`:

```python
@pytest.mark.parametrize("path", ["/dashboard", "/statements", "/accounts", "/settings"])
def test_the_picker_renders_on_every_page(client, path):
    html = client.get(path).text
    assert 'id="range-button"' in html and 'id="range-dialog"' in html
    assert "data-window-preset" not in html and "calendar-nav" not in html

def test_the_popup_carries_the_server_date(client):   # Review Focus 3
    assert f'data-today="{date.today().isoformat()}"' in client.get("/dashboard").text
```

- [ ] **Step 4: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_date_range_js.py tests/test_range_context.py -q`
Expected: FAIL.

- [ ] **Step 5: Implement.** Panel behaviour in `static/date-range.js`:
  - Opening reads `data-rule`; if the page shows an override (`data-start`/`data-end` differ from the rule resolved for `data-today`), it opens as `day/absolute` with those dates.
  - Grain segmented control (Day · Month · Year · Decade, buttons with `aria-pressed`), mode radios, then Start/End controls per the spec's table: `<input type="date">` (day absolute); `<select>` of months grouped by year with `<optgroup>`, of years, or of decades, spanning `data-first-year` to today's year + 10 (absolute); −/+ steppers with the value and `stepLabel` beside them (relative). Start's + stops at end; end's − stops at start; both stop at `RELATIVE_LIMITS`.
  - Changing grain or mode calls `convertRule`.
  - `#range-label` shows `formatRange` of the edited rule.
  - Panel visibility toggles via the funnel and is stored in `localStorage["metis_range_panel"]` inside try/catch; shown by default.
  - Apply writes the hidden fields and submits; Cancel and Escape close without saving; reopening starts again from the saved rule.
  - Load the script from `base.html` as `{{ base_path() }}/static/date-range.js?v=…` with `defer`.
  - Header placement: `#range-button` sits after `.site-nav` and before `.header-actions`, with `margin-left: auto` moving from `.header-actions` to it. Under 980px it stays on the top line with `metis` and the actions (the nav already drops to its own line via `order: 3`); under 640px its label may shorten to `Jan 2026 – Dec 2026`.
  - Under 640px the dialog is full width and height, with the panel stacked above the preview.
  - Remove from `base.html` the `header-window-form` row, the old dialog and the old picker script. Remove the matching CSS rules (`.header-window-form`, `.window-range-*`, `.window-presets`, `.header-fallback-*`, `.calendar-*`, including the dark-theme and media-query copies) in `static/styles.css`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Check in the browser** against a synthetic preview database (seed script pattern: `FINANCE_DB_PATH=<scratch>.duckdb`, a demo user, two example accounts). Open the popup, switch every grain and mode, step to the limits, Apply, Cancel, Escape, reload. Expected: the header range and the next page match what was applied; Cancel and Escape change nothing.

- [ ] **Step 8: Commit**

```bash
git add templates/base.html templates/_date_range.html static/date-range.js static/styles.css tests/js/date_range_check.cjs tests/test_date_range_js.py tests/test_range_context.py
git commit -m "Header date range picker: grain and mode panel, no presets"
```

---

### Task 5: The preview

**Files:**
- Modify: `static/date-range.js`
- Modify: `tests/js/date_range_check.cjs`

**Interfaces:**
- Consumes: `resolveRule` (Task 4), `#range-preview`, `data-today`
- Produces (pure, exported):
  - `monthsTouched(start: Date, end: Date) -> number`
  - `previewView(count: number) -> "full" | "mini" | "years"` (1–3, 4–24, 25+)
  - `quarterRows(start: Date, end: Date) -> Array<[year, firstMonth]>`: every quarter (first months 1, 4, 7, 10) the range touches, plus one quarter before and one after
  - `renderPreview(container: Element, start: Date, end: Date, today: Date) -> void` (DOM)

- [ ] **Step 1: Add assertions** to `tests/js/date_range_check.cjs`:
  - `monthsTouched(2026-11-10, 2027-01-20) === 3`; `monthsTouched(2026-01-01, 2026-12-31) === 12`; `monthsTouched(2026-08-01, 2028-07-31) === 24`
  - `previewView(3) === "full"`, `previewView(4) === "mini"`, `previewView(24) === "mini"`, `previewView(25) === "years"`
  - `quarterRows(2026-08-01, 2027-07-31)` → `[[2026,4],[2026,7],[2026,10],[2027,1],[2027,4],[2027,7],[2027,10]]`

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/test_date_range_js.py -q`
Expected: FAIL (`monthsTouched is not a function`).

- [ ] **Step 3: Implement the views** (markup and look as in mockup `preview-stages-v3`):
  - **full:** one column (max width ~300px): month name, weekday letters S M T W T F S, day numbers. In-range days tinted, start and end days filled, today outlined orange. The month before and after are included, dimmed.
  - **mini:** 3 columns, rows from `quarterRows`; each month is its short name and year over a 7-column grid of day squares with no numbers. Neighbour rows and months outside the range dimmed. Today's square orange.
  - **years:** one row per year: the year, then 12 labelled blocks (J F M A M J J A S O N D), in range tinted, today's month orange.
  - The preview has no buttons or links and no click handlers.

  Sizing (runs on open and on `resize`, from the container's height `H`), the part neither the signature nor the tests determine:

```js
// full: range months take ~80% of H; a neighbour month peeks in, faded.
monthH = Math.min(230, (H * 0.8) / count);            // 230px caps short ranges
cellH  = Math.floor((monthH - 54) / 6);               // title + weekdays + gaps ≈ 54px
fade   = H * 0.12;
// mini: range rows plus half a neighbour row at each end fill H.
rowH  = H / (rangeRows + 1);
gap   = Math.max(6, rowH * 0.12);
cellH = Math.max(3, Math.floor((rowH - gap - 22) / 6));
fade  = rowH * 0.5;
// years: rows share H; no fade.
rowH = Math.min(28, H / years);
```

  The content is centred vertically in an `overflow: hidden` container with a `mask-image` gradient (`transparent 0 → black fade → black calc(100% - fade) → transparent`), so the neighbours fade at top and bottom. Under 640px wide the container height is the dialog's remaining height after the stacked panel.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_date_range_js.py -q`
Expected: pass.

- [ ] **Step 5: Check in the browser** at 1440×900 and 390×844: ranges of 1, 3, 12 and 24 months, 120 months (decade) and a 30-year day range. Expected: no scrollbar in the preview or the page; January in column 1 in mini view; faded neighbours in full and mini; today marked.

- [ ] **Step 6: Commit**

```bash
git add static/date-range.js tests/js/date_range_check.cjs
git commit -m "Date range preview: full, mini and year views that never scroll"
```

---

### Task 6: Neumorphic style for the picker

**Files:**
- Modify: `static/styles.css`
- Modify: `templates/base.html` (bump the stylesheet `?v=`)

**Interfaces:**
- Consumes: the markup from Tasks 4–5
- Produces: `--neu-base`, `--neu-blue`, `--neu-pink`, `--neu-light`, `--neu-dark`, `--neu-accent`, `--neu-heading`, `--neu-text` on `:root` with the brief's values, and dark values under `:root[data-theme="dark"]` (base `#2A2D32`, light `#34383E`, dark `#1F2125`, accent `#9D8CF5`, heading `#E4E4E7`, text `#C4C4CC`, blue `#3A4A5A`, pink `#4A3A42`). Shared rules `.neu-raised`, `.neu-inset` and `.neu-press` (the 150ms press, none under reduced motion) used by the picker's button, dialog, panel controls, segmented control and steppers.

- [ ] **Step 1: Implement** the tokens and the picker styles, mapping the brief's `card`/`inset`/`button` onto the picker's elements through those three shared rules. Header range button: raised pill. Dialog: raised surface. Inputs, selects, the selected grain segment and a pressed stepper: inset. Every control keeps an edge at 3:1 or better (a 1px border in a token colour where the shadow alone falls short), and `:focus-visible` shows a 3px `--neu-accent` outline. Preview cells and blocks stay flat.

- [ ] **Step 2: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 3: Check in the browser**, light and dark themes, desktop and phone: header button, open popup at each view, keyboard Tab through every control (outline visible), and the same with `prefers-reduced-motion: reduce` emulated (no press transition). Expected: matches the style doc's "soft frame, flat data".

- [ ] **Step 4: Commit**

```bash
git add static/styles.css templates/base.html
git commit -m "Neumorphic style for the date range picker, light and dark"
```
