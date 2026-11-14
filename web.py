from datetime import date
from typing import Annotated, Any, Dict

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from review import review_waiting_count
from date_range import format_range, resolve_rule, rule_to_saved
from services import parse_iso_date
from services import get_current_user, load_range_rule, load_saved_ranges, range_first_year, template_context

templates = Jinja2Templates(directory="templates")

# Injectable current user: resolves from request cookie/query and caches on request.state.
CurrentUser = Annotated[Dict[str, Any], Depends(get_current_user)]


def render(request: Request, template_name: str, message: str = "", err: int = 0, **context: Any):
    """Render a template with the shared base context (current user, users, message/error)."""
    signed_in = getattr(request.state, "current_user", None)
    if signed_in and "review_waiting" not in context:
        context["review_waiting"] = review_waiting_count(int(signed_in["id"]))
    if signed_in:
        context.update(range_context(int(signed_in["id"]), context))
    return templates.TemplateResponse(request, template_name, template_context(request, message, err, **context))


def range_context(user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
    """What the header's date range picker needs on every page: the saved rule,
    and the range in effect. A route that shows its own range (a ?start=&end=
    link) passes start and end, and those win."""
    today = date.today()
    rule = load_range_rule(user_id)
    saved_start, saved_end = resolve_rule(rule, today)
    range_start, range_end = saved_start, saved_end
    try:
        range_start = parse_iso_date(context["start"]) if context.get("start") else saved_start
        range_end = parse_iso_date(context["end"]) if context.get("end") else saved_end
    except ValueError:
        range_start, range_end = saved_start, saved_end  # a malformed link date
    range_start, range_end = range_start.isoformat(), range_end.isoformat()
    return {
        "range_rule": rule_to_saved(rule),
        "range_start": range_start,
        "range_end": range_end,
        "range_label": format_range(parse_iso_date(range_start), parse_iso_date(range_end)),
        "range_today": today.isoformat(),
        "range_first_year": range_first_year(user_id, today),
        "range_saved": load_saved_ranges(user_id),
    }
