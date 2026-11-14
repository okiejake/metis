"""Budget, and what bills and one-time items share: the expected form, saving
it, and the reconcile page."""

from datetime import date, timedelta
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import expected_form as ef
from inbox import record_action
from services import (
    DEFAULT_CATEGORY_COLOR,
    EXPECTED_SOURCE_TYPES,
    bill_occurrence_status,
    build_budget_summary,
    confirm_reconciliation,
    create_match_rule,
    delete_match_rule,
    detect_bill_suggestions,
    dismiss_suggestion,
    find_amount_suggestions,
    find_bill_suggestion,
    load_accounts,
    load_categories,
    load_expected_item,
    load_expected_items,
    load_match_rules_for_item,
    load_reconciliation_counts,
    load_reconciliations_for_item,
    occurrence_history_start,
    redirect_with_message,
    reset_dismissed_suggestions,
    resolve_forecast_window,
    resolve_safe_redirect_target,
    settle_one_time_items,
    suggest_rule_pattern,
    unlink_reconciliation,
    update_match_rule,
)
from web import CurrentUser, render

router = APIRouter()


def _reconcile_path(source_type: str, source_id: int) -> str:
    """Built only from a known item type, so form input never shapes a redirect."""
    if source_type == "bill":
        return f"/bills/{int(source_id)}/reconcile"
    if source_type == "one_time":
        return f"/one-time/{int(source_id)}/reconcile"
    return "/budget"


def form_path(source_type: str, source_id: Optional[int] = None) -> str:
    base = "/bills" if source_type == "bill" else "/one-time"
    return f"{base}/{int(source_id)}/edit" if source_id else f"{base}/new"


# --- The expected form ----------------------------------------------------------


def render_form(request: Request, user: Dict[str, Any], values: Dict[str, Any], action: str,
                editing: Optional[str], next_path: str, msg: str, err: int):
    """The expected form. `editing` is the item's source type when editing, so
    the kind of item is fixed."""
    return render(
        request,
        "expected_form.html",
        msg,
        err,
        values=values,
        action=action,
        editing=editing,
        heading=ef.heading(values, bool(editing)),
        repeats_options=[r for r in ef.REPEATS if not (editing == "bill" and r == ef.ONE_TIME)],
        repeats_labels=ef.REPEATS_LABELS,
        categories=load_categories(user["id"]),
        accounts=load_accounts(user["id"]),
        next_path=resolve_safe_redirect_target(next_path) if next_path else "",
        # The page's own address, without anything but the prefill ids and enums.
        range_next=request.url.path + ef.form_query(request.query_params),
    )


def new_form(request: Request, user: Dict[str, Any], form_base: str, msg: str, err: int):
    """The form for a new item, opened from /bills/new or /one-time/new."""
    query = dict(request.query_params)
    values = ef.blank(user["id"], query, ef.ONE_TIME if form_base == "/one-time" else "monthly")
    return render_form(request, user, values, form_base, None, query.get("next", ""), msg, err)


async def save_new(request: Request, user: Dict[str, Any], form_base: str):
    """Add a bill or a one-time item, whichever `repeats` says."""
    form = await request.form()
    next_path = str(form.get("next", "")) or "/budget"
    token = str(form.get("from_suggestion", "")).strip()
    if token and ef.added_suggestion_key(user["id"], token):
        return redirect_with_message(next_path, "Nothing left to change")
    try:
        values = ef.parse(user["id"], form)
    except ValueError as exc:
        return redirect_with_message(f"{form_base}/new{ef.form_query(form)}", str(exc), is_error=True)

    suggestion = find_bill_suggestion(user["id"], token) if token else None
    source_type, item_id = ef.create(user["id"], values)
    ef.link_transaction(user["id"], source_type, item_id, ef._int(form.get("from_tx")))
    if source_type == "one_time":
        settle_one_time_items(user["id"])
    if suggestion and source_type == "bill":
        dismiss_suggestion(user["id"], suggestion["key"], reason="added")
        action = record_action(user["id"], "bill", {"bill_id": item_id, "suggestion_key": suggestion["key"]})
        return redirect_with_message(next_path, f"added {values['name']}", undo=action)
    return redirect_with_message(next_path, f"added {values['name']}")


def render_reconcile(request: Request, user: Dict[str, Any], source_type: str, source_id: int,
                     rule_seed: str, msg: str, err: int):
    item = load_expected_item(user["id"], source_type, source_id)
    if not item:
        return redirect_with_message("/budget", "not found", is_error=True)

    rules = load_match_rules_for_item(user["id"], source_type, source_id)
    linked = load_reconciliations_for_item(user["id"], source_type, source_id)
    suggestions = find_amount_suggestions(user["id"], item["kind"], item["amount"])

    occurrences = None
    if source_type == "bill":
        raw = item["raw"]
        occurrences = bill_occurrence_status(raw, occurrence_history_start(raw), date.today() + timedelta(days=90), linked)

    return render(
        request,
        "reconcile.html",
        msg,
        err,
        item=item,
        rules=rules,
        linked=linked,
        suggestions=suggestions,
        occurrences=occurrences,
        rule_seed=rule_seed,
    )


# --- Budget -----------------------------------------------------------------------


@router.get("/expected")
def expected_page(request: Request):
    # Expected has been consolidated into Budget.
    return RedirectResponse(url="/budget", status_code=303)


