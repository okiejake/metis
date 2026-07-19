from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services import (
    EXPECTED_SOURCE_TYPES,
    VALID_KINDS,
    build_budget_summary,
    confirm_reconciliation,
    count_dismissed_suggestions,
    create_match_rule,
    delete_match_rule,
    detect_recurring_suggestions,
    dismiss_suggestion,
    ensure_seed_accounts,
    find_amount_suggestions,
    get_connection,
    get_current_user,
    load_accounts,
    load_categories,
    resolve_budget_window,
    load_expected_item,
    load_expected_items,
    load_match_rules_for_item,
    load_reconciliation_counts,
    load_reconciliations_for_item,
    parse_iso_date,
    parse_optional_account_id,
    parse_optional_category_id,
    parse_positive_float,
    recurring_occurrence_status,
    redirect_with_message,
    reset_dismissed_suggestions,
    suggest_rule_pattern,
    template_context,
    unlink_reconciliation,
    update_match_rule,
)
from web import templates

router = APIRouter()


def _reconcile_path(source_type: str, source_id: int) -> str:
    return f"/expected/{source_type}/{source_id}/reconcile"


@router.get("/expected")
def expected_page(request: Request):
    # Expected has been consolidated into Budget.
    return RedirectResponse(url="/budget", status_code=303)


@router.get("/budget", response_class=HTMLResponse)
def budget_page(request: Request, start: str = "", end: str = "", msg: str = "", err: int = 0):
    user = get_current_user(request)
    ensure_seed_accounts(user["id"])

    window_start, window_end, invalid_filter = resolve_budget_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1
    budget_summary = build_budget_summary(user["id"], window_start, window_end)

    items = load_expected_items(user["id"])
    counts = load_reconciliation_counts(user["id"])
    for item in items:
        item["recon_count"] = counts.get((item["source_type"], item["id"]), 0)

    recurring_items = [item for item in items if item["source_type"] == "recurring"]
    one_time_items = [item for item in items if item["source_type"] == "one_time"]
    suggestions = detect_recurring_suggestions(user["id"])
    dismissed_count = count_dismissed_suggestions(user["id"])

    return templates.TemplateResponse(
        "budget.html",
        template_context(
            request,
            msg,
            err,
            budget_summary=budget_summary,
            recurring_items=recurring_items,
            one_time_items=one_time_items,
            suggestions=suggestions,
            dismissed_count=dismissed_count,
            categories=load_categories(user["id"]),
            accounts=load_accounts(user["id"]),
            today_iso=date.today().isoformat(),
            start=window_start.isoformat(),
            end=window_end.isoformat(),
            show_forecast_window_form=True,
            window_form_action="/budget",
        ),
    )


@router.post("/expected/suggestions/dismiss")
def dismiss_suggestion_route(request: Request, suggestion_key: str = Form(...)):
    user = get_current_user(request)
    dismiss_suggestion(user["id"], suggestion_key, reason="dismissed")
    return redirect_with_message("/budget", "Suggestion dismissed")


@router.post("/expected/suggestions/reset")
def reset_suggestions_route(request: Request):
    user = get_current_user(request)
    reset_dismissed_suggestions(user["id"])
    return redirect_with_message("/budget", "Dismissed suggestions restored")


@router.post("/expected/one-time")
def create_one_time_expected(
    request: Request,
    name: str = Form(...),
    kind: str = Form(...),
    amount: str = Form(...),
    category_id: str = Form(""),
    account_id: str = Form(""),
    tx_date: str = Form(...),
):
    user = get_current_user(request)
    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")
        if kind not in VALID_KINDS:
            raise ValueError("Kind must be income or expense")
        parsed_amount = parse_positive_float(amount)
        parsed_date = parse_iso_date(tx_date)
        parsed_category_id = parse_optional_category_id(user["id"], category_id)
        parsed_account_id = parse_optional_account_id(user["id"], account_id)

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO manual_transactions (name, kind, amount, tx_date, category_id, account_id, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [cleaned_name, kind, parsed_amount, parsed_date, parsed_category_id, parsed_account_id, user["id"]],
            )
    except ValueError as exc:
        return redirect_with_message("/budget", str(exc), is_error=True)

    return redirect_with_message("/budget", "One-time expected transaction added")


