import json
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from services import (
    add_month_summary_rows,
    build_daily_balance_series,
    build_ledger_rows,
    build_monthly_totals,
    checking_actual_balance_before,
    collect_blended_transactions,
    first_checking_actual_date,
    get_setting_float,
    latest_checking_actual_date,
    load_categories,
    resolve_forecast_window,
)
from web import CurrentUser, render

router = APIRouter()


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request, user: CurrentUser, start: str = "", end: str = "", msg: str = "", err: int = 0):
    today = date.today()
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1

    opening_balance = get_setting_float(user["id"], "starting_balance", 0.0)
    window_start_balance = checking_actual_balance_before(user["id"], window_start, opening_balance)
    cutover = latest_checking_actual_date(user["id"])
    first_actual = first_checking_actual_date(user["id"])
    current_checking_balance = (
        checking_actual_balance_before(user["id"], cutover + timedelta(days=1), opening_balance)
        if cutover
        else opening_balance
    )

    transactions = collect_blended_transactions(user["id"], window_start, window_end)
    ledger_rows, first_negative_date, running_balance = build_ledger_rows(
        transactions, window_start_balance, window_start
    )
    display_rows = add_month_summary_rows(ledger_rows, window_start, window_end, window_start_balance)

    total_income = sum(tx["income"] for tx in transactions)
    total_expense = sum(tx["expense"] for tx in transactions)
    projected_end_balance = window_start_balance + total_income - total_expense

    forecast_start = (cutover + timedelta(days=1)) if cutover else None
    window_all_future = bool(cutover and window_start > cutover)

    return render(
        request,
        "ledger.html",
        msg,
        err,
        rows=display_rows,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
        today_iso=today.isoformat(),
        categories=load_categories(user["id"]),
        current_checking_balance=current_checking_balance,
        total_income=total_income,
        total_expense=total_expense,
        projected_end_balance=projected_end_balance,
        first_negative_date=first_negative_date.isoformat() if first_negative_date else "",
        forecast_start_iso=forecast_start.isoformat() if forecast_start else "",
        first_actual_iso=first_actual.isoformat() if first_actual else "",
        window_all_future=window_all_future,
        show_forecast_window_form=True,
        window_form_action="/ledger",
    )


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

    return render(
        request,
        "dashboard.html",
        msg,
        err,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
        show_forecast_window_form=True,
        window_form_action="/dashboard",
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
    )
