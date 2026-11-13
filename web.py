from datetime import date
from typing import Annotated, Any, Dict

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from review import review_waiting_count
from date_range import resolve_rule, rule_to_saved
from services import get_current_user, load_range_rule, range_first_year, template_context

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
    return {
        "range_rule": rule_to_saved(rule),
        "range_start": context.get("start") or saved_start.isoformat(),
        "range_end": context.get("end") or saved_end.isoformat(),
        "range_today": today.isoformat(),
        "range_first_year": range_first_year(user_id, today),
    }
