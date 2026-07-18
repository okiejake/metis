from datetime import date, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    account_cycle_status,
    ensure_seed_accounts,
    first_checking_actual_date,
    forecast_card_payments,
    latest_checking_actual_date,
    get_connection,
    get_current_user,
    get_setting_float,
    load_account_by_id,
    load_accounts,
    parse_day,
    parse_form_bool,
    redirect_with_message,
    set_setting_float,
    template_context,
)
from web import templates

router = APIRouter()

VALID_ACCOUNT_TYPES = {"checking", "credit_card"}


def _parse_account_form(account_type: str, statement_day: str, due_day: str, statement_eom: str, due_eom: str):
    if account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError("Account type must be checking or credit card")
    if account_type == "credit_card":
        # 0 is the "last day of month" sentinel.
        parsed_statement = 0 if parse_form_bool(statement_eom) else parse_day(statement_day, fallback=1)
        parsed_due = 0 if parse_form_bool(due_eom) else parse_day(due_day, fallback=1)
        return parsed_statement, parsed_due
    return None, None


def _load_account_usage(user_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT account_id, COUNT(*) FROM (
                SELECT account_id FROM recurring_items WHERE user_id = ? AND account_id IS NOT NULL
                UNION ALL
                SELECT account_id FROM manual_transactions WHERE user_id = ? AND account_id IS NOT NULL
            ) GROUP BY account_id
            """,
            [user_id, user_id],
        ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def _projected_card_payments(user_id: int, horizon_days: int = 150):
    today = date.today()
    cutover = latest_checking_actual_date(user_id)
    rows = forecast_card_payments(user_id, today, today + timedelta(days=horizon_days), cutover)
    upcoming: dict = {}
    for row in rows:
        aid = int(row["account_id"])
        if aid not in upcoming or row["date"] < upcoming[aid]["date"]:
            upcoming[aid] = {"date": row["date"], "amount": abs(float(row["delta"]))}
    return upcoming


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, msg: str = "", err: int = 0):
    user = get_current_user(request)
    ensure_seed_accounts(user["id"])

    accounts = load_accounts(user["id"])
    usage = _load_account_usage(user["id"])
    projected = _projected_card_payments(user["id"])
    today = date.today()
    for account in accounts:
        account["assigned_count"] = usage.get(int(account["id"]), 0)
        if account["account_type"] == "credit_card" and account.get("statement_day") and account.get("due_day"):
            account["cycle"] = account_cycle_status(account, today)
            account["projected"] = projected.get(int(account["id"]))

    return templates.TemplateResponse(
        "accounts.html",
        template_context(
            request,
            msg,
            err,
            accounts=accounts,
            starting_balance=get_setting_float(user["id"], "starting_balance", 0.0),
            first_actual=first_checking_actual_date(user["id"]),
            today_iso=today.isoformat(),
        ),
    )


@router.post("/accounts/opening-balance")
def update_opening_balance(request: Request, value: str = Form(...)):
    user = get_current_user(request)
    try:
        amount = float(value)
    except ValueError:
        return redirect_with_message("/accounts", "Opening balance must be a number", is_error=True)

    set_setting_float(user["id"], "starting_balance", amount)
    return redirect_with_message("/accounts", "Checking opening balance updated")


@router.post("/accounts")
def create_account(
    request: Request,
    name: str = Form(...),
    account_type: str = Form(...),
    statement_day: str = Form(""),
    due_day: str = Form(""),
    statement_eom: str = Form(""),
    due_eom: str = Form(""),
):
    user = get_current_user(request)
    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Account name is required")
        parsed_statement, parsed_due = _parse_account_form(
            account_type, statement_day, due_day, statement_eom, due_eom
        )

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM accounts WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1",
                [user["id"], cleaned_name],
            ).fetchone()
            if existing:
                raise ValueError("An account with this name already exists")
            conn.execute(
                "INSERT INTO accounts (user_id, name, account_type, statement_day, due_day) VALUES (?, ?, ?, ?, ?)",
                [user["id"], cleaned_name, account_type, parsed_statement, parsed_due],
            )
    except ValueError as exc:
        return redirect_with_message("/accounts", str(exc), is_error=True)

    return redirect_with_message("/accounts", "Account added")


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account_page(request: Request, account_id: int, msg: str = "", err: int = 0):
    user = get_current_user(request)
    account = load_account_by_id(user["id"], account_id)
    if not account:
        return redirect_with_message("/accounts", "Account not found", is_error=True)
    return templates.TemplateResponse(
        "account_edit.html",
        template_context(request, msg, err, account=account),
    )


@router.post("/accounts/{account_id}/edit")
def edit_account(
    request: Request,
    account_id: int,
    name: str = Form(...),
    account_type: str = Form(...),
    statement_day: str = Form(""),
    due_day: str = Form(""),
    statement_eom: str = Form(""),
    due_eom: str = Form(""),
):
    user = get_current_user(request)
    if not load_account_by_id(user["id"], account_id):
        return redirect_with_message("/accounts", "Account not found", is_error=True)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Account name is required")
        parsed_statement, parsed_due = _parse_account_form(
            account_type, statement_day, due_day, statement_eom, due_eom
        )

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM accounts WHERE user_id = ? AND LOWER(name) = LOWER(?) AND id <> ? LIMIT 1",
                [user["id"], cleaned_name, account_id],
            ).fetchone()
            if existing:
                raise ValueError("An account with this name already exists")
            conn.execute(
                "UPDATE accounts SET name = ?, account_type = ?, statement_day = ?, due_day = ? WHERE id = ? AND user_id = ?",
                [cleaned_name, account_type, parsed_statement, parsed_due, account_id, user["id"]],
            )
    except ValueError as exc:
        return redirect_with_message(f"/accounts/{account_id}/edit", str(exc), is_error=True)

    return redirect_with_message("/accounts", "Account updated")


@router.post("/accounts/{account_id}/delete")
def delete_account(request: Request, account_id: int):
    user = get_current_user(request)
    if not load_account_by_id(user["id"], account_id):
        return redirect_with_message("/accounts", "Account not found", is_error=True)

    with get_connection() as conn:
        conn.execute(
            "UPDATE recurring_items SET account_id = NULL WHERE account_id = ? AND user_id = ?",
            [account_id, user["id"]],
        )
        conn.execute(
            "UPDATE manual_transactions SET account_id = NULL WHERE account_id = ? AND user_id = ?",
            [account_id, user["id"]],
        )
        conn.execute("DELETE FROM accounts WHERE id = ? AND user_id = ?", [account_id, user["id"]])

    return redirect_with_message("/accounts", "Account deleted")
