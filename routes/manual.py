from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    VALID_KINDS,
    get_connection,
    get_current_user,
    load_categories,
    load_manual_transaction_by_id,
    parse_form_bool,
    parse_iso_date,
    parse_optional_category_id,
    parse_positive_float,
    redirect_with_message,
    template_context,
)
from web import templates

router = APIRouter()


@router.post("/manual")
def create_manual_transaction(
    request: Request,
    name: str = Form(...),
    kind: str = Form(...),
    amount: str = Form(...),
    category_id: str = Form(""),
    tx_date: str = Form(...),
    make_recurring: str = Form("0"),
    start: str = Form(...),
    end: str = Form(...),
):
    user = get_current_user(request)
    redirect_target = f"/ledger?start={quote_plus(start)}&end={quote_plus(end)}"
    should_make_recurring = parse_form_bool(make_recurring)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")
        if kind not in VALID_KINDS:
            raise ValueError("Kind must be income or expense")

        parsed_amount = parse_positive_float(amount)
        parsed_date = parse_iso_date(tx_date)
        parsed_category_id = parse_optional_category_id(user["id"], category_id)

        with get_connection() as conn:
            if should_make_recurring:
                conn.execute(
                    """
                    INSERT INTO recurring_items (
                        name, kind, amount, start_date, end_date, frequency_type,
                        interval_months, semimonthly_day1, semimonthly_day2, day_of_month,
                        category_id, user_id, active
                    )
                    VALUES (?, ?, ?, ?, NULL, 'monthly', 1, 1, 15, ?, ?, ?, TRUE)
                    """,
                    [
                        cleaned_name,
                        kind,
                        parsed_amount,
                        parsed_date,
                        parsed_date.day,
                        parsed_category_id,
                        user["id"],
                    ],
                )
            else:
                conn.execute(
                    """
                    INSERT INTO manual_transactions (name, kind, amount, tx_date, category_id, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [cleaned_name, kind, parsed_amount, parsed_date, parsed_category_id, user["id"]],
                )
    except ValueError as exc:
        return redirect_with_message(redirect_target, str(exc), is_error=True)

    if should_make_recurring:
        return redirect_with_message(redirect_target, "Recurring item added")
    return redirect_with_message(redirect_target, "Manual transaction added")


@router.get("/manual/{tx_id}/edit", response_class=HTMLResponse)
def edit_manual_transaction_page(request: Request, tx_id: int, start: str = "", end: str = "", msg: str = "", err: int = 0):
    user = get_current_user(request)
    redirect_target = f"/ledger?start={quote_plus(start)}&end={quote_plus(end)}" if start and end else "/ledger"
    transaction = load_manual_transaction_by_id(user["id"], tx_id)
    if not transaction:
        return redirect_with_message(redirect_target, "Manual transaction not found", is_error=True)

    return templates.TemplateResponse(
        "manual_edit.html",
        template_context(
            request,
            msg,
            err,
            tx=transaction,
            categories=load_categories(user["id"]),
            start=start,
            end=end,
        ),
    )


@router.post("/manual/{tx_id}/edit")
def edit_manual_transaction(
    request: Request,
    tx_id: int,
    name: str = Form(...),
    kind: str = Form(...),
    amount: str = Form(...),
    category_id: str = Form(""),
    tx_date: str = Form(...),
    make_recurring: str = Form("0"),
    start: str = Form(...),
    end: str = Form(...),
):
    user = get_current_user(request)
    redirect_target = f"/ledger?start={quote_plus(start)}&end={quote_plus(end)}"
    edit_redirect_target = f"/manual/{tx_id}/edit?start={quote_plus(start)}&end={quote_plus(end)}"
    should_make_recurring = parse_form_bool(make_recurring)
    if not load_manual_transaction_by_id(user["id"], tx_id):
        return redirect_with_message(redirect_target, "Manual transaction not found", is_error=True)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")
        if kind not in VALID_KINDS:
            raise ValueError("Kind must be income or expense")

        parsed_amount = parse_positive_float(amount)
        parsed_date = parse_iso_date(tx_date)
        parsed_category_id = parse_optional_category_id(user["id"], category_id)

        with get_connection() as conn:
            if should_make_recurring:
                conn.execute(
                    """
                    INSERT INTO recurring_items (
                        name, kind, amount, start_date, end_date, frequency_type,
                        interval_months, semimonthly_day1, semimonthly_day2, day_of_month,
                        category_id, user_id, active
                    )
                    VALUES (?, ?, ?, ?, NULL, 'monthly', 1, 1, 15, ?, ?, ?, TRUE)
                    """,
                    [
                        cleaned_name,
                        kind,
                        parsed_amount,
                        parsed_date,
                        parsed_date.day,
                        parsed_category_id,
                        user["id"],
                    ],
                )
                conn.execute("DELETE FROM manual_transactions WHERE id = ? AND user_id = ?", [tx_id, user["id"]])
            else:
                conn.execute(
                    """
                    UPDATE manual_transactions
                    SET name = ?, kind = ?, amount = ?, tx_date = ?, category_id = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    [cleaned_name, kind, parsed_amount, parsed_date, parsed_category_id, tx_id, user["id"]],
                )
    except ValueError as exc:
        return redirect_with_message(edit_redirect_target, str(exc), is_error=True)

    if should_make_recurring:
        return redirect_with_message(redirect_target, "Manual transaction converted to recurring")
    return redirect_with_message(redirect_target, "Manual transaction updated")


@router.post("/manual/{tx_id}/delete")
def delete_manual_transaction(request: Request, tx_id: int, start: str = Form(...), end: str = Form(...)):
    user = get_current_user(request)
    with get_connection() as conn:
        conn.execute("DELETE FROM manual_transactions WHERE id = ? AND user_id = ?", [tx_id, user["id"]])

    redirect_target = f"/ledger?start={quote_plus(start)}&end={quote_plus(end)}"
    return redirect_with_message(redirect_target, "Manual transaction deleted")
