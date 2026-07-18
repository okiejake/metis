from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    VALID_FREQUENCIES,
    VALID_KINDS,
    delete_overrides_for_item,
    dismiss_suggestion,
    get_connection,
    get_current_user,
    get_setting_float,
    load_accounts,
    load_all_recurring,
    load_amount_overrides,
    load_categories,
    load_recurring_item_by_id,
    parse_day,
    parse_form_bool,
    parse_iso_date,
    parse_optional_account_id,
    parse_optional_category_id,
    parse_optional_date,
    parse_positive_float,
    parse_positive_int,
    redirect_with_message,
    save_amount_override,
    set_setting_float,
    summarize_frequency,
    template_context,
)
from web import templates

router = APIRouter()


@router.get("/recurring", response_class=HTMLResponse)
def recurring_page(request: Request, msg: str = "", err: int = 0):
    user = get_current_user(request)
    recurring_items = load_all_recurring(user["id"])
    for item in recurring_items:
        item["frequency_summary"] = summarize_frequency(item)

    return templates.TemplateResponse(
        "recurring.html",
        template_context(
            request,
            msg,
            err,
            items=recurring_items,
            categories=load_categories(user["id"]),
            starting_balance=get_setting_float(user["id"], "starting_balance", 0.0),
            today_iso=date.today().isoformat(),
        ),
    )


@router.post("/settings/starting-balance")
def update_starting_balance(request: Request, value: str = Form(...)):
    user = get_current_user(request)
    try:
        amount = float(value)
    except ValueError:
        return redirect_with_message("/recurring", "Starting balance must be a number", is_error=True)

    set_setting_float(user["id"], "starting_balance", amount)
    return redirect_with_message("/recurring", "Starting balance updated")


