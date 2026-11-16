import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse

import inbox as inbox_module
import ledger
import settle
from services import (
    account_balance_before,
    account_cutover,
    account_first_date,
    add_month_summary_rows,
    build_daily_balance_series,
    build_ledger_rows,
    build_monthly_totals,
    checking_actual_balance_before,
    collect_account_transactions,
    collect_blended_transactions,
    get_setting_float,
    load_accounts,
    load_categories,
    load_transaction_splits,
    redirect_with_message,
    resolve_forecast_window,
    split_remainder,
)
from inbox import build_inbox
from web import CurrentUser, render, render_part

router = APIRouter()


def _ledger_context(user: Dict[str, Any], start: str, end: str, account: str, q: str, category: List[str],
                    merchant: str, open: str, rule_tx: str, rule_kind: str) -> Dict[str, Any]:
    """Everything the ledger page and its table need: one account's rows for
    the window, what a filter keeps, and the one row whose panel is open."""
    user_id = int(user["id"])
    window_start, window_end, invalid = resolve_forecast_window(user_id, start, end)
    view = ledger.LedgerView.from_params(account, q, category, merchant, open, start, end)
    selected, fell_back = ledger.resolve_account(user_id, view.account_id)
    if selected:
        view = replace(view, account_id=int(selected["id"]))

    start_balance = account_balance_before(user_id, selected, window_start)
    cutover = account_cutover(user_id, selected)
    current_balance = account_balance_before(user_id, selected, cutover + timedelta(days=1) if cutover else window_start)
    transactions = collect_account_transactions(user_id, selected, window_start, window_end)
    ledger_rows, _first_negative, _end = build_ledger_rows(transactions, start_balance, window_start)
    shown = ledger.filter_rows(user_id, ledger_rows, view, window_start, window_end)
    rows = shown if view.filtered else add_month_summary_rows(ledger_rows, window_start, window_end, start_balance)
    payments = sum(tx["income"] for tx in transactions)
    charges = sum(tx["expense"] for tx in transactions)

    categories = load_categories(user_id)
    state_query = view.query()
    page_next = f"/ledger?{state_query}"
    first_actual = account_first_date(user_id, selected)
    return {
        "view": view,
        "account": selected,
        "accounts": load_accounts(user_id),
        "fell_back": fell_back,
        "invalid_window": invalid,
        "rows": rows,
        "panel": _panel(user_id, view, shown, categories),
        "rule_prompt": (settle.rule_prompt(user_id, int(rule_tx), rule_kind, window_start)
                        if rule_tx.strip().isdigit() else None),
        "chips": view.chips({int(c["id"]): (c.get("label") or c["name"]) for c in categories}),
        "categories": categories,
        "has_forecast": any(row.get("row_type") != "month_summary" and row.get("type") != "actual"
                            and not row.get("due_not_posted") for row in rows),
        "current_balance": current_balance,
        "payments": payments,
        "charges": charges,
        "projected_balance": start_balance + payments - charges,
        "forecast_start_iso": (cutover + timedelta(days=1)).isoformat() if cutover else "",
        "first_actual_iso": first_actual.isoformat() if first_actual else "",
        "window_all_future": bool(cutover and window_start > cutover),
        "state_query": state_query,
        "page_next": page_next,
        "section_next": f"/ledger/table?{state_query}",
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "today_iso": date.today().isoformat(),
    }


