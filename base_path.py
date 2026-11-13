"""Serving Metis under a path, such as `/metis_prod`, instead of at a site's root.

Set METIS_BASE_PATH to that path. Inside the app nothing changes: routes, `next`
targets and redirects all use plain paths like `/budget`. The base path is
added only where the browser sees a URL — links and forms (through the
`base_path()` template global), redirects and the sign-in cookie — and removed
from incoming requests. A proxy may forward requests with the path kept or
already stripped; both work.
"""

import os


def configured_base_path() -> str:
    """The base path with one leading slash and no trailing slash, or "" for
    the site root."""
    raw = os.getenv("METIS_BASE_PATH", "").strip().strip("/")
    return f"/{raw}" if raw else ""


def with_base_path(path: str) -> str:
    """Prefix a root-relative path. Anything else, such as an absolute URL, is
    returned unchanged."""
    base = configured_base_path()
    if base and path.startswith("/") and not path.startswith("//"):
        return base + path
    return path


class BasePathMiddleware:
    """Strip the base path from incoming requests and add it to redirects."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        base = configured_base_path()
        if not base or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path == base or path.startswith(base + "/"):
            scope = dict(scope)
            scope["path"] = path[len(base):] or "/"
            raw_path = scope.get("raw_path")
            if raw_path and raw_path.startswith(base.encode()):
                scope["raw_path"] = raw_path[len(base):] or b"/"

        async def send_with_base_path(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (name, with_base_path(value.decode("latin-1")).encode("latin-1"))
                    if name.lower() == b"location"
                    else (name, value)
                    for name, value in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_with_base_path)
