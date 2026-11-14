"""Category rules: one table and one list for description rules and the
merchant rules the inbox makes (#103, #100's rule targets). Both honor amount
bounds and a date range; a merchant rule beats a description rule; a category
picked by hand is never changed; rule-set categories are worked out again from
every rule whenever one changes.

All data here is synthetic.
"""

from datetime import date

import pytest

import inbox
from conftest import add_imported
from merchants import merchant_key


@pytest.fixture
def card(accounts):
    return accounts["Example Card"]


@pytest.fixture
def checking(accounts):
    return accounts["Example Checking"]


@pytest.fixture
def groceries(db, user_id):
    return db.create_category(user_id, "Groceries")


@pytest.fixture
def dining(db, user_id):
    return db.create_category(user_id, "Dining")


def spend(db, user_id, account, day, description, amount=-25.0, month=6):
    name = "Example Card" if account else ""
    return add_imported(db, user_id, account, name, date(2026, month, day), description, amount)


def category_of(db, tx_id):
    return db.fetch_one(
        "SELECT category_id, category_via, category_rule_id FROM imported_transactions WHERE id = ?", [tx_id]
    )


def test_a_description_rule_categorizes_rows_whose_description_contains_it(db, user_id, card, groceries):
    hit = spend(db, user_id, card, 1, "ACME MARKET 0042")
    miss = spend(db, user_id, card, 2, "EXAMPLE FUEL")

    rule = db.create_category_rule(user_id, groceries, "acme market")

    assert category_of(db, hit) == (groceries, "rule", rule)
    assert category_of(db, miss) == (None, None, None)


def test_a_merchant_rule_matches_the_merchant_key_exactly(db, user_id, card, groceries):
    store_one = spend(db, user_id, card, 1, "ACME GROCER #1")
    store_two = spend(db, user_id, card, 2, "ACME GROCER #22")
    other = spend(db, user_id, card, 3, "ACME GROCER OUTLET")

    rule = db.create_category_rule(user_id, groceries, merchant_key("ACME GROCER #1"), match_on="merchant")

    assert category_of(db, store_one) == (groceries, "rule", rule)
    assert category_of(db, store_two) == (groceries, "rule", rule)
    assert category_of(db, other) == (None, None, None)


def test_a_merchant_rule_matches_only_spending(db, user_id, checking, groceries):
    deposit = add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 1), "ACME GROCER", 30.0, "income")
    moved = add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 2), "ACME GROCER", -30.0,
                         is_transfer=True)
    bought = add_imported(db, user_id, checking, "Example Checking", date(2026, 6, 3), "ACME GROCER", -30.0)

    db.create_category_rule(user_id, groceries, "ACME GROCER", match_on="merchant")

    assert category_of(db, deposit)[0] is None
    assert category_of(db, moved)[0] is None
    assert category_of(db, bought)[0] == groceries


@pytest.mark.parametrize("match_on,pattern", [("description", "ACME"), ("merchant", "ACME DINER")])
def test_both_kinds_honor_amount_bounds_and_an_empty_bound_is_open(db, user_id, card, dining, match_on, pattern):
    small = spend(db, user_id, card, 1, "ACME DINER", -9.0)
    edge = spend(db, user_id, card, 2, "ACME DINER", -10.0)
    large = spend(db, user_id, card, 3, "ACME DINER", -500.0)

    db.create_category_rule(user_id, dining, pattern, amount_min=10.0, match_on=match_on)

    assert [category_of(db, tx)[0] for tx in (small, edge, large)] == [None, dining, dining]


@pytest.mark.parametrize("match_on,pattern", [("description", "ACME"), ("merchant", "ACME DINER")])
def test_both_kinds_honor_a_date_range(db, user_id, card, dining, match_on, pattern):
    before = spend(db, user_id, card, 30, "ACME DINER", month=4)
    first = spend(db, user_id, card, 1, "ACME DINER", month=5)
    last = spend(db, user_id, card, 31, "ACME DINER", month=5)
    after = spend(db, user_id, card, 1, "ACME DINER", month=6)

    db.create_category_rule(user_id, dining, pattern, match_on=match_on,
                            date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))

    assert [category_of(db, tx)[0] for tx in (before, first, last, after)] == [None, dining, dining, None]


def test_a_merchant_rule_beats_a_description_rule(db, user_id, card, groceries, dining):
    tx = spend(db, user_id, card, 1, "ACME DINER #4")
    db.create_category_rule(user_id, dining, "ACME DINER", match_on="merchant")

    db.create_category_rule(user_id, groceries, "ACME")

    assert category_of(db, tx)[0] == dining


def test_among_description_rules_the_newest_wins(db, user_id, card, groceries, dining):
    tx = spend(db, user_id, card, 1, "ACME DINER MARKET")
    db.create_category_rule(user_id, groceries, "MARKET")

    newest = db.create_category_rule(user_id, dining, "DINER")

    assert category_of(db, tx) == (dining, "rule", newest)


