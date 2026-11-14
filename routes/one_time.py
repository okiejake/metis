"""One-time items: a single expected transaction. Added, edited, deleted and
reconciled here; each settles itself against the actual that pays it."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import expected_form as ef
from routes.expected import form_path, new_form, render_form, render_reconcile, save_new
from services import load_one_time_item_by_id, redirect_with_message, settle_one_time_items
from web import CurrentUser

router = APIRouter()


@router.get("/one-time/new", response_class=HTMLResponse)
def new_one_time_page(request: Request, user: CurrentUser, msg: str = "", err: int = 0):
    return new_form(request, user, "/one-time", msg, err)


@router.post("/one-time")
async def create_one_time(request: Request, user: CurrentUser):
    return await save_new(request, user, "/one-time")


@router.get("/one-time/{item_id}/edit", response_class=HTMLResponse)
def edit_one_time_page(request: Request, user: CurrentUser, item_id: int, next: str = "", msg: str = "", err: int = 0):
    item = load_one_time_item_by_id(user["id"], item_id)
    if not item:
        return redirect_with_message("/budget", "not found", is_error=True)
    return render_form(request, user, ef.from_one_time(item), form_path("one_time", item_id), "one_time", next, msg, err)


@router.post("/one-time/{item_id}/edit")
async def edit_one_time(request: Request, user: CurrentUser, item_id: int):
    if not load_one_time_item_by_id(user["id"], item_id):
        return redirect_with_message("/budget", "not found", is_error=True)
    form = await request.form()
    try:
        if form.get("repeats", ef.ONE_TIME) != ef.ONE_TIME:
            raise ValueError("a one-time item can't become a bill")
        values = ef.parse(user["id"], {**form, "repeats": ef.ONE_TIME})
    except ValueError as exc:
        return redirect_with_message(form_path("one_time", item_id), str(exc), is_error=True)
    ef.update_one_time(user["id"], item_id, values)
    settle_one_time_items(user["id"])
    return redirect_with_message(str(form.get("next", "")) or "/budget", f"saved {values['name']}")


@router.post("/one-time/{item_id}/delete")
def delete_one_time(request: Request, user: CurrentUser, item_id: int, next: str = ""):
    ef.delete_item(user["id"], "one_time", item_id)
    return redirect_with_message(next or "/budget", "deleted")


@router.get("/one-time/{item_id}/reconcile", response_class=HTMLResponse)
def reconcile_one_time(request: Request, user: CurrentUser, item_id: int, rule_seed: str = "", msg: str = "", err: int = 0):
    return render_reconcile(request, user, "one_time", item_id, rule_seed, msg, err)
