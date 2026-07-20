from typing import Annotated, Any, Dict

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from services import get_current_user, template_context

templates = Jinja2Templates(directory="templates")

# Injectable current user: resolves from request cookie/query and caches on request.state.
CurrentUser = Annotated[Dict[str, Any], Depends(get_current_user)]


def render(request: Request, template_name: str, message: str = "", err: int = 0, **context: Any):
    """Render a template with the shared base context (current user, users, message/error)."""
    return templates.TemplateResponse(template_name, template_context(request, message, err, **context))
