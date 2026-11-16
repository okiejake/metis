"""The ledger shows one account at a time (v0.5.1). One builder serves every
account: the account's own imports up to its last imported date, then its
forecast. A checking account's forecast holds its bills and one-time items and
a row for each statement of every card it pays; a card's holds its expected
charges and the payments coming in from the account that pays it.

All data here is synthetic. Dates are fixed and "today" is passed in, so the
clock never decides which statements have closed. The fixture card closes on
the 5th and is due on the 25th.
"""

from datetime import date

from conftest import add_bill, add_imported, add_one_time, reconcile

TODAY = date(2026, 7, 10)
START = date(2026, 7, 1)
END = date(2026, 8, 31)
CUTOVER = date(2026, 7, 6)


def ledger(db, user_id, account_id, start=START, end=END):
    account = db.load_account_by_id(user_id, account_id) if account_id else None
    return db.collect_account_transactions(user_id, account, start, end, today=TODAY)


def add_checking(db, user_id, name="Example Second Checking"):
    row = db.fetch_one("INSERT INTO accounts (user_id, name, account_type, payment_pattern) "
                       "VALUES (?, ?, 'checking', '') RETURNING id", [user_id, name])
    return int(row[0])


def checking_import(db, user_id, accounts, when, description, amount, **kwargs):
    return add_imported(db, user_id, accounts["Example Checking"], "Example Checking", when, description, amount,
                        **kwargs)


def card_import(db, user_id, accounts, when, description, amount, **kwargs):
    return add_imported(db, user_id, accounts["Example Card"], "Example Card", when, description, amount, **kwargs)


def set_cutover(db, user_id, accounts, when=CUTOVER):
    """An unrelated checking import that fixes the checking account's cutover."""
    return checking_import(db, user_id, accounts, when, "Example Coffee", -5.00)


def charge_the_june_statement(db, user_id, accounts, amount=-400.00):
    """A charge in the cycle that closes on July 5, due July 25."""
    return card_import(db, user_id, accounts, date(2026, 6, 20), "Acme Hardware", amount)


def descriptions(rows):
    return [row["description"] for row in rows]


def of_type(rows, kind):
    return [row for row in rows if row["type"] == kind]


# --- One account at a time ---------------------------------------------------------


def test_each_checking_account_shows_only_its_own_imports(db, user_id, accounts):
    second = add_checking(db, user_id)
    checking_import(db, user_id, accounts, date(2026, 7, 2), "Example Grocer", -40.00)
    add_imported(db, user_id, second, "Example Second Checking", date(2026, 7, 3), "Example Bakery", -12.00)

    assert "Example Grocer" in descriptions(ledger(db, user_id, accounts["Example Checking"]))
    assert "Example Bakery" not in descriptions(ledger(db, user_id, accounts["Example Checking"]))
    assert "Example Bakery" in descriptions(ledger(db, user_id, second))
    assert "Example Grocer" not in descriptions(ledger(db, user_id, second))


def test_a_second_checking_accounts_imports_stay_off_the_dashboards_ledger(db, user_id, accounts):
    second = add_checking(db, user_id)
    add_imported(db, user_id, second, "Example Second Checking", date(2026, 7, 3), "Example Bakery", -12.00)
    assert "Example Bakery" not in descriptions(db.collect_blended_transactions(user_id, START, END))


def test_a_bill_shows_on_the_account_it_is_paid_from_only(db, user_id, accounts):
    second = add_checking(db, user_id)
    set_cutover(db, user_id, accounts)
    add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 7, 15), account_id=second)

    assert "Acme Utilities" in descriptions(ledger(db, user_id, second))
    assert "Acme Utilities" not in descriptions(ledger(db, user_id, accounts["Example Checking"]))


def test_a_bill_with_no_account_shows_on_the_default_checking_account_only(db, user_id, accounts):
    second = add_checking(db, user_id)
    set_cutover(db, user_id, accounts)
    add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 7, 15))

    assert "Acme Utilities" in descriptions(ledger(db, user_id, accounts["Example Checking"]))
    assert "Acme Utilities" not in descriptions(ledger(db, user_id, second))