def _panel(user_id: int, view: "ledger.LedgerView", rows: List[Dict[str, Any]],
           categories: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The open row's action panel: which transaction, which action, and what
    that action's form needs. Only one row carries a panel, so the page stays
    light however many rows it shows."""
    tx_id, action = view.opened()
    row = next((r for r in rows if r.get("type") == "actual" and r.get("id") == tx_id), None)
    if row is None or action not in PANEL_ACTIONS:
        return None
    panel: Dict[str, Any] = {"tx_id": tx_id, "action": action, "row": row}
    if action == "split":
        panel["splits"] = load_transaction_splits(user_id, tx_id)
        panel["remainder"] = split_remainder(user_id, tx_id)
        panel["magnitude"] = abs(float(row["delta"]))
    elif action == "match":
        panel["tx"] = {"id": tx_id, "kind": row["kind"], "bills": settle.bills_to_match(user_id, {"signed": row["delta"], "amount": abs(row["delta"])})}
    return panel


PANEL_ACTIONS = ("category", "split", "match", "note")


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request, user: CurrentUser, start: str = "", end: str = "", account: str = "", q: str = "",
                category: List[str] = Query([]), merchant: str = "", open: str = "", rule_tx: str = "",
                rule_kind: str = "", msg: str = "", err: int = 0):
    context = _ledger_context(user, start, end, account, q, category, merchant, open, rule_tx, rule_kind)
    if context["invalid_window"]:
        msg, err = "Invalid date filter. Using saved/default range.", 1
    elif context["fell_back"]:
        msg, err = "Account not found. Showing your checking account.", 1
    return render(request, "ledger.html", msg, err, **context)


@router.get("/ledger/table", response_class=HTMLResponse)
def ledger_table(request: Request, user: CurrentUser, start: str = "", end: str = "", account: str = "", q: str = "",
                 category: List[str] = Query([]), merchant: str = "", open: str = "", rule_tx: str = "",
                 rule_kind: str = "", msg: str = "", err: int = 0):
    context = _ledger_context(user, start, end, account, q, category, merchant, open, rule_tx, rule_kind)
    return render_part(request, "_ledger_section.html", msg, err, section_only=True, **context)


@router.post("/merchants/transfer")
def merchant_transfer(request: Request, user: CurrentUser, merchant: str = Form(""), next: str = Form("/ledger")):
    """Every debit from this merchant is a transfer between the owner's
    accounts, now and on later imports: the inbox's transfer confirm."""
    key = ledger.merchant_for_token(int(user["id"]), merchant)
    if not key:
        return redirect_with_message(next or "/ledger", "Merchant not found", is_error=True)
    action = inbox_module.confirm_transfer(int(user["id"]), key)
    if action is None:
        return redirect_with_message(next or "/ledger", "nothing left to change")
    return redirect_with_message(next or "/ledger", "marked as transfers", undo=action)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, user: CurrentUser, start: str = "", end: str = "", msg: str = "", err: int = 0):
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1

    opening_balance = get_setting_float(user["id"], "starting_balance", 0.0)
    window_start_balance = checking_actual_balance_before(user["id"], window_start, opening_balance)
    transactions = collect_blended_transactions(user["id"], window_start, window_end)
    ledger_rows, first_negative_date, running_balance = build_ledger_rows(
        transactions, window_start_balance, window_start
    )
    monthly_rows = build_monthly_totals(window_start, window_end, window_start_balance, transactions)
    chart_labels, chart_values = build_daily_balance_series(
        window_start, window_end, window_start_balance, transactions
    )

    starting_balance = window_start_balance
    total_income = sum(tx["income"] for tx in transactions)
    total_expense = sum(tx["expense"] for tx in transactions)
    projected_end_balance = running_balance
    period_net = projected_end_balance - starting_balance
    average_monthly_net = period_net / len(monthly_rows) if monthly_rows else 0.0
    inbox_summary = build_inbox(user["id"], window_start, window_end)

    return render(
        request,
        "dashboard.html",
        msg,
        err,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
        starting_balance=starting_balance,
        total_income=total_income,
        total_expense=total_expense,
        projected_end_balance=projected_end_balance,
        period_net=period_net,
        average_monthly_net=average_monthly_net,
        monthly_rows=monthly_rows,
        transaction_count=len(ledger_rows),
        first_negative_date=first_negative_date.isoformat() if first_negative_date else "",
        chart_labels_json=json.dumps(chart_labels),
        chart_values_json=json.dumps(chart_values),
        inbox_line={"percent": inbox_summary["meter"]["percent"], "waiting": inbox_summary["waiting"]},
    )
