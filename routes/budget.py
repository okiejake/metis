"""Budget (#104): one section where each category's expected transactions are
listed against its spending, and what it has not settled is settled in place.

The page and GET /budget/section render the same section. A background action
posts with next set to the section's address, so the server's fresh copy
replaces it (see static/partial.js); without script the same forms reload the
page. Which rows are open, the page of each list and the status filter travel
in the address.
"""

from datetime import date
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import settle
from services import (
    DEFAULT_CATEGORY_COLOR,
    build_budget_summary,
    detect_bill_suggestions,
    load_categories,
    page_of,
    resolve_forecast_window,
    unsettled_transactions,
)
from web import CurrentUser, render, render_part

router = APIRouter()

STATUSES = ("active", "completed", "paused", "all")


class BudgetState:
    """What the address carries: the range, the status filter, the open rows
    and the page of each list. Row keys are a category id, "income" or
    "uncategorized"; "all" opens every row."""

    def __init__(self, start: date, end: date, status: str, opened: str, pages: str):
        self.start, self.end = start, end
        self.status = status if status in STATUSES else "active"
        self.opened = {key for key in (opened or "").split(",") if key}
        self.pages: Dict[str, int] = {}
        for pair in (pages or "").split(","):
            key, _, number = pair.partition(":")
            if key and number.isdigit():
                self.pages[key] = int(number)

    def is_open(self, key: Any) -> bool:
        return "all" in self.opened or str(key) in self.opened

    def page(self, key: Any) -> int:
        return self.pages.get(str(key), 1)

    def query(self, opened: Optional[set] = None, pages: Optional[Dict[str, int]] = None,
              status: Optional[str] = None) -> str:
        opened = self.opened if opened is None else opened
        pages = self.pages if pages is None else pages
        fields = {"start": self.start.isoformat(), "end": self.end.isoformat(), "status": status or self.status,
                  "open": ",".join(sorted(opened)), "page": ",".join(f"{k}:{v}" for k, v in sorted(pages.items()))}
        return urlencode({key: value for key, value in fields.items() if value})

    def url(self, base: str = "/budget", **changes: Any) -> str:
        return f"{base}?{self.query(**changes)}"

    def page_link(self, key: Any, number: int) -> Dict[str, str]:
        pages = {**self.pages, str(key): number}
        return {"href": self.url(pages=pages), "partial": self.url(SECTION, pages=pages)}

    def toggle_url(self, key: Any) -> str:
        return self.url(opened=self.opened ^ {str(key)})


SECTION = "/budget/section"


def _context(user: Dict[str, Any], state: BudgetState, rule_tx: str, rule_kind: str) -> Dict[str, Any]:
    summary = build_budget_summary(user["id"], state.start, state.end)
    unsettled = unsettled_transactions(user["id"], state.start, state.end)
    lists: Dict[str, Any] = {}
    for key, rows in unsettled.items():
        if not state.is_open(key):
            lists[str(key)] = {"rows": [], "page": 1, "pages": 1, "total": len(rows), "lazy": True}
            continue
        listed = page_of(rows, state.page(key))
        for tx in listed["rows"]:
            tx["bills"] = settle.bills_to_match(user["id"], tx) if key != "uncategorized" else []
        if listed["page"] > 1:
            listed["prev"] = state.page_link(key, listed["page"] - 1)
        if listed["page"] < listed["pages"]:
            listed["next"] = state.page_link(key, listed["page"] + 1)
        lists[str(key)] = listed
    prompt = None
    if rule_tx.strip().isdigit():
        prompt = settle.rule_prompt(user["id"], int(rule_tx), rule_kind, state.start)
    return {
        "budget": summary,
        "lists": lists,
        "state": state,
        "rule_prompt": prompt,
        "categories": load_categories(user["id"]),
        "default_color": DEFAULT_CATEGORY_COLOR,
        "page_next": state.url(),
        "section_next": state.url(SECTION),
        "start": state.start.isoformat(),
        "end": state.end.isoformat(),
    }


def _state(user: Dict[str, Any], start: str, end: str, status: str, opened: str, pages: str):
    window_start, window_end, invalid = resolve_forecast_window(user["id"], start, end)
    return BudgetState(window_start, window_end, status, opened, pages), invalid


@router.get("/expected")
def expected_page(request: Request):
    # Expected has been consolidated into Budget.
    return RedirectResponse(url="/budget", status_code=303)


@router.get("/budget", response_class=HTMLResponse)
def budget_page(request: Request, user: CurrentUser, start: str = "", end: str = "", status: str = "active",
                open: str = "", page: str = "", rule_tx: str = "", rule_kind: str = "", msg: str = "", err: int = 0):
    state, invalid = _state(user, start, end, status, open, page)
    if invalid:
        msg, err = "Invalid date filter. Using saved/default range.", 1
    return render(request, "budget.html", msg, err, suggestion_count=len(detect_bill_suggestions(user["id"])),
                  **_context(user, state, rule_tx, rule_kind))


@router.get("/budget/section", response_class=HTMLResponse)
def budget_section(request: Request, user: CurrentUser, start: str = "", end: str = "", status: str = "active",
                   open: str = "", page: str = "", rule_tx: str = "", rule_kind: str = "", msg: str = "", err: int = 0):
    state, _invalid = _state(user, start, end, status, open, page)
    return render_part(request, "_budget_section.html", msg, err, section_only=True,
                       **_context(user, state, rule_tx, rule_kind))
