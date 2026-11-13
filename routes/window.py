from datetime import date
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from date_range import MAX_SPAN_DAYS, resolve_rule, rule_from_form
from services import redirect_with_message, resolve_safe_redirect_target, save_range_rule
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
    range_start, range_end = resolve_rule(rule, date.today())
    if (range_end - range_start).days > MAX_SPAN_DAYS:
        return redirect_with_message(back, "That range is too long: up to 100 years", is_error=True)
    save_range_rule(user["id"], rule)
    return RedirectResponse(url=back, status_code=303)
