"""The ledger's filter (v0.5.1): a search of descriptions, bills and notes,
categories (with uncategorized), and a merchant. A filter only hides rows; which
account is shown is not a filter.

All data here is synthetic. Dates are fixed and "today" is passed in.
"""

from datetime import date

import ledger
from conftest import add_bill, add_imported, reconcile

TODAY = date(2026, 7, 10)
START = date(2026, 7, 1)
END = date(2026, 8, 31)


def rows_for(db, user_id, accounts):
    checking = db.load_account_by_id(user_id, accounts["Example Checking"])
    return db.collect_account_transactions(user_id, checking, START, END, today=TODAY)


def view(**params):
    return ledger.LedgerView.from_params(**params)


def shown(db, user_id, accounts, **params):
    rows = rows_for(db, user_id, accounts)
    return [row["description"] for row in ledger.filter_rows(user_id, rows, view(**params), START, END)]


def checking_import(db, user_id, accounts, when, description, amount, **kwargs):
    return add_imported(db, user_id, accounts["Example Checking"], "Example Checking", when, description, amount,
                        **kwargs)


def seed(db, user_id, accounts):
    """A small month: groceries (a child of food), a split hardware run, a bill,
    an uncategorized charge, a transfer and a paycheck, then a statement."""
    food = db.create_category(user_id, "food")
    groceries = db.create_category(user_id, "groceries", parent_id=food)
    home = db.create_category(user_id, "home")
    garden = db.create_category(user_id, "garden")
    grocer = checking_import(db, user_id, accounts, date(2026, 7, 2), "EXAMPLE GROCER 0042", -40.00)
    db.execute("UPDATE imported_transactions SET category_id = ? WHERE id = ?", [groceries, grocer])
    hardware = checking_import(db, user_id, accounts, date(2026, 7, 3), "ACME HARDWARE", -40.00)
    db.save_transaction_splits(user_id, hardware, [{"category_id": home, "amount": 25.00, "note": ""},
                                                   {"category_id": garden, "amount": 15.00, "note": ""}])
    loose = checking_import(db, user_id, accounts, date(2026, 7, 4), "EXAMPLE GIFT SHOP", -30.00)
    db.execute("UPDATE imported_transactions SET note = 'birthday present' WHERE id = ?", [loose])
    checking_import(db, user_id, accounts, date(2026, 7, 5), "EXAMPLE TRANSFER OUT", -100.00, flow="transfer",
                    is_transfer=True)
    checking_import(db, user_id, accounts, date(2026, 7, 6), "EXAMPLE EMPLOYER PAYROLL", 2000.00, flow="income")
    add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 7, 15), account_id=accounts["Example Checking"],
             category_id=home)
    add_imported(db, user_id, accounts["Example Card"], "Example Card", date(2026, 6, 20), "Acme Books", -60.00)
    return {"food": food, "groceries": groceries, "home": home, "garden": garden, "loose": loose}


def test_no_filter_shows_every_row(db, user_id, accounts):
    seed(db, user_id, accounts)
    rows = rows_for(db, user_id, accounts)
    assert ledger.filter_rows(user_id, rows, view(), START, END) == rows


def test_search_matches_part_of_a_description_ignoring_case(db, user_id, accounts):
    seed(db, user_id, accounts)
    assert shown(db, user_id, accounts, q="grocer") == ["EXAMPLE GROCER 0042"]


def test_search_matches_a_bill_name_and_a_note(db, user_id, accounts):
    seed(db, user_id, accounts)
    bill = add_bill(db, user_id, "Example Gym", 30.00, date(2026, 6, 8), account_id=accounts["Example Checking"])
    gym = checking_import(db, user_id, accounts, date(2026, 7, 6), "EXMPL FITNESS 99", -30.00)
    reconcile(db, user_id, "bill", bill, gym)
    assert "EXMPL FITNESS 99" in shown(db, user_id, accounts, q="gym")
    assert shown(db, user_id, accounts, q="birthday") == ["EXAMPLE GIFT SHOP"]


def test_a_category_matches_imported_rows_by_category_and_forecast_rows_by_their_bill(db, user_id, accounts):
    ids = seed(db, user_id, accounts)
    assert shown(db, user_id, accounts, category=[str(ids["home"])]) == ["ACME HARDWARE", "Acme Utilities", "Acme Utilities"]


def test_picking_a_parent_includes_its_children(db, user_id, accounts):
    ids = seed(db, user_id, accounts)
    assert shown(db, user_id, accounts, category=[str(ids["food"])]) == ["EXAMPLE GROCER 0042"]


def test_a_split_row_matches_any_of_its_parts(db, user_id, accounts):
    ids = seed(db, user_id, accounts)
    assert shown(db, user_id, accounts, category=[str(ids["garden"])]) == ["ACME HARDWARE"]


def test_uncategorized_means_imported_spending_with_no_category(db, user_id, accounts):
    seed(db, user_id, accounts)
    assert shown(db, user_id, accounts, category=["uncategorized"]) == ["EXAMPLE GIFT SHOP"]


def test_statements_show_only_under_all(db, user_id, accounts):
    ids = seed(db, user_id, accounts)
    assert not [d for d in shown(db, user_id, accounts, category=[str(ids["home"]), "uncategorized"])
                if "statement" in d]
    assert [d for d in shown(db, user_id, accounts, q="statement") if "statement" in d]


def test_all_among_the_picks_means_every_category(db, user_id, accounts):
    ids = seed(db, user_id, accounts)
    rows = rows_for(db, user_id, accounts)
    picks = view(category=["all", str(ids["home"])])
    assert ledger.filter_rows(user_id, rows, picks, START, END) == rows
    assert not picks.filtered


def test_a_merchant_filter_shows_only_that_merchants_imported_rows(db, user_id, accounts):
    seed(db, user_id, accounts)
    token = ledger.merchant_token("EXAMPLE TRANSFER OUT")
    assert shown(db, user_id, accounts, merchant=token) == ["EXAMPLE TRANSFER OUT"]


def test_choosing_an_account_alone_is_not_a_filter(db, user_id, accounts):
    assert not view(account=str(accounts["Example Card"])).filtered
    assert view(q="acme").filtered


def test_the_view_writes_its_filters_into_the_address_and_reads_them_back(db, user_id, accounts):
    original = view(account="7", q="acme hardware", category=["3", "uncategorized"], merchant="abc123")
    query = original.query()
    assert "account=7" in query and "q=acme+hardware" in query and "merchant=abc123" in query
    from urllib.parse import parse_qs
    params = parse_qs(query)
    again = view(account=params["account"][0], q=params["q"][0], category=params["category"],
                 merchant=params["merchant"][0])
    assert again == original


def test_removing_one_filter_keeps_the_others(db, user_id, accounts):
    current = view(account="7", q="acme", category=["3"])
    chips = {chip["label"]: chip["url"] for chip in current.chips({3: "home"})}
    assert "q=" not in chips["search: acme"] and "category=3" in chips["search: acme"]
    assert "category=" not in chips["home"] and "q=acme" in chips["home"]
