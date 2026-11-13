"""Parts that appear in more than one place are written once, in
templates/_parts.html, and every page uses that one copy.

Reads templates and renders macros; no database.
"""

import re
from pathlib import Path

import pytest

import app  # noqa: F401  registers the template globals and filters
from web import templates

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def parts():
    return templates.env.get_template("_parts.html").module


@pytest.mark.parametrize(
    "marker",
    ['class="category-pill"', 'class="budget-bar-track"', 'class="suggestion-field'],
)
def test_no_template_writes_a_shared_part_by_hand(marker):
    offenders = [
        path.name for path in TEMPLATES.glob("*.html")
        if path.name != "_parts.html" and marker in path.read_text()
    ]
    assert offenders == []


def test_a_pill_without_a_color_uses_the_muted_dot():
    html = str(parts().category_pill("Groceries", ""))

    assert "Groceries" in html and "var(--muted)" in html


def test_the_picker_offers_transfer_and_new_only_when_asked():
    categories = [{"id": 1, "name": "Groceries"}, {"id": 2, "name": "Dining"}]

    plain = str(parts().category_picker("category_id", categories, 2))
    extended = str(parts().category_picker("choice", categories, None, extras=True))

    assert 'value="transfer"' not in plain and 'value="new"' not in plain
    assert re.search(r'<option value="2"\s+selected', plain)
    assert 'value="transfer"' in extended and 'value="new"' in extended
    assert 'name="new_category_name"' in extended


def test_the_bar_draws_fill_and_overflow_or_a_neutral_bar():
    assert "budget-bar-overflow" in str(parts().bar(60, 20))
    assert "budget-bar-neutral" in str(parts().bar(neutral_pct=40))


def test_budget_draws_one_shared_bar_per_category_with_its_title(db, user_id, accounts):
    from datetime import date

    from fastapi.testclient import TestClient

    from conftest import add_imported
    from services import SESSION_COOKIE

    dining = db.create_category(user_id, "Dining")
    db.set_category_budget(user_id, dining, 100.0)
    today = date.today()
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", today, "ACME DINER", -40.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [dining, tx])
    db.save_forecast_window(user_id, today.replace(day=1), today)
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app.app) as client:
        client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        page = client.get("/budget").text

    assert page.count('class="budget-bar-track"') == page.count('class="budget-bar-row') >= 1
    assert 'title="Dining — actual $40.00, budget $' in page
