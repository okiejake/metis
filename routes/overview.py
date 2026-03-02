import json
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from services import (
    add_month_summary_rows,
    build_daily_balance_series,
    build_ledger_rows,
    build_monthly_totals,
    collect_window_transactions,
    get_current_user,
    get_setting_float,
    load_categories,
    resolve_forecast_window,
    template_context,
)
from web import templates

router = APIRouter()


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request, start: str = "", end: str = "", msg: str = "", err: int = 0):
    user = get_current_user(request)
    today = date.today()
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1

    starting_balance = get_setting_float(user["id"], "starting_balance", 0.0)

    transactions = collect_window_transactions(user["id"], window_start, window_end)
    ledger_rows, first_negative_date, running_balance = build_ledger_rows(
        transactions, starting_balance, window_start
    )
    display_rows = add_month_summary_rows(ledger_rows, window_start, window_end, starting_balance)

    total_income = sum(tx["income"] for tx in transactions)
    total_expense = sum(tx["expense"] for tx in transactions)
    projected_end_balance = starting_balance + total_income - total_expense

    return templates.TemplateResponse(
        "ledger.html",
        template_context(
            request,
            msg,
            err,
            rows=display_rows,
            start=window_start.isoformat(),
            end=window_end.isoformat(),
            today_iso=today.isoformat(),
            categories=load_categories(user["id"]),
            starting_balance=starting_balance,
            total_income=total_income,
            total_expense=total_expense,
            projected_end_balance=projected_end_balance,
            first_negative_date=first_negative_date.isoformat() if first_negative_date else "",
            show_forecast_window_form=True,
            window_form_action="/ledger",
        ),
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, start: str = "", end: str = "", msg: str = "", err: int = 0):
    user = get_current_user(request)
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg = "Invalid date filter. Using saved/default range."
        err = 1

    starting_balance = get_setting_float(user["id"], "starting_balance", 0.0)
    transactions = collect_window_transactions(user["id"], window_start, window_end)
    ledger_rows, first_negative_date, running_balance = build_ledger_rows(
        transactions, starting_balance, window_start
    )
    monthly_rows = build_monthly_totals(window_start, window_end, starting_balance, transactions)
    chart_labels, chart_values = build_daily_balance_series(
        window_start, window_end, starting_balance, transactions
    )

    total_income = sum(tx["income"] for tx in transactions)
    total_expense = sum(tx["expense"] for tx in transactions)
    projected_end_balance = running_balance
    period_net = projected_end_balance - starting_balance
    average_monthly_net = period_net / len(monthly_rows) if monthly_rows else 0.0

    return templates.TemplateResponse(
        "dashboard.html",
        template_context(
            request,
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
        ),
    )