@router.post("/recurring")
def create_recurring_item(
    request: Request,
    name: str = Form(...),
    kind: str = Form(...),
    amount: str = Form(...),
    category_id: str = Form(""),
    account_id: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(""),
    frequency_type: str = Form(...),
    interval_months: str = Form("1"),
    day_of_month: str = Form("1"),
    day_of_month_touched: str = Form("0"),
    semimonthly_day1: str = Form("1"),
    semimonthly_day2: str = Form("15"),
    suggestion_key: str = Form(""),
):
    user = get_current_user(request)
    redirect_target = "/expected" if suggestion_key.strip() else "/recurring"
    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")

        if kind not in VALID_KINDS:
            raise ValueError("Kind must be income or expense")
        if frequency_type not in VALID_FREQUENCIES:
            raise ValueError("Invalid frequency type")

        parsed_amount = parse_positive_float(amount)
        parsed_start_date = parse_iso_date(start_date)
        parsed_end_date = parse_optional_date(end_date)
        if parsed_end_date and parsed_end_date < parsed_start_date:
            raise ValueError("End date cannot be before start date")

        parsed_interval = parse_positive_int(interval_months, fallback=1)
        parsed_day_of_month = parse_day(day_of_month, fallback=parsed_start_date.day)
        if frequency_type in {"monthly", "every_x_months", "yearly"} and not parse_form_bool(day_of_month_touched):
            parsed_day_of_month = parsed_start_date.day
        parsed_day1 = parse_day(semimonthly_day1, fallback=1)
        parsed_day2 = parse_day(semimonthly_day2, fallback=15)
        parsed_category_id = parse_optional_category_id(user["id"], category_id)
        parsed_account_id = parse_optional_account_id(user["id"], account_id)

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO recurring_items (
                    name, kind, amount, start_date, end_date, frequency_type,
                    interval_months, semimonthly_day1, semimonthly_day2, day_of_month,
                    category_id, account_id, user_id, active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                """,
                [
                    cleaned_name,
                    kind,
                    parsed_amount,
                    parsed_start_date,
                    parsed_end_date,
                    frequency_type,
                    parsed_interval,
                    parsed_day1,
                    parsed_day2,
                    parsed_day_of_month,
                    parsed_category_id,
                    parsed_account_id,
                    user["id"],
                ],
            )
    except ValueError as exc:
        return redirect_with_message(redirect_target, str(exc), is_error=True)

    if suggestion_key.strip():
        # Remember the confirmed suggestion so it stops being suggested.
        dismiss_suggestion(user["id"], suggestion_key, reason="added")
        return redirect_with_message(
            "/expected", f"Added recurring item “{cleaned_name}” from suggestion"
        )
    return redirect_with_message("/recurring", "Recurring item added")


@router.get("/recurring/{item_id}/edit", response_class=HTMLResponse)
def edit_recurring_item_page(request: Request, item_id: int, msg: str = "", err: int = 0):
    user = get_current_user(request)
    item = load_recurring_item_by_id(user["id"], item_id)
    if not item:
        return redirect_with_message("/recurring", "Recurring item not found", is_error=True)

    overrides = load_amount_overrides(item_id)
    current_amount = float(item["amount"])
    today = date.today()
    for ovr in overrides:
        if ovr["effective_date"] <= today:
            current_amount = float(ovr["amount"])

    return templates.TemplateResponse(
        "recurring_edit.html",
        template_context(
            request, msg, err,
            item=item,
            categories=load_categories(user["id"]),
            accounts=load_accounts(user["id"]),
            current_amount=current_amount,
            has_overrides=len(overrides) > 0,
        ),
    )


@router.post("/recurring/{item_id}/edit")
def edit_recurring_item(
    request: Request,
    item_id: int,
    name: str = Form(...),
    kind: str = Form(...),
    amount: str = Form(...),
    category_id: str = Form(""),
    account_id: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(""),
    frequency_type: str = Form(...),
    interval_months: str = Form("1"),
    day_of_month: str = Form("1"),
    day_of_month_touched: str = Form("0"),
    semimonthly_day1: str = Form("1"),
    semimonthly_day2: str = Form("15"),
    amount_change_scope: str = Form("all"),
):
    user = get_current_user(request)
    existing_item = load_recurring_item_by_id(user["id"], item_id)
    if not existing_item:
        return redirect_with_message("/recurring", "Recurring item not found", is_error=True)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")

        if kind not in VALID_KINDS:
            raise ValueError("Kind must be income or expense")
        if frequency_type not in VALID_FREQUENCIES:
            raise ValueError("Invalid frequency type")

        parsed_amount = parse_positive_float(amount)
        parsed_start_date = parse_iso_date(start_date)
        parsed_end_date = parse_optional_date(end_date)
        if parsed_end_date and parsed_end_date < parsed_start_date:
            raise ValueError("End date cannot be before start date")

        parsed_interval = parse_positive_int(interval_months, fallback=1)
        parsed_day_of_month = parse_day(day_of_month, fallback=parsed_start_date.day)
        if frequency_type in {"monthly", "every_x_months", "yearly"} and not parse_form_bool(day_of_month_touched):
            parsed_day_of_month = parsed_start_date.day
        parsed_day1 = parse_day(semimonthly_day1, fallback=1)
        parsed_day2 = parse_day(semimonthly_day2, fallback=15)
        parsed_category_id = parse_optional_category_id(user["id"], category_id)
        parsed_account_id = parse_optional_account_id(user["id"], account_id)

        amount_changed = parsed_amount != float(existing_item["amount"])

        if amount_change_scope == "future" and amount_changed:
            # Update everything except amount; save override for today forward
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE recurring_items
                    SET name = ?, kind = ?, start_date = ?, end_date = ?, frequency_type = ?,
                        interval_months = ?, semimonthly_day1 = ?, semimonthly_day2 = ?, day_of_month = ?,
                        category_id = ?, account_id = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    [
                        cleaned_name,
                        kind,
                        parsed_start_date,
                        parsed_end_date,
                        frequency_type,
                        parsed_interval,
                        parsed_day1,
                        parsed_day2,
                        parsed_day_of_month,
                        parsed_category_id,
                        parsed_account_id,
                        item_id,
                        user["id"],
                    ],
                )
            save_amount_override(item_id, date.today(), parsed_amount)
        else:
            # Update everything including amount; clear any overrides
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE recurring_items
                    SET name = ?, kind = ?, amount = ?, start_date = ?, end_date = ?, frequency_type = ?,
                        interval_months = ?, semimonthly_day1 = ?, semimonthly_day2 = ?, day_of_month = ?,
                        category_id = ?, account_id = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    [
                        cleaned_name,
                        kind,
                        parsed_amount,
                        parsed_start_date,
                        parsed_end_date,
                        frequency_type,
                        parsed_interval,
                        parsed_day1,
                        parsed_day2,
                        parsed_day_of_month,
                        parsed_category_id,
                        parsed_account_id,
                        item_id,
                        user["id"],
                    ],
                )
            delete_overrides_for_item(item_id)
    except ValueError as exc:
        target = f"/recurring/{item_id}/edit"
        return redirect_with_message(target, str(exc), is_error=True)

    return redirect_with_message("/recurring", "Recurring item updated")


@router.post("/recurring/{item_id}/toggle")
def toggle_recurring_item(request: Request, item_id: int):
    user = get_current_user(request)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE recurring_items
            SET active = NOT active
            WHERE id = ? AND user_id = ?
            """,
            [item_id, user["id"]],
        )
    return redirect_with_message("/recurring", "Recurring item updated")


@router.post("/recurring/{item_id}/delete")
def delete_recurring_item(request: Request, item_id: int):
    user = get_current_user(request)
    delete_overrides_for_item(item_id)
    with get_connection() as conn:
        conn.execute("DELETE FROM recurring_items WHERE id = ? AND user_id = ?", [item_id, user["id"]])
        # Drop any reconciliations / match rules tied to this expected item so the
        # linked actuals are freed for future matching.
        conn.execute(
            "DELETE FROM expected_reconciliations WHERE user_id = ? AND source_type = 'recurring' AND source_id = ?",
            [user["id"], item_id],
        )
        conn.execute(
            "DELETE FROM expected_match_rules WHERE user_id = ? AND source_type = 'recurring' AND source_id = ?",
            [user["id"], item_id],
        )
    return redirect_with_message("/recurring", "Recurring item deleted")