def test_a_bill_charged_to_a_card_stays_off_the_checking_ledger(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    add_bill(db, user_id, "Acme Streaming", 15.00, date(2026, 7, 15), account_id=accounts["Example Card"])
    assert "Acme Streaming" not in descriptions(ledger(db, user_id, accounts["Example Checking"]))


# --- Where each account's balance starts --------------------------------------------


def test_the_default_checking_account_starts_from_the_starting_balance(db, user_id, accounts):
    db.set_setting_float(user_id, "starting_balance", 1000.00)
    checking_import(db, user_id, accounts, date(2026, 6, 15), "Example Grocer", -50.00)
    checking = db.load_account_by_id(user_id, accounts["Example Checking"])
    assert db.account_balance_before(user_id, checking, START) == 950.00


def test_another_checking_account_starts_from_zero(db, user_id, accounts):
    db.set_setting_float(user_id, "starting_balance", 1000.00)
    second = add_checking(db, user_id)
    add_imported(db, user_id, second, "Example Second Checking", date(2026, 6, 15), "Example Bakery", -50.00)
    assert db.account_balance_before(user_id, db.load_account_by_id(user_id, second), START) == -50.00


def test_a_card_balance_is_negative_while_money_is_owed(db, user_id, accounts):
    db.set_setting_float(user_id, "starting_balance", 1000.00)
    charge_the_june_statement(db, user_id, accounts, -300.00)
    card = db.load_account_by_id(user_id, accounts["Example Card"])
    assert db.account_balance_before(user_id, card, START) == -300.00


# --- Statements show on the account that pays the card -------------------------------


def test_a_statement_shows_on_the_account_that_pays_the_card(db, user_id, accounts):
    second = add_checking(db, user_id)
    db.execute("UPDATE accounts SET paid_from_account_id = ? WHERE id = ?", [second, accounts["Example Card"]])
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)

    assert of_type(ledger(db, user_id, accounts["Example Checking"]), "card") == []
    assert [row["date"] for row in of_type(ledger(db, user_id, second), "card")][0] == date(2026, 7, 25)


def test_a_statement_paid_in_full_still_shows_for_nothing(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)
    card_import(db, user_id, accounts, date(2026, 7, 6), "Example Card Payment", 400.00, flow="transfer",
                is_transfer=True)

    row = [r for r in of_type(ledger(db, user_id, accounts["Example Checking"]), "card")
           if r["date"] == date(2026, 7, 25)][0]
    assert (row["expense"], row["delta"]) == (0.0, 0.0)
    assert row["statement"]["status"] == "paid"


def test_an_unpaid_statement_shows_what_is_left(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)

    row = [r for r in of_type(ledger(db, user_id, accounts["Example Checking"]), "card")
           if r["date"] == date(2026, 7, 25)][0]
    assert (row["expense"], row["delta"]) == (400.00, -400.00)


def test_a_statement_due_on_or_before_the_cutover_does_not_show(db, user_id, accounts):
    set_cutover(db, user_id, accounts, date(2026, 7, 28))
    charge_the_june_statement(db, user_id, accounts)
    due_dates = [row["date"] for row in of_type(ledger(db, user_id, accounts["Example Checking"]), "card")]
    assert date(2026, 7, 25) not in due_dates
    assert date(2026, 8, 25) in due_dates


# --- What each row says ----------------------------------------------------------------


def test_an_imported_row_shows_the_bank_text_not_the_merchant_name(db, user_id, accounts):
    tx = checking_import(db, user_id, accounts, date(2026, 7, 2), "EXAMPLE GROCER 0042 POS", -40.00)
    db.execute("UPDATE imported_transactions SET merchant = 'Example Grocer' WHERE id = ?", [tx])
    assert "EXAMPLE GROCER 0042 POS" in descriptions(ledger(db, user_id, accounts["Example Checking"]))


def test_a_matched_row_names_its_bill_and_keeps_the_bank_text(db, user_id, accounts):
    bill = add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 6, 2), account_id=accounts["Example Checking"])
    tx = checking_import(db, user_id, accounts, date(2026, 7, 2), "ACME UTIL AUTOPAY", -90.00)
    reconcile(db, user_id, "bill", bill, tx)

    row = [r for r in ledger(db, user_id, accounts["Example Checking"]) if r.get("id") == tx and r["type"] == "actual"][0]
    assert (row["description"], row["bill"]) == ("ACME UTIL AUTOPAY", "Acme Utilities")


def test_each_row_says_its_type_and_only_imported_rows_name_a_bill(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)
    add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 7, 15), account_id=accounts["Example Checking"])
    add_bill(db, user_id, "Example Employer", 2000.00, date(2026, 7, 17), account_id=accounts["Example Checking"],
             kind="income")
    add_one_time(db, user_id, "Example Repair", 250.00, date(2026, 7, 20), account_id=accounts["Example Checking"])

    rows = ledger(db, user_id, accounts["Example Checking"])
    types = {row["description"]: row["type"] for row in rows}
    assert types["Example Coffee"] == "actual"
    assert types["Acme Utilities"] == "bill"
    assert types["Example Employer"] == "income"
    assert types["Example Repair"] == "one-time"
    assert types["Example Card statement"] == "card"
    bills = {row["description"]: row["bill"] for row in rows if row["type"] not in ("actual", "card")}
    assert bills == {"Acme Utilities": "Acme Utilities", "Example Employer": "Example Employer",
                     "Example Repair": "Example Repair"}
    assert all(row["bill"] == "" for row in rows if row["type"] == "card")


