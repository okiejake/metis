from datetime import date
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse, RedirectResponse, Response

from date_range import MAX_SPAN_DAYS, resolve_rule, rule_from_form
from services import (
    delete_saved_range,
    redirect_with_message,
    resolve_safe_redirect_target,
    save_range_rule,
    save_saved_range,
    saved_range_name_taken,
)
from web import CurrentUser

router = APIRouter()

# A link's one-off dates and an old message must not survive Apply, or the page
# would come back showing the range the user just replaced.
_DROPPED_KEYS = {"start", "end", "msg", "err", "undo"}


def clean_window_next(target: str) -> str:
    """The same-site page to return to, without one-off dates or messages."""
    path, _, fragment = resolve_safe_redirect_target(target).partition("#")
    path, _, query = path.partition("?")
    kept = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key not in _DROPPED_KEYS]
    url = f"{path}?{urlencode(kept)}" if kept else path
    return f"{url}#{fragment}" if fragment else url


@router.post("/window")
def save_window(
    user: CurrentUser,
    grain: str = Form(""),
    mode: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    next: str = Form("/dashboard"),
):
    back = clean_window_next(next)
    try:
        rule = rule_from_form(grain, mode, start, end)
    except ValueError as error:
        return redirect_with_message(back, str(error), is_error=True)
    if span_too_long(rule):
        return redirect_with_message(back, TOO_LONG, is_error=True)
    save_range_rule(user["id"], rule)
    return RedirectResponse(url=back, status_code=303)


TOO_LONG = "that range is too long: up to 100 years"


def span_too_long(rule) -> bool:
    range_start, range_end = resolve_rule(rule, date.today())
    return (range_end - range_start).days > MAX_SPAN_DAYS


SAVED_NAME_MAX = 60


@router.post("/window/saved")
def save_filter(
    user: CurrentUser,
    name: str = Form(""),
    grain: str = Form(""),
    mode: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
):
    """Save the picker's range under a name, exactly as set. JSON, so the
    popup stays open with the user's unapplied edits."""
    # Replies carry fixed messages only, never an exception's own text.
    name = name.strip()
    if not name:
        return _bad("enter a name for this filter")
    if len(name) > SAVED_NAME_MAX:
        return _bad(f"keep the name to {SAVED_NAME_MAX} characters")
    try:
        rule = rule_from_form(grain, mode, start, end)
    except ValueError:
        return _bad("that range isn't valid")  # the picker only sends valid ranges
    if span_too_long(rule):
        return _bad(TOO_LONG)
    if saved_range_name_taken(user["id"], name):
        return _bad(f'a filter named "{name}" already exists')
    return JSONResponse(save_saved_range(user["id"], name, rule), status_code=201)


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


@router.post("/window/saved/{range_id}/delete")
def delete_filter(user: CurrentUser, range_id: int):
    if not delete_saved_range(user["id"], range_id):
        return JSONResponse({"error": "that filter doesn't exist"}, status_code=404)
    return Response(status_code=204)
