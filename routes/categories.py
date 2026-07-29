from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    DEFAULT_CATEGORY_COLOR,
    create_category_rule,
    delete_category_rule,
    get_connection,
    load_categories,
    load_category_by_id,
    load_category_rules,
    normalize_hex_color,
    parse_optional_float,
    redirect_with_message,
    set_category_budget,
)
from web import CurrentUser, render

router = APIRouter()


@router.get("/categories")
def categories_page(request: Request, user: CurrentUser):
    """Retired. Budget is the single hub for budgets and categories."""
    return redirect_with_message("/budget", "")


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
        return redirect_with_message("/budget", str(exc), is_error=True)

    return redirect_with_message("/budget", "Category added")


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_page(request: Request, user: CurrentUser, category_id: int, msg: str = "", err: int = 0):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/budget", "Category not found", is_error=True)

    rules = [rule for rule in load_category_rules(user["id"]) if int(rule["category_id"]) == category_id]
    return render(
        request,
        "category_edit.html",
        msg,
        err,
        category=category,
        rules=rules,
        categories=load_categories(user["id"]),
    )


@router.post("/categories/{category_id}/edit")
def edit_category(request: Request, user: CurrentUser, category_id: int, name: str = Form(...), color: str = Form(...)):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/budget", "Category not found", is_error=True)

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
        return redirect_with_message(f"/categories/{category_id}/edit", str(exc), is_error=True)

    return redirect_with_message("/budget", "Category updated")


@router.post("/categories/{category_id}/budget")
def update_category_budget(
    request: Request, user: CurrentUser, category_id: int, budget_amount: str = Form("")
):
    """Set or clear a category's explicit monthly budget. Blank clears it, which
    hands the category back to a budget derived from its expected items."""
    if not load_category_by_id(user["id"], category_id):
        return redirect_with_message("/budget", "Category not found", is_error=True)

    try:
        amount: Optional[float] = parse_optional_float(budget_amount)
        set_category_budget(user["id"], category_id, amount)
    except ValueError as exc:
        return redirect_with_message("/budget", str(exc), is_error=True)

    return redirect_with_message("/budget", "Budget cleared" if amount is None else "Budget saved")


@router.post("/categories/{category_id}/rules")
def add_category_rule(
    request: Request, user: CurrentUser, category_id: int,
    pattern: str = Form(...),
    amount_min: str = Form(""),
    amount_max: str = Form(""),
):
    try:
        create_category_rule(
            user["id"], category_id, pattern,
            parse_optional_float(amount_min), parse_optional_float(amount_max),
        )
    except ValueError as exc:
        return redirect_with_message(f"/categories/{category_id}/edit", str(exc), is_error=True)

    return redirect_with_message(
        f"/categories/{category_id}/edit", "Rule saved — matching transactions categorized"
    )


@router.post("/categories/{category_id}/rules/{rule_id}/delete")
def remove_category_rule(request: Request, user: CurrentUser, category_id: int, rule_id: int):
    delete_category_rule(user["id"], rule_id)
    return redirect_with_message(f"/categories/{category_id}/edit", "Rule deleted")


@router.post("/categories/{category_id}/delete")
def delete_category(request: Request, user: CurrentUser, category_id: int):
    if not load_category_by_id(user["id"], category_id):
        return redirect_with_message("/budget", "Category not found", is_error=True)

    with get_connection() as conn:
        conn.execute(
            "UPDATE recurring_items SET category_id = NULL WHERE category_id = ? AND user_id = ?",
            [category_id, user["id"]],
        )
        conn.execute(
            "UPDATE manual_transactions SET category_id = NULL WHERE category_id = ? AND user_id = ?",
            [category_id, user["id"]],
        )
        # Anything that pointed imported rows at this category goes with it,
        # otherwise deleted categories leave orphaned splits and rules behind.
        conn.execute(
            "UPDATE imported_transactions SET category_id = NULL, category_via = NULL "
            "WHERE category_id = ? AND user_id = ?",
            [category_id, user["id"]],
        )
        conn.execute("DELETE FROM category_rules WHERE category_id = ? AND user_id = ?", [category_id, user["id"]])
        conn.execute("DELETE FROM transaction_splits WHERE category_id = ? AND user_id = ?", [category_id, user["id"]])
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", [category_id, user["id"]])

    return redirect_with_message("/budget", "Category deleted")