def test_the_line_on_an_imported_row_follows_its_state(db, user_id, accounts):
    bill = add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 6, 2), account_id=accounts["Example Checking"])
    matched = checking_import(db, user_id, accounts, date(2026, 7, 2), "ACME UTIL AUTOPAY", -90.00)
    reconcile(db, user_id, "bill", bill, matched)
    flagged = checking_import(db, user_id, accounts, date(2026, 7, 3), "Example Gift Shop", -30.00)
    db.execute("UPDATE imported_transactions SET unexpected = TRUE WHERE id = ?", [flagged])
    moved = checking_import(db, user_id, accounts, date(2026, 7, 4), "Example Transfer Out", -100.00,
                            flow="transfer", is_transfer=True)
    loose = checking_import(db, user_id, accounts, date(2026, 7, 5), "Example Grocer", -40.00)

    lines = {row["id"]: row["line"] for row in ledger(db, user_id, accounts["Example Checking"]) if row["type"] == "actual"}
    assert lines == {matched: "matched", flagged: "unexpected", moved: None, loose: "unsettled"}


def test_a_split_row_lists_its_parts(db, user_id, accounts):
    home = db.create_category(user_id, "home")
    garden = db.create_category(user_id, "garden")
    tx = checking_import(db, user_id, accounts, date(2026, 7, 2), "Acme Hardware", -40.00)
    db.save_transaction_splits(user_id, tx, [{"category_id": home, "amount": 25.00, "note": ""},
                                             {"category_id": garden, "amount": 15.00, "note": ""}])

    row = [r for r in ledger(db, user_id, accounts["Example Checking"]) if r.get("id") == tx][0]
    assert [part["category_id"] for part in row["splits"]] == [home, garden]


def test_an_imported_row_carries_its_category_id_and_note(db, user_id, accounts):
    home = db.create_category(user_id, "home")
    tx = checking_import(db, user_id, accounts, date(2026, 7, 2), "Acme Hardware", -40.00)
    db.execute("UPDATE imported_transactions SET category_id = ?, note = 'gift for a friend' WHERE id = ?", [home, tx])

    row = [r for r in ledger(db, user_id, accounts["Example Checking"]) if r.get("id") == tx][0]
    assert (row["category_id"], row["note"]) == (home, "gift for a friend")


def test_a_forecast_row_carries_its_bills_category_id(db, user_id, accounts):
    home = db.create_category(user_id, "home")
    set_cutover(db, user_id, accounts)
    add_bill(db, user_id, "Acme Utilities", 90.00, date(2026, 7, 15), account_id=accounts["Example Checking"],
             category_id=home)
    row = [r for r in ledger(db, user_id, accounts["Example Checking"]) if r["description"] == "Acme Utilities"][0]
    assert row["category_id"] == home


# --- A card's own ledger ----------------------------------------------------------------


def test_a_card_ledger_lists_its_own_imports_and_no_checking_rows(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)
    card_import(db, user_id, accounts, date(2026, 7, 3), "Acme Books", -60.00)

    rows = ledger(db, user_id, accounts["Example Card"])
    assert "Acme Books" in descriptions(rows)
    assert "Example Coffee" not in descriptions(rows)


def test_a_card_ledger_forecasts_its_expected_charges_after_its_last_import(db, user_id, accounts):
    card_import(db, user_id, accounts, date(2026, 7, 8), "Acme Books", -60.00)
    add_bill(db, user_id, "Acme Streaming", 15.00, date(2026, 6, 4), account_id=accounts["Example Card"])

    streaming = [r for r in ledger(db, user_id, accounts["Example Card"]) if r["description"] == "Acme Streaming"]
    assert [r["date"] for r in streaming] == [date(2026, 8, 4)]
    assert (streaming[0]["type"], streaming[0]["delta"]) == ("bill", -15.00)


def test_a_card_ledger_receives_each_statement_payment_on_its_due_date(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)

    payments = of_type(ledger(db, user_id, accounts["Example Card"]), "card")
    july = [row for row in payments if row["date"] == date(2026, 7, 25)][0]
    assert (july["income"], july["delta"]) == (400.00, 400.00)
    assert july["description"] == "Example Card statement"


def test_a_card_balance_falls_with_charges_and_rises_with_payments(db, user_id, accounts):
    set_cutover(db, user_id, accounts)
    charge_the_june_statement(db, user_id, accounts)
    card = db.load_account_by_id(user_id, accounts["Example Card"])

    rows, _first_negative, _end = db.build_ledger_rows(
        ledger(db, user_id, accounts["Example Card"]), db.account_balance_before(user_id, card, START), START)
    balance_on = {row["date"]: row["running_balance"] for row in rows}
    assert balance_on[date(2026, 7, 25)] == 0.0
