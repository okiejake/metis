from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from routes.categories import router as categories_router
from routes.core import router as core_router
from routes.manual import router as manual_router
from routes.overview import router as overview_router
from routes.recurring import router as recurring_router
from services import attach_user_cookie, format_currency, init_db
from web import templates

app = FastAPI(title="Metis Finance Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates.env.filters["currency"] = format_currency


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.middleware("http")
async def persist_current_user_cookie(request: Request, call_next):
    response = await call_next(request)
    user_slug = getattr(request.state, "current_user_slug", "")
    if user_slug:
        attach_user_cookie(response, user_slug)
    return response


app.include_router(core_router)
app.include_router(categories_router)
app.include_router(recurring_router)
app.include_router(manual_router)
app.include_router(overview_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
