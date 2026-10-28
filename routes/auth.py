from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services import (
    PASSWORD_MIN_LENGTH,
    SESSION_COOKIE,
    any_user_has_password,
    attach_session_cookie,
    authenticate,
    clear_session_cookie,
    create_session,
    destroy_session,
    load_session_user,
    resolve_safe_redirect_target,
    set_user_password,
    users_needing_password,
)
from web import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard", msg: str = "", err: int = 0):
    if not any_user_has_password():
        return RedirectResponse(url="/setup", status_code=303)
    if load_session_user(request.cookies.get(SESSION_COOKIE, "")):
        return RedirectResponse(url=resolve_safe_redirect_target(next), status_code=303)

    return render(
        request,
        "login.html",
        msg,
        err,
        next_path=resolve_safe_redirect_target(next),
        chrome=False,
    )


@router.post("/login")
def sign_in(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next_path: str = Form("/dashboard"),
):
    target = resolve_safe_redirect_target(next_path)
    try:
        user = authenticate(identifier, password)
    except ValueError as exc:
        # Bounce back to the form rather than through the flash-redirect helper, so
        # the attempted password never lands in a URL or the browser's history.
        return render(
            request,
            "login.html",
            str(exc),
            1,
            next_path=target,
            identifier=identifier,
            chrome=False,
        )

    response = RedirectResponse(url=target, status_code=303)
    attach_session_cookie(response, create_session(user["id"]))
    return response


@router.post("/logout")
def sign_out(request: Request):
    destroy_session(request.cookies.get(SESSION_COOKIE, ""))
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, msg: str = "", err: int = 0):
    """First run after auth arrives: existing profiles have no password, so this
    is the only reachable page until one is set."""
    if any_user_has_password():
        return RedirectResponse(url="/login", status_code=303)

    pending = users_needing_password()
    return render(
        request,
        "setup.html",
        msg,
        err,
        pending_users=pending,
        min_length=PASSWORD_MIN_LENGTH,
        chrome=False,
    )


@router.post("/setup")
def complete_setup(
    request: Request,
    user_id: int = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
):
    if any_user_has_password():
        return RedirectResponse(url="/login", status_code=303)

    pending = users_needing_password()
    if not any(int(candidate["id"]) == int(user_id) for candidate in pending):
        return render(
            request, "setup.html", "Choose a profile to secure", 1,
            pending_users=pending, min_length=PASSWORD_MIN_LENGTH, chrome=False,
        )

    try:
        set_user_password(int(user_id), password, confirm)
    except ValueError as exc:
        return render(
            request, "setup.html", str(exc), 1,
            pending_users=pending, min_length=PASSWORD_MIN_LENGTH, chrome=False,
        )

    response = RedirectResponse(url="/dashboard", status_code=303)
    attach_session_cookie(response, create_session(int(user_id)))
    return response