@router.get("/expected/{source_type}/{source_id}/reconcile", response_class=HTMLResponse)
def reconcile_page(
    request: Request,
    source_type: str,
    source_id: int,
    rule_seed: str = "",
    msg: str = "",
    err: int = 0,
):
    user = get_current_user(request)
    if source_type not in EXPECTED_SOURCE_TYPES:
        return redirect_with_message("/budget", "Unknown expected item type", is_error=True)

    item = load_expected_item(user["id"], source_type, source_id)
    if not item:
        return redirect_with_message("/budget", "Expected item not found", is_error=True)

    rules = load_match_rules_for_item(user["id"], source_type, source_id)
    linked = load_reconciliations_for_item(user["id"], source_type, source_id)
    suggestions = find_amount_suggestions(user["id"], item["kind"], item["amount"])

    occurrences = None
    if source_type == "recurring":
        raw = item["raw"]
        start = raw["start_date"]
        if hasattr(start, "date") and not isinstance(start, date):
            start = start.date()
        window_start = max(start, date.today() - timedelta(days=400))
        window_end = date.today() + timedelta(days=90)
        occurrences = recurring_occurrence_status(raw, window_start, window_end, linked)

    return templates.TemplateResponse(
        "reconcile.html",
        template_context(
            request,
            msg,
            err,
            item=item,
            rules=rules,
            linked=linked,
            suggestions=suggestions,
            occurrences=occurrences,
            rule_seed=rule_seed,
        ),
    )


@router.post("/expected/reconcile/confirm")
def confirm_match(
    request: Request,
    source_type: str = Form(...),
    source_id: int = Form(...),
    imported_transaction_id: int = Form(...),
    description: str = Form(""),
):
    user = get_current_user(request)
    target = _reconcile_path(source_type, source_id)
    if source_type not in EXPECTED_SOURCE_TYPES or not load_expected_item(user["id"], source_type, source_id):
        return redirect_with_message("/budget", "Expected item not found", is_error=True)

    created = confirm_reconciliation(user["id"], source_type, source_id, imported_transaction_id)
    if not created:
        return redirect_with_message(target, "That transaction is already reconciled", is_error=True)

    seed = suggest_rule_pattern(description)
    separator = "&" if "?" in target else "?"
    url = (
        f"{target}{separator}rule_seed={quote_plus(seed)}"
        f"&msg={quote_plus('Match confirmed — review the auto-match rule below')}&err=0"
    )
    return RedirectResponse(url=url, status_code=303)


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
    request: Request,
    source_type: str = Form(...),
    source_id: int = Form(...),
    pattern: str = Form(...),
    rule_id: str = Form(""),
    amount_mode: str = Form("any"),
    amount_exact: str = Form(""),
    amount_min: str = Form(""),
    amount_max: str = Form(""),
):
    user = get_current_user(request)
    target = _reconcile_path(source_type, source_id)
    if source_type not in EXPECTED_SOURCE_TYPES or not load_expected_item(user["id"], source_type, source_id):
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
def remove_rule(request: Request, rule_id: int, source_type: str = Form(...), source_id: int = Form(...)):
    user = get_current_user(request)
    delete_match_rule(user["id"], rule_id)
    return redirect_with_message(_reconcile_path(source_type, source_id), "Match rule deleted")


@router.post("/expected/reconcile/{recon_id}/unlink")
def unlink_match(request: Request, recon_id: int, source_type: str = Form(...), source_id: int = Form(...)):
    user = get_current_user(request)
    unlink_reconciliation(user["id"], recon_id)
    return redirect_with_message(_reconcile_path(source_type, source_id), "Match removed")


@router.post("/expected/one-time/{tx_id}/delete")
def delete_one_time_expected(request: Request, tx_id: int):
    user = get_current_user(request)
    with get_connection() as conn:
        conn.execute("DELETE FROM manual_transactions WHERE id = ? AND user_id = ?", [tx_id, user["id"]])
        conn.execute(
            "DELETE FROM expected_reconciliations WHERE user_id = ? AND source_type = 'one_time' AND source_id = ?",
            [user["id"], tx_id],
        )
        conn.execute(
            "DELETE FROM expected_match_rules WHERE user_id = ? AND source_type = 'one_time' AND source_id = ?",
            [user["id"], tx_id],
        )
    return redirect_with_message("/budget", "One-time expected transaction deleted")
