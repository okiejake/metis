"""The Review page and its actions. Every action lands back on its list with
a message that carries an Undo."""

from typing import Optional, Tuple, Union

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import review
from services import (
    count_dismissed_suggestions,
    create_category,
    load_accounts,
    load_categories,
    load_imported_transaction_by_id,
    redirect_with_message,
    reset_dismissed_suggestions,
    resolve_forecast_window,
)
from web import CurrentUser, render

router = APIRouter()

LISTS = ("transfers", "categories", "budgets", "recurring")


def _back(list_name: str, message: str, action_id: Optional[int]):
    """Return to the list. A None action means nothing was left to change."""
    target = f"/review#{list_name if list_name in LISTS else 'categories'}"
    if action_id is None:
        return redirect_with_message(target, "Nothing left to change")
    return redirect_with_message(target, message, undo=action_id)


def _choice(user_id: int, choice: str, new_category_name: str) -> Tuple[Union[int, str], bool]:
    """A picker value as (category id or "transfer", created). "new" reuses a
    category of that name, so a repeated submit doesn't fail, and otherwise
    creates it. Call only once there is something to change."""
    if choice == "transfer":
        return "transfer", False
    if choice == "new":
        existing = review.find_category_id(user_id, new_category_name)
        if existing:
            return existing, False
        return create_category(user_id, new_category_name), True
    if not choice.strip():
        raise ValueError("Choose a category")
    return int(choice), False


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, user: CurrentUser, start: str = "", end: str = "", msg: str = "", err: int = 0):
    window_start, window_end, invalid_filter = resolve_forecast_window(user["id"], start, end)
    if invalid_filter:
        msg, err = "Invalid date filter. Using saved/default range.", 1
    return render(
        request,
        "review.html",
        msg,
        err,
        review=review.build_review(user["id"], window_start, window_end),
        categories=load_categories(user["id"]),
        accounts=load_accounts(user["id"]),
        dismissed_count=count_dismissed_suggestions(user["id"]),
        start=window_start.isoformat(),
        end=window_end.isoformat(),
        show_forecast_window_form=True,
        window_form_action="/review",
    )


@router.post("/review/merchants/confirm")
def confirm_merchant(
    request: Request, user: CurrentUser,
    key: str = Form(...), choice: str = Form(""), new_category_name: str = Form(""), list: str = Form("categories"),
):
    if choice != "transfer" and not review.has_uncategorized(user["id"], key):
        return _back(list, "Confirmed", None)
    try:
        picked, created = _choice(user["id"], choice, new_category_name)
    except ValueError as exc:
        return redirect_with_message(f"/review#{list}", str(exc), is_error=True)
    if picked == "transfer":
        action = review.confirm_transfer(user["id"], key)
    else:
        action = review.confirm_category(user["id"], key, picked, created=created)
    return _back(list, "Confirmed", action)


@router.post("/review/merchants/dismiss")
def dismiss_merchant(request: Request, user: CurrentUser, key: str = Form(...)):
    return _back("transfers", "Dismissed", review.dismiss(user["id"], review.TRANSFER_DISMISS_PREFIX + key))


@router.post("/review/charges/{tx_id}")
def set_charge(
    request: Request, user: CurrentUser, tx_id: int, choice: str = Form(""), new_category_name: str = Form(""),
):
    if not load_imported_transaction_by_id(user["id"], tx_id):
        return _back("categories", "Confirmed", None)
    try:
        picked, created = _choice(user["id"], choice, new_category_name)
        action = review.set_charge(user["id"], tx_id, picked, created=created)
    except ValueError as exc:
        return redirect_with_message("/review#categories", str(exc), is_error=True)
    return _back("categories", "Confirmed", action)


@router.post("/review/budgets/confirm")
def confirm_budget(request: Request, user: CurrentUser, category_id: int = Form(...), amount: str = Form("")):
    try:
        monthly = float(amount)
        if monthly < 0:
            raise ValueError
    except ValueError:
        return redirect_with_message("/review#budgets", "Enter a monthly amount", is_error=True)
    return _back("budgets", "Confirmed", review.confirm_budget(user["id"], category_id, monthly))


@router.post("/review/budgets/dismiss")
def dismiss_budget(request: Request, user: CurrentUser, category_id: int = Form(...)):
    return _back("budgets", "Dismissed", review.dismiss(user["id"], f"{review.BUDGET_DISMISS_PREFIX}{category_id}"))


@router.post("/review/recurring/dismiss")
def dismiss_recurring(request: Request, user: CurrentUser, suggestion_key: str = Form(...)):
    return _back("recurring", "Dismissed", review.dismiss(user["id"], suggestion_key))


@router.post("/review/undo/{action_id}")
def undo_action(request: Request, user: CurrentUser, action_id: int):
    return redirect_with_message("/review", review.undo(user["id"], action_id))


@router.post("/review/restore")
def restore_dismissed(request: Request, user: CurrentUser):
    reset_dismissed_suggestions(user["id"])
    return redirect_with_message("/review", "Restored")
