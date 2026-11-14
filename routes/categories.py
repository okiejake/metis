
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    category_children,
    create_category,
    create_category_rule,
    delete_category,
    delete_category_rule,
    get_connection,
    load_categories,
    load_category_by_id,
    load_category_rules,
    normalize_hex_color,
    parse_optional_date,
    parse_optional_float,
    redirect_with_message,
    set_category_parent,
)
from web import CurrentUser, render

router = APIRouter()


def _parent_id(value: str):
    try:
        return int(value) if str(value).strip() else None
    except ValueError as exc:
        raise ValueError("parent not found") from exc


@router.get("/categories")
def categories_page(request: Request, user: CurrentUser):
    """Retired. Budget is the single hub for budgets and categories."""
    return redirect_with_message("/budget", "")


@router.post("/categories")
def create_category_route(request: Request, user: CurrentUser, name: str = Form(...), color: str = Form(""),
                          parent_id: str = Form("")):
    """Add a category. A sub-category starts with its parent's color, so the
    color field only counts for a top-level one."""
    try:
        parent = _parent_id(parent_id)
        create_category(user["id"], name, None if parent else (color or None), parent)
    except ValueError as exc:
        return redirect_with_message("/budget", str(exc), is_error=True)

    return redirect_with_message("/budget", "Category added")


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_page(request: Request, user: CurrentUser, category_id: int, msg: str = "", err: int = 0):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/budget", "Category not found", is_error=True)

    has_children = bool(category_children(user["id"], category_id))
    parents = [] if has_children else [
        c for c in load_categories(user["id"]) if c["parent_id"] is None and c["id"] != category_id
    ]
    return render(
        request,
        "category_edit.html",
        msg,
        err,
        category=category,
        rules=load_category_rules(user["id"], category_id=category_id),
        parents=parents,
        has_children=has_children,
    )


@router.post("/categories/{category_id}/edit")
def edit_category(request: Request, user: CurrentUser, category_id: int, name: str = Form(...), color: str = Form(...),
                  parent_id: str = Form("")):
    category = load_category_by_id(user["id"], category_id)
    if not category:
        return redirect_with_message("/budget", "Category not found", is_error=True)

    try:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Category name is required")
        normalized_color = normalize_hex_color(color)
        set_category_parent(user["id"], category_id, _parent_id(parent_id))

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


@router.post("/categories/{category_id}/rules")
def add_category_rule(
    request: Request, user: CurrentUser, category_id: int,
    pattern: str = Form(...),
    amount_min: str = Form(""),
    amount_max: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    next: str = Form(""),
):
    """Add a description rule. From category edit it returns there; from the
    rule prompt it returns to the prompt's page."""
    target = next or f"/categories/{category_id}/edit"
    try:
        create_category_rule(
            user["id"], category_id, pattern,
            parse_optional_float(amount_min), parse_optional_float(amount_max),
            date_from=parse_optional_date(date_from), date_to=parse_optional_date(date_to),
        )
    except ValueError as exc:
        return redirect_with_message(target, str(exc), is_error=True)

    return redirect_with_message(target, "rule saved")


@router.post("/categories/{category_id}/rules/{rule_id}/delete")
def remove_category_rule(request: Request, user: CurrentUser, category_id: int, rule_id: int):
    delete_category_rule(user["id"], rule_id)
    return redirect_with_message(f"/categories/{category_id}/edit", "Rule deleted")


@router.post("/categories/{category_id}/delete")
def delete_category_route(request: Request, user: CurrentUser, category_id: int, children: str = Form("")):
    if not load_category_by_id(user["id"], category_id):
        return redirect_with_message("/budget", "Category not found", is_error=True)
    try:
        delete_category(user["id"], category_id, children)
    except ValueError as exc:
        return redirect_with_message(f"/categories/{category_id}/edit", str(exc), is_error=True)
    return redirect_with_message("/budget", "Category deleted")