@router.get("/budget", response_class=HTMLResponse)
def budget_page(request: Request, user: CurrentUser, start: str = "", end: str = "", msg: str = "", err: int = 0):
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1
    budget_summary = build_budget_summary(user["id"], window_start, window_end)

    items = load_expected_items(user["id"])
    counts = load_reconciliation_counts(user["id"])
    for item in items:
        item["recon_count"] = counts.get((item["source_type"], item["id"]), 0)

    return render(
        request,
        "budget.html",
        msg,
        err,
        budget_summary=budget_summary,
        bills=[item for item in items if item["source_type"] == "bill"],
        one_time_items=[item for item in items if item["source_type"] == "one_time"],
        suggestion_count=len(detect_bill_suggestions(user["id"])),
        categories=load_categories(user["id"]),
        accounts=load_accounts(user["id"]),
        default_color=DEFAULT_CATEGORY_COLOR,
        today_iso=date.today().isoformat(),
        start=window_start.isoformat(),
        end=window_end.isoformat(),
    )


@router.post("/expected/suggestions/dismiss")
def dismiss_suggestion_route(request: Request, user: CurrentUser, suggestion_key: str = Form(...)):
    dismiss_suggestion(user["id"], suggestion_key, reason="dismissed")
    return redirect_with_message("/budget", "Suggestion dismissed")


@router.post("/expected/suggestions/reset")
def reset_suggestions_route(request: Request, user: CurrentUser):
    reset_dismissed_suggestions(user["id"])
    return redirect_with_message("/budget", "Dismissed suggestions restored")


# --- Matching an expected transaction to actuals ----------------------------------


def _known(user_id: int, source_type: str, source_id: int) -> bool:
    return source_type in EXPECTED_SOURCE_TYPES and bool(load_expected_item(user_id, source_type, source_id))


@router.post("/expected/reconcile/confirm")
def confirm_match(
    request: Request, user: CurrentUser,
    source_type: str = Form(...),
    source_id: int = Form(...),
    imported_transaction_id: int = Form(...),
    description: str = Form(""),
):
    target = _reconcile_path(source_type, source_id)
    if not _known(user["id"], source_type, source_id):
        return redirect_with_message("/budget", "Expected item not found", is_error=True)

    created = confirm_reconciliation(user["id"], source_type, source_id, imported_transaction_id)
    if not created:
        return redirect_with_message(target, "That transaction is already reconciled", is_error=True)

    seed = suggest_rule_pattern(description)
    return redirect_with_message(
        f"{target}?rule_seed={quote_plus(seed)}", "Match confirmed — check the auto-match rule below"
    )


def _parse_rule_amount(value: str, label: str) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be zero or greater")
    return parsed


def _parse_rule_amount_bounds(mode: str, exact: str, minimum: str, maximum: str):
    """Turn the rule form's amount-mode fields into (amount_min, amount_max),
    where None means unbounded on that side (mode 'any' → no constraint)."""
    if mode == "exact":
        if not exact.strip():
            raise ValueError("Enter an exact amount to match")
        value = _parse_rule_amount(exact, "Amount")
        return value, value
    if mode == "range":
        lo = _parse_rule_amount(minimum, "Minimum amount") if minimum.strip() else None
        hi = _parse_rule_amount(maximum, "Maximum amount") if maximum.strip() else None
        if lo is None and hi is None:
            raise ValueError("Enter a minimum and/or maximum amount for the range")
        return lo, hi
    return None, None


@router.post("/expected/rules")
def save_rule(
    request: Request, user: CurrentUser,
    source_type: str = Form(...),
    source_id: int = Form(...),
    pattern: str = Form(...),
    rule_id: str = Form(""),
    amount_mode: str = Form("any"),
    amount_exact: str = Form(""),
    amount_min: str = Form(""),
    amount_max: str = Form(""),
):
    target = _reconcile_path(source_type, source_id)
    if not _known(user["id"], source_type, source_id):
        return redirect_with_message("/budget", "Expected item not found", is_error=True)

    try:
        lo, hi = _parse_rule_amount_bounds(amount_mode, amount_exact, amount_min, amount_max)
        if rule_id.strip():
            update_match_rule(user["id"], int(rule_id), pattern, amount_min=lo, amount_max=hi)
            message = "Match rule updated — transactions re-matched"
        else:
            create_match_rule(user["id"], source_type, source_id, pattern, amount_min=lo, amount_max=hi)
            message = "Match rule saved — matching transactions auto-reconciled"
    except ValueError as exc:
        return redirect_with_message(target, str(exc), is_error=True)

    return redirect_with_message(target, message)


@router.post("/expected/rules/{rule_id}/delete")
def remove_rule(request: Request, user: CurrentUser, rule_id: int, source_type: str = Form(...), source_id: int = Form(...)):
    delete_match_rule(user["id"], rule_id)
    return redirect_with_message(_reconcile_path(source_type, source_id), "Match rule deleted")


@router.post("/expected/reconcile/{recon_id}/unlink")
def unlink_match(request: Request, user: CurrentUser, recon_id: int, source_type: str = Form(...), source_id: int = Form(...)):
    unlink_reconciliation(user["id"], recon_id)
    return redirect_with_message(_reconcile_path(source_type, source_id), "Match removed")
