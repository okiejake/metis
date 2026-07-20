from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    DEFAULT_CATEGORY_COLOR,
    get_connection,
    load_categories_with_usage,
    load_category_by_id,
    normalize_hex_color,
    redirect_with_message,
)
from web import CurrentUser, render

router = APIRouter()


@router.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, user: CurrentUser, msg: str = "", err: int = 0):
    return render(
        request,
        "categories.html",
        msg,
        err,
        categories=load_categories_with_usage(user["id"]),
        default_color=DEFAULT_CATEGORY_COLOR,
    )


@router.post("/categories")
def create_category(request: Request, user: CurrentUser, name: str = Form(...), color: str = Form(DEFAULT_CATEGORY_COLOR)):
    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Category name is required")
        normalized_color = normalize_hex_color(color)

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM categories WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1",
                [user["id"], cleaned_name],
            ).fetchone()
            if existing:
                raise ValueError("A category with this name already exists")
            conn.execute(
                "INSERT INTO categories (name, color, user_id) VALUES (?, ?, ?)",
                [cleaned_name, normalized_color, user["id"]],
            )
    except ValueError as exc:
        return redirect_with_message("/categories", str(exc), is_error=True)

    return redirect_with_message("/categories", "Category added")


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_page(request: Request, user: CurrentUser, category_id: int, msg: str = "", err: int = 0):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/categories", "Category not found", is_error=True)

    return render(
        request,
        "category_edit.html",
        msg,
        err,
        category=category,
    )


@router.post("/categories/{category_id}/edit")
def edit_category(request: Request, user: CurrentUser, category_id: int, name: str = Form(...), color: str = Form(...)):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/categories", "Category not found", is_error=True)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Category name is required")
        normalized_color = normalize_hex_color(color)

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM categories WHERE user_id = ? AND LOWER(name) = LOWER(?) AND id <> ? LIMIT 1",
                [user["id"], cleaned_name, category_id],
            ).fetchone()
            if existing:
                raise ValueError("A category with this name already exists")
            conn.execute(
                "UPDATE categories SET name = ?, color = ? WHERE id = ? AND user_id = ?",
                [cleaned_name, normalized_color, category_id, user["id"]],
            )
    except ValueError as exc:
        target = f"/categories/{category_id}/edit"
        return redirect_with_message(target, str(exc), is_error=True)

    return redirect_with_message("/categories", "Category updated")


@router.post("/categories/{category_id}/delete")
def delete_category(request: Request, user: CurrentUser, category_id: int):
    if not load_category_by_id(user["id"], category_id):
        return redirect_with_message("/categories", "Category not found", is_error=True)

    with get_connection() as conn:
        conn.execute(
            "UPDATE recurring_items SET category_id = NULL WHERE category_id = ? AND user_id = ?",
            [category_id, user["id"]],
        )
        conn.execute(
            "UPDATE manual_transactions SET category_id = NULL WHERE category_id = ? AND user_id = ?",
            [category_id, user["id"]],
        )
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", [category_id, user["id"]])

    return redirect_with_message("/categories", "Category deleted")