def test_a_category_picked_by_hand_is_never_changed(db, user_id, card, groceries, dining):
    tx = spend(db, user_id, card, 1, "ACME GROCER")
    inbox.set_charge(user_id, tx, dining)

    db.create_category_rule(user_id, groceries, "ACME GROCER", match_on="merchant")
    db.create_category_rule(user_id, groceries, "ACME")

    assert category_of(db, tx) == (dining, None, None)


def test_changing_a_rule_releases_rows_it_no_longer_matches(db, user_id, card, groceries):
    tx = spend(db, user_id, card, 1, "ACME MARKET")
    rule = db.create_category_rule(user_id, groceries, "ACME")

    db.update_category_rule(user_id, rule, groceries, "EXAMPLE")

    assert category_of(db, tx) == (None, None, None)


def test_deleting_a_rule_hands_its_rows_to_the_next_rule_that_matches(db, user_id, card, groceries, dining):
    tx = spend(db, user_id, card, 1, "ACME DINER")
    db.create_category_rule(user_id, groceries, "ACME")
    merchant = db.create_category_rule(user_id, dining, "ACME DINER", match_on="merchant")

    db.delete_category_rule(user_id, merchant)

    assert category_of(db, tx)[0] == groceries


def test_rows_imported_later_are_categorized_on_resync(db, user_id, card, groceries):
    db.create_category_rule(user_id, groceries, "ACME GROCER", match_on="merchant")
    later = spend(db, user_id, card, 20, "ACME GROCER #9")

    changed = db.resync_category_rules(user_id)

    assert changed == 1 and category_of(db, later)[0] == groceries
    assert db.resync_category_rules(user_id) == 0


def test_a_rule_can_target_a_sub_category(db, user_id, card):
    food = db.create_category(user_id, "Food")
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    tx = spend(db, user_id, card, 1, "ACME GROCER")

    db.create_category_rule(user_id, groceries, "ACME GROCER")

    assert category_of(db, tx)[0] == groceries


def test_a_rule_needs_a_pattern_a_known_kind_and_ordered_bounds(db, user_id, groceries):
    with pytest.raises(ValueError):
        db.create_category_rule(user_id, groceries, "  ")
    with pytest.raises(ValueError):
        db.create_category_rule(user_id, groceries, "ACME", match_on="memo")
    with pytest.raises(ValueError):
        db.create_category_rule(user_id, groceries, "ACME", amount_min=20.0, amount_max=10.0)
    with pytest.raises(ValueError):
        db.create_category_rule(user_id, groceries, "ACME", date_from=date(2026, 6, 2), date_to=date(2026, 6, 1))


def test_the_rules_of_a_category_list_what_they_match_on_amount_and_dates(db, user_id, groceries, dining):
    db.create_category_rule(user_id, groceries, "ACME", amount_max=50.0, date_from=date(2026, 1, 1))
    db.create_category_rule(user_id, groceries, "ACME GROCER", match_on="merchant")
    db.create_category_rule(user_id, dining, "DINER")

    rules = db.load_category_rules(user_id, category_id=groceries)

    assert [(r["match_on"], r["pattern"]) for r in rules] == [("merchant", "ACME GROCER"), ("description", "ACME")]
    assert rules[1]["amount_max"] == 50.0 and rules[1]["date_from"] == date(2026, 1, 1) and rules[1]["date_to"] is None


# --- The inbox makes merchant rules ---------------------------------------------


def test_confirming_a_merchant_in_the_inbox_creates_a_merchant_rule(db, user_id, card, groceries):
    first = spend(db, user_id, card, 1, "ACME GROCER #1")
    second = spend(db, user_id, card, 2, "ACME GROCER #2")

    inbox.confirm_category(user_id, merchant_key("ACME GROCER #1"), groceries)

    rules = db.load_category_rules(user_id, category_id=groceries)
    assert [(r["match_on"], r["pattern"]) for r in rules] == [("merchant", "ACME GROCER")]
    assert category_of(db, first) == category_of(db, second) == (groceries, "rule", rules[0]["id"])


def test_undoing_a_merchant_confirm_deletes_the_rule_and_clears_its_rows(db, user_id, card, groceries):
    tx = spend(db, user_id, card, 1, "ACME GROCER #1")
    action = inbox.confirm_category(user_id, merchant_key("ACME GROCER #1"), groceries)
    later = spend(db, user_id, card, 20, "ACME GROCER #9")
    db.resync_category_rules(user_id)

    inbox.undo(user_id, action)

    assert db.load_category_rules(user_id) == []
    assert category_of(db, tx) == category_of(db, later) == (None, None, None)


def test_deleting_a_category_deletes_its_rules_and_frees_their_rows(db, user_id, card, groceries):
    tx = spend(db, user_id, card, 1, "ACME GROCER #1")
    inbox.confirm_category(user_id, merchant_key("ACME GROCER #1"), groceries)

    db.delete_category(user_id, groceries)

    assert db.load_category_rules(user_id) == []
    assert category_of(db, tx) == (None, None, None)
