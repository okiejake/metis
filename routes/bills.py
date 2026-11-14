"""Bills and scheduled income: add, edit, pause, delete and reconcile. A bill
is any scheduled money out; scheduled income is a bill row of kind 'income'."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import expected_form as ef
from routes.expected import form_path, new_form, render_form, render_reconcile, save_new
from services import execute, load_bill_by_id, redirect_with_message
from web import CurrentUser

router = APIRouter()


@router.get("/bills/new", response_class=HTMLResponse)
def new_bill_page(request: Request, user: CurrentUser, msg: str = "", err: int = 0):
    return new_form(request, user, "/bills", msg, err)


@router.post("/bills")
async def create_bill(request: Request, user: CurrentUser):
    return await save_new(request, user, "/bills")


@router.get("/bills/{bill_id}/edit", response_class=HTMLResponse)
def edit_bill_page(request: Request, user: CurrentUser, bill_id: int, next: str = "", msg: str = "", err: int = 0):
    bill = load_bill_by_id(user["id"], bill_id)
    if not bill:
        return redirect_with_message("/budget", "not found", is_error=True)
    return render_form(request, user, ef.from_bill(bill), form_path("bill", bill_id), "bill", next, msg, err)


@router.post("/bills/{bill_id}/edit")
async def edit_bill(request: Request, user: CurrentUser, bill_id: int):
    bill = load_bill_by_id(user["id"], bill_id)
    if not bill:
        return redirect_with_message("/budget", "not found", is_error=True)
    form = await request.form()
    try:
        if form.get("repeats") == ef.ONE_TIME:
            raise ValueError("a bill can't become one-time")
        values = ef.parse(user["id"], form)
    except ValueError as exc:
        return redirect_with_message(form_path("bill", bill_id), str(exc), is_error=True)
    ef.update_bill(user["id"], bill, values, str(form.get("amount_change_scope", "all")))
    return redirect_with_message(str(form.get("next", "")) or "/budget", f"saved {values['name']}")


@router.post("/bills/{bill_id}/toggle")
def toggle_bill(request: Request, user: CurrentUser, bill_id: int, next: str = ""):
    bill = load_bill_by_id(user["id"], bill_id)
    if bill:
        execute("UPDATE bills SET active = NOT active WHERE id = ? AND user_id = ?", [bill_id, user["id"]])
    return redirect_with_message(next or "/budget", "resumed" if bill and not bill["active"] else "paused")


@router.post("/bills/{bill_id}/delete")
def delete_bill(request: Request, user: CurrentUser, bill_id: int, next: str = ""):
    ef.delete_item(user["id"], "bill", bill_id)
    return redirect_with_message(next or "/budget", "deleted")


@router.get("/bills/{bill_id}/reconcile", response_class=HTMLResponse)
def reconcile_bill(request: Request, user: CurrentUser, bill_id: int, rule_seed: str = "", msg: str = "", err: int = 0):
    return render_reconcile(request, user, "bill", bill_id, rule_seed, msg, err)
