"""Sub-categories, one level deep (#100). A sub-category reads with its parent
(`food › groceries`), starts with its parent's color, can be moved or promoted,
and deleting a parent asks what happens to its children.

All data here is synthetic.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_bill, add_imported
from services import SESSION_COOKIE


@pytest.fixture
def client(db, user_id):
    db.set_user_password(user_id, "synthetic-password-for-tests")
    with TestClient(app_module.app) as test_client:
        test_client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        yield test_client


@pytest.fixture
def food(db, user_id):
    return db.create_category(user_id, "Food", "#22c55e")


def parent_of(db, category_id):
    return db.fetch_one("SELECT parent_id FROM categories WHERE id = ?", [category_id])[0]


def test_existing_categories_are_top_level(db, user_id, food):
    assert parent_of(db, food) is None


def test_a_sub_category_starts_with_its_parents_color(db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    assert parent_of(db, groceries) == food
    assert db.load_category_by_id(user_id, groceries)["color"] == "#22c55e"


def test_an_explicit_color_still_wins(db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", "#ef4444", parent_id=food)

    assert db.load_category_by_id(user_id, groceries)["color"] == "#ef4444"


def test_only_one_level(db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    with pytest.raises(ValueError):
        db.create_category(user_id, "Produce", parent_id=groceries)
    with pytest.raises(ValueError):
        db.set_category_parent(user_id, food, db.create_category(user_id, "Household"))


def test_a_category_cannot_be_its_own_parent_or_take_another_users(db, user_id, food):
    with pytest.raises(ValueError):
        db.set_category_parent(user_id, food, food)
    db.execute("INSERT INTO users (slug, display_name) VALUES ('other', 'Other')")
    other_user = db.fetch_one("SELECT id FROM users WHERE slug = 'other'")[0]
    theirs = db.create_category(other_user, "Theirs")
    with pytest.raises(ValueError):
        db.create_category(user_id, "Groceries", parent_id=theirs)


def test_names_stay_unique_per_user_sub_categories_included(db, user_id, food):
    db.create_category(user_id, "Groceries", parent_id=food)

    with pytest.raises(ValueError):
        db.create_category(user_id, "groceries")


def test_categories_load_with_their_label_parents_first(db, user_id, food):
    db.create_category(user_id, "Groceries", parent_id=food)
    db.create_category(user_id, "Dining", parent_id=food)
    db.create_category(user_id, "Auto")

    loaded = db.load_categories(user_id)

    assert [c["label"] for c in loaded] == ["Auto", "Food", "Food › Dining", "Food › Groceries"]
    assert [c["parent_id"] for c in loaded][:2] == [None, None]


def test_a_sub_categorys_pill_reads_with_its_parent_wherever_it_shows(db, user_id, accounts, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    checking = accounts["Example Checking"]
    tx = add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 1), "ACME GROCER", -20.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [groceries, tx])
    add_bill(db, user_id, "Produce box", 30.0, date(2026, 6, 5), checking, category_id=groceries)

    ledger = db.load_actual_checking_transactions(user_id, date(2026, 6, 1), date(2026, 6, 30))
    parts = db.spending_parts(user_id, date(2026, 6, 1), date(2026, 6, 30))
    bills = db.load_all_bills(user_id)

    assert ledger[0]["category_name"] == "Food › Groceries"
    assert parts[0]["category_name"] == "Food › Groceries"
    assert bills[0]["category_name"] == "Food › Groceries"
    assert db.load_imported_transaction_by_id(user_id, tx)["category_name"] == "Food › Groceries"


def test_editing_moves_or_promotes_a_sub_category(client, db, user_id, food):
    home = db.create_category(user_id, "Home")
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    client.post(f"/categories/{groceries}/edit", data={"name": "Groceries", "color": "#22c55e", "parent_id": str(home)})
    assert parent_of(db, groceries) == home

    client.post(f"/categories/{groceries}/edit", data={"name": "Groceries", "color": "#22c55e", "parent_id": ""})
    assert parent_of(db, groceries) is None


def test_the_edit_page_offers_only_top_level_parents(client, db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    html = client.get(f"/categories/{groceries}/edit").text

    assert 'name="parent_id"' in html
    assert f'<option value="{food}" selected' in html
    assert f'value="{groceries}"' not in html.split('name="parent_id"')[1].split("</select>")[0]


def test_adding_from_budget_can_name_a_parent(client, db, user_id, food):
    client.post("/categories", data={"name": "Groceries", "parent_id": str(food)})

    groceries = db.fetch_one("SELECT id FROM categories WHERE name = 'Groceries'")[0]
    assert parent_of(db, groceries) == food
    assert db.load_category_by_id(user_id, groceries)["color"] == "#22c55e"


def test_deleting_a_parent_with_children_needs_a_choice(client, db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    response = client.post(f"/categories/{food}/delete", follow_redirects=False)

    assert "err=1" in response.headers["location"]
    assert db.load_category_by_id(user_id, food) is not None
    assert parent_of(db, groceries) == food


def test_deleting_a_parent_can_move_its_children_to_top_level(client, db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    client.post(f"/categories/{food}/delete", data={"children": "move"})

    assert db.load_category_by_id(user_id, food) is None
    assert parent_of(db, groceries) is None


def test_deleting_a_parent_can_delete_its_children_too(client, db, user_id, accounts, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    tx = add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 1), "ACME GROCER", -20.0)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [groceries, tx])

    client.post(f"/categories/{food}/delete", data={"children": "delete"})

    assert db.load_category_by_id(user_id, food) is None
    assert db.load_category_by_id(user_id, groceries) is None
    assert db.fetch_one("SELECT category_id FROM imported_transactions WHERE id = ?", [tx])[0] is None


def test_deleting_a_sub_category_leaves_its_parent(client, db, user_id, food):
    groceries = db.create_category(user_id, "Groceries", parent_id=food)

    client.post(f"/categories/{groceries}/delete")

    assert db.load_category_by_id(user_id, groceries) is None
    assert db.load_category_by_id(user_id, food) is not None
