from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services import (
    create_user_record,
    delete_user_and_data,
    get_connection,
    get_current_user,
    get_default_user,
    load_all_users,
    load_user_by_id,
    load_user_by_slug,
    normalize_user_email,
    normalize_user_slug,
    redirect_with_message,
    resolve_safe_redirect_target,
)
from web import CurrentUser, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> RedirectResponse:
    get_current_user(request)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/users")
def create_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    next_path: str = Form("/dashboard"),
):
    try:
        chosen_slug = create_user_record(display_name, email)
    except ValueError as exc:
        return redirect_with_message(resolve_safe_redirect_target(next_path), str(exc), is_error=True)

    created_user = load_user_by_slug(chosen_slug)
    if created_user:
        request.state.current_user = created_user
    request.state.current_user_slug = chosen_slug
    return redirect_with_message(
        resolve_safe_redirect_target(next_path),
        f"User '{(display_name or '').strip()}' created",
    )


@router.post("/users/switch")
def switch_user(request: Request, user_slug: str = Form(...), next_path: str = Form("/dashboard")):
    try:
        normalized_slug = normalize_user_slug(user_slug)
    except ValueError:
        return redirect_with_message(resolve_safe_redirect_target(next_path), "Invalid user selection", is_error=True)

    user = load_user_by_slug(normalized_slug)
    if not user:
        return redirect_with_message(resolve_safe_redirect_target(next_path), "User not found", is_error=True)

    request.state.current_user = user
    request.state.current_user_slug = user["slug"]
    return redirect_with_message(resolve_safe_redirect_target(next_path), f"Switched to {user['display_name']}")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: CurrentUser, section: str = "user-management", msg: str = "", err: int = 0):
    selected_section = section if section == "user-management" else "user-management"
    return render(
        request,
        "settings.html",
        msg,
        err,
        section=selected_section,
        managed_users=load_all_users(),
        personal_user=user,
    )


@router.post("/settings/personal")
def update_personal_settings(
    request: Request, user: CurrentUser,
    display_name: str = Form(...),
    email: str = Form(""),
):
    try:
        cleaned_name = display_name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")
        normalized_email = normalize_user_email(email)
        with get_connection() as conn:
            conn.execute(
                "UPDATE users SET display_name = ?, email = ? WHERE id = ?",
                [cleaned_name, normalized_email, user["id"]],
            )
    except ValueError as exc:
        return redirect_with_message("/settings?section=user-management", str(exc), is_error=True)

    updated_user = load_user_by_id(user["id"])
    if updated_user:
        request.state.current_user = updated_user
        request.state.current_user_slug = updated_user["slug"]
    return redirect_with_message("/settings?section=user-management", "Personal details updated")


@router.post("/settings/personal/passkey")
def add_passkey_placeholder():
    return redirect_with_message(
        "/settings?section=user-management",
        "Passkey support is coming soon",
    )


@router.post("/settings/users")
def admin_create_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
):
    try:
        create_user_record(display_name, email)
    except ValueError as exc:
        return redirect_with_message("/settings?section=user-management", str(exc), is_error=True)
    return redirect_with_message("/settings?section=user-management", "User added")


@router.post("/settings/users/{user_id}/edit")
def admin_edit_user(
    user_id: int,
    display_name: str = Form(...),
    email: str = Form(""),
):
    target_user = load_user_by_id(user_id)
    if not target_user:
        return redirect_with_message("/settings?section=user-management", "User not found", is_error=True)

    try:
        cleaned_name = display_name.strip()
        if not cleaned_name:
            raise ValueError("Name is required")
        normalized_email = normalize_user_email(email)
        with get_connection() as conn:
            conn.execute(
                "UPDATE users SET display_name = ?, email = ? WHERE id = ?",
                [cleaned_name, normalized_email, user_id],
            )
    except ValueError as exc:
        return redirect_with_message("/settings?section=user-management", str(exc), is_error=True)

    return redirect_with_message("/settings?section=user-management", "User updated")


@router.post("/settings/users/{user_id}/delete")
def admin_delete_user(
    request: Request,
    user_id: int,
    confirm_name: str = Form(""),
):
    target_user = load_user_by_id(user_id)
    if not target_user:
        return redirect_with_message("/settings?section=user-management", "User not found", is_error=True)

    expected_name = target_user["display_name"]
    if confirm_name.strip() != expected_name:
        return redirect_with_message(
            "/settings?section=user-management",
            f"Delete confirmation failed. Please type '{expected_name}' exactly.",
            is_error=True,
        )

    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    user_count = int(row[0]) if row else 0
    if user_count <= 1:
        return redirect_with_message(
            "/settings?section=user-management",
            "At least one user must remain",
            is_error=True,
        )

    active_user = get_current_user(request)
    delete_user_and_data(user_id)

    if active_user["id"] == user_id:
        fallback_user = get_default_user()
        request.state.current_user = fallback_user
        request.state.current_user_slug = fallback_user["slug"]
        return redirect_with_message(
            "/settings?section=user-management",
            f"User deleted. Switched to {fallback_user['display_name']}.",
        )

    return redirect_with_message("/settings?section=user-management", "User and all data deleted")
