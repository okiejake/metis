from urllib.parse import quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from routes.accounts import router as accounts_router
from routes.categories import router as categories_router
from routes.core import router as core_router
from routes.expected import router as expected_router
from routes.imports import router as imports_router
from routes.manual import router as manual_router
from routes.overview import router as overview_router
from routes.auth import router as auth_router
from routes.recurring import router as recurring_router
from routes.splits import router as splits_router
from services import (
    SESSION_COOKIE,
    any_user_has_password,
    format_currency,
    init_db,
    load_session_user,
    purge_expired_sessions,
)
from web import templates

app = FastAPI(title="Metis Finance Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates.env.filters["currency"] = format_currency


@app.on_event("startup")
def startup_event() -> None:
    init_db()
    purge_expired_sessions()


# Paths reachable without a session. Everything else — all 50-odd routes — is
# gated by the middleware below, so a new route is private by default rather than
# private only if someone remembers to decorate it.
PUBLIC_PATH_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/login", "/logout", "/setup", "/favicon.ico"}


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Resolve the signed-in user, or send them to sign in.

    Identity comes only from the session cookie. On a database that predates
    auth no password exists yet, so everything routes to /setup until one is
    created — the app is never briefly open while you get around to it."""
    path = request.url.path
    if path.startswith(PUBLIC_PATH_PREFIXES) or path in PUBLIC_PATHS:
        return await call_next(request)

    if not any_user_has_password():
        return RedirectResponse(url="/setup", status_code=303)

    user = load_session_user(request.cookies.get(SESSION_COOKIE, ""))
    if not user:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote_plus(target)}", status_code=303)

    request.state.current_user = user
    request.state.current_user_slug = user["slug"]
    return await call_next(request)


app.include_router(auth_router)
app.include_router(core_router)
app.include_router(categories_router)
app.include_router(recurring_router)
app.include_router(manual_router)
app.include_router(overview_router)
app.include_router(imports_router)
app.include_router(expected_router)
app.include_router(accounts_router)
app.include_router(splits_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
