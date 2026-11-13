from typing import Annotated, Any, Dict

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from review import review_waiting_count
from services import get_current_user, template_context

templates = Jinja2Templates(directory="templates")

# Injectable current user: resolves from request cookie/query and caches on request.state.
CurrentUser = Annotated[Dict[str, Any], Depends(get_current_user)]


def render(request: Request, template_name: str, message: str = "", err: int = 0, **context: Any):
    """Render a template with the shared base context (current user, users, message/error)."""
    signed_in = getattr(request.state, "current_user", None)
    if signed_in and "review_waiting" not in context:
        context["review_waiting"] = review_waiting_count(int(signed_in["id"]))
    return templates.TemplateResponse(request, template_name, template_context(request, message, err, **context))
