from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services import (
    add_months,
    build_card_statements,
    card_cycle_configured,
    latest_checking_actual_date,
    load_account_by_id,
    load_accounts,
    load_card_statement,
    load_recorded_statements,
    parse_iso_date,
    parse_optional_balance,
    redirect_with_message,
    update_card_statement,
)
from web import CurrentUser, render

router = APIRouter()

# The statements list estimates the open statement and the couple after it —
# as far out as an estimate built from expected charges stays worth reading.
STATEMENTS_AHEAD = 3


def _cards(user_id: int):
    return [account for account in load_accounts(user_id) if account.get("account_type") == "credit_card"]


def _load_card(user_id: int, account_id: int):
    card = load_account_by_id(user_id, account_id)
    return card if card and card.get("account_type") == "credit_card" else None


@router.get("/statements", response_class=HTMLResponse)
def statements_index(request: Request, user: CurrentUser, msg: str = "", err: int = 0):
    cards = _cards(user["id"])
    if cards:
        return RedirectResponse(url=f"/statements/{cards[0]['id']}", status_code=303)
    return render(request, "statements.html", msg, err, cards=[], card=None)


@router.get("/statements/{account_id}", response_class=HTMLResponse)
def card_statements_page(request: Request, user: CurrentUser, account_id: int, msg: str = "", err: int = 0):
    card = _load_card(user["id"], account_id)
    if not card:
        return redirect_with_message("/accounts", "Card not found", is_error=True)

    today = date.today()
    statements = []
    if card_cycle_configured(card):
        statements = build_card_statements(
            user["id"],
            card,
            add_months(today, STATEMENTS_AHEAD + 1),
            latest_checking_actual_date(user["id"]),
            today=today,
        )
    closed = [statement for statement in statements if statement["closed"]]
    upcoming = [statement for statement in statements if not statement["closed"]][:STATEMENTS_AHEAD]
    return render(
        request,
        "statements.html",
        msg,
        err,
        cards=_cards(user["id"]),
        card=card,
        configured=card_cycle_configured(card),
        latest=closed[-1] if closed else None,
        open_statement=next((s for s in upcoming if s["status"] == "open"), None),
        closed=list(reversed(closed)),
        upcoming=upcoming,
        mismatched=sum(1 for s in closed if s["difference"] and abs(s["difference"]) > 0.005),
    )


@router.get("/statements/{account_id}/{close}", response_class=HTMLResponse)
def statement_page(
    request: Request, user: CurrentUser, account_id: int, close: str, msg: str = "", err: int = 0
):
    card = _load_card(user["id"], account_id)
    if not card:
        return redirect_with_message("/accounts", "Card not found", is_error=True)
    try:
        close_date = parse_iso_date(close)
    except ValueError:
        return redirect_with_message(f"/statements/{account_id}", "Invalid statement date", is_error=True)

    statement, statements = load_card_statement(
        user["id"], card, close_date, latest_checking_actual_date(user["id"])
    )
    if statement is None:
        return redirect_with_message(
            f"/statements/{account_id}", f"No statement closes on {close_date}", is_error=True
        )
    index = next(i for i, item in enumerate(statements) if item["close_date"] == close_date)
    lines = sorted(
        statement["activity"] + [{**item, "expected": True} for item in statement["expected_items"]],
        key=lambda item: item["date"],
    )
    return render(
        request,
        "statement.html",
        msg,
        err,
        card=card,
        statement=statement,
        lines=lines,
        previous=statements[index - 1] if index > 0 else None,
        following=statements[index + 1] if index + 1 < len(statements) else None,
    )


@router.post("/statements/{account_id}/{close}")
def update_statement(
    request: Request, user: CurrentUser,
    account_id: int,
    close: str,
    close_date: str = Form(...),
    due_date: str = Form(...),
    issuer_balance: str = Form(""),
):
    back = f"/statements/{account_id}/{close}"
    try:
        current = parse_iso_date(close)
    except ValueError:
        return redirect_with_message(f"/statements/{account_id}", "Invalid statement date", is_error=True)
    recorded = next(
        (row for row in load_recorded_statements(user["id"], account_id) if row["close_date"] == current),
        None,
    )
    if recorded is None:
        return redirect_with_message(back, "Only a statement that has closed can be edited", is_error=True)

    try:
        new_close = parse_iso_date(close_date)
        update_card_statement(
            user["id"],
            int(recorded["id"]),
            new_close,
            parse_iso_date(due_date),
            parse_optional_balance(issuer_balance),
        )
    except ValueError as exc:
        return redirect_with_message(back, str(exc), is_error=True)
    return redirect_with_message(f"/statements/{account_id}/{new_close.isoformat()}", "Statement saved")
