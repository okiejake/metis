"""Templates are the only way Metis reads a CSV. The user maps the columns once;
Metis never guesses a format, an account or an account type from a file.

All data here is synthetic.
"""

from datetime import date

import pytest

import import_templates as it
from services import CsvImportError

CHECKING = {"id": 1, "name": "Example Checking", "account_type": "checking"}
CARD = {"id": 2, "name": "Example Card", "account_type": "credit_card"}


def template(**fields):
    base = {"name": "Example export", "date_field": "Date", "description_field": "Description",
            "amount_layout": "signed", "amount_field": "Amount"}
    return it.build_template({**base, **fields})


def parse(csv_text, tmpl, account=CHECKING, patterns=()):
    header, rows = it.read_csv(csv_text.encode())
    return it.parse_rows(header, rows, tmpl, account, patterns)


def money(rows):
    return [(r["amount"], r["flow"], r["is_transfer"]) for r in rows]


def test_signed_amounts_negative_is_money_out():
    rows = parse("Date,Description,Amount\n2026-07-01,ACME GROCER,-25.00\n2026-07-02,EXAMPLE PAYROLL,100\n",
                 template())

    assert money(rows) == [(-25.0, "expense", False), (100.0, "income", False)]
    assert rows[0]["tx_date"] == date(2026, 7, 1)
    assert rows[0]["account_id"] == 1 and rows[0]["account"] == "Example Checking"


def test_flipped_signs_read_a_positive_charge_as_money_out():
    rows = parse("Date,Description,Amount\n2026-07-01,ACME GROCER,25.00\n", template(amount_sign="inverted"))

    assert money(rows) == [(-25.0, "expense", False)]


def test_separate_in_and_out_columns():
    tmpl = template(amount_layout="split", amount_field="", amount_in_field="Deposit", amount_out_field="Withdrawal")
    rows = parse("Date,Description,Deposit,Withdrawal\n2026-07-01,ACME GROCER,,25.00\n2026-07-02,EXAMPLE PAYROLL,100.00,\n",
                 tmpl)

    assert money(rows) == [(-25.0, "expense", False), (100.0, "income", False)]


def test_a_type_column_names_money_in():
    tmpl = template(amount_layout="typed", type_field="Kind", credit_value="Credit")
    rows = parse("Date,Description,Kind,Amount\n2026-07-01,ACME GROCER,Debit,25.00\n2026-07-02,EXAMPLE PAYROLL,credit,100.00\n",
                 tmpl)

    assert money(rows) == [(-25.0, "expense", False), (100.0, "income", False)]
    assert rows[0]["raw_type"] == "Debit"


def test_on_a_card_a_payment_is_a_transfer_and_other_money_in_a_refund():
    tmpl = template(amount_layout="typed", type_field="Kind", credit_value="Credit")
    rows = parse(
        "Date,Description,Kind,Amount\n"
        "2026-07-01,PAYMENT - THANK YOU,Credit,250.00\n"
        "2026-07-02,ACME STORE,Credit,20.00\n"
        "2026-07-03,ACME STORE,Debit,40.00\n",
        tmpl, CARD)

    assert money(rows) == [(250.0, "transfer", True), (20.0, "refund", False), (-40.0, "expense", False)]


def test_on_a_card_a_payment_type_is_a_transfer_whatever_the_description():
    rows = parse("Date,Description,Type,Amount\n2026-07-01,ONLINE,Payment,250.00\n",
                 template(type_field="Type", amount_sign="standard"), CARD)

    assert money(rows) == [(250.0, "transfer", True)]


def test_checking_money_out_matching_a_card_pattern_is_a_transfer():
    rows = parse("Date,Description,Amount\n2026-07-01,EXAMPLE CARD PMT,-300.00\n", template(),
                 patterns=("EXAMPLE CARD",))

    assert money(rows) == [(-300.0, "transfer", True)]


def test_the_second_date_column_fills_in_when_the_first_is_blank():
    tmpl = template(date_field="Cleared", date_fallback_field="Date")
    rows = parse("Date,Cleared,Description,Amount\n07/01/2026,07/03/2026,ACME A,-1\n07/05/2026,,ACME B,-1\n", tmpl)

    assert [r["tx_date"] for r in rows] == [date(2026, 7, 3), date(2026, 7, 5)]


def test_the_date_format_is_read_from_the_sample():
    assert it.detect_date_format(["11/13/2026", "01/02/2026"]) == "%m/%d/%Y"
    assert it.detect_date_format(["2026-11-13"]) == "%Y-%m-%d"
    assert it.detect_date_format(["13/11/2026"]) == "%d/%m/%Y"
    assert it.detect_date_format(["11/13/26"]) == "%m/%d/%y"
    assert it.detect_date_format(["soon"]) is None


def test_a_chosen_date_format_is_used():
    rows = parse("Date,Description,Amount\n02/03/2026,ACME,-1\n", template(date_format="%d/%m/%Y"))

    assert rows[0]["tx_date"] == date(2026, 3, 2)


def test_amounts_with_symbols_commas_and_parentheses():
    rows = parse('Date,Description,Amount\n2026-07-01,ACME,"$1,250.50"\n2026-07-02,ACME,(12.00)\n', template())

    assert [r["amount"] for r in rows] == [1250.5, -12.0]


def test_a_file_missing_the_template_s_columns_names_them():
    with pytest.raises(CsvImportError, match="Amount"):
        parse("Date,Description,Total\n2026-07-01,ACME,-1\n", template())


def test_the_merchant_column_wins_otherwise_the_ach_payee_or_the_description():
    with_column = parse("Date,Description,Payee,Amount\n2026-07-01,ACME 123 TOWN,Acme,-1\n",
                        template(merchant_field="Payee"))
    ach = parse("Date,Description,Amount\n2026-07-01,ORIG ACH ACME UTILITIES TYPE: BILL ID: 9,-1\n", template())
    plain = parse("Date,Description,Amount\n2026-07-01,ACME   GROCER,-1\n", template())

    assert [with_column[0]["merchant"], ach[0]["merchant"], plain[0]["merchant"]] == [
        "Acme", "ACME UTILITIES", "ACME GROCER"]


def test_without_a_merchant_column_checking_keeps_four_words_and_a_card_the_description():
    text = "Date,Description,Amount\n2026-07-01,POS PURCHASE ACME STORE 1234 SPRINGFIELD,-1\n"

    assert parse(text, template())[0]["merchant"] == "POS PURCHASE ACME STORE"
    assert parse(text, template(), CARD)[0]["merchant"] == "POS PURCHASE ACME STORE 1234 SPRINGFIELD"


def test_read_csv_handles_a_byte_order_mark_and_older_encodings():
    header, rows = it.read_csv("﻿Date,Description\n2026-07-01,Café\n".encode("utf-8"))
    assert header == ["Date", "Description"]

    header, rows = it.read_csv("Date,Description\n2026-07-01,Caf\xe9\n".encode("cp1252"))
    assert rows[0]["Description"] == "Café"

    with pytest.raises(CsvImportError):
        it.read_csv(b"")


def test_suggested_columns_come_from_the_column_names():
    assert it.suggest_mapping(["Posting Date", "Details", "Amount", "Memo"]) == {
        "date_field": "Posting Date", "amount_field": "Amount"}


@pytest.mark.parametrize("fields, message", [
    ({"name": " "}, "name"),
    ({"date_field": ""}, "date"),
    ({"description_field": ""}, "description"),
    ({"amount_field": ""}, "amount"),
    ({"amount_layout": "split", "amount_in_field": "", "amount_out_field": ""}, "money"),
    ({"amount_layout": "typed", "type_field": "Kind", "credit_value": ""}, "money in"),
])
def test_build_template_refuses_an_incomplete_mapping(fields, message):
    with pytest.raises(ValueError, match=message):
        template(**fields)


def test_build_template_keeps_only_the_chosen_amount_layout():
    tmpl = template(amount_layout="split", amount_field="Amount", amount_in_field="In", amount_out_field="Out",
                    type_field="Kind", credit_value="Credit")

    assert (tmpl["amount_field"], tmpl["credit_value"]) == (None, None)
    assert tmpl["type_field"] == "Kind", "a type column is recorded with any layout"


def test_templates_are_saved_listed_with_their_created_date_and_deleted(db, user_id, accounts):
    template_id = it.save_template(user_id, template(name="Acme export"))
    it.set_default_template(user_id, accounts["Example Checking"], template_id)

    [saved] = it.load_templates(user_id)
    assert saved["name"] == "Acme export"
    assert saved["created"] == date.today().isoformat()
    assert saved["used_by"] == ["Example Checking"]

    with pytest.raises(ValueError, match="already"):
        it.save_template(user_id, template(name="acme EXPORT"))

    it.delete_template(user_id, template_id)
    assert it.load_templates(user_id) == []
    assert db.load_account_by_id(user_id, accounts["Example Checking"])["default_template_id"] is None


def test_another_user_s_template_cannot_be_loaded_or_set(db, user_id, accounts):
    with db.get_connection() as conn:
        other = conn.execute("INSERT INTO users (slug, display_name, email) VALUES "
                             "('other', 'Other', 'other@example.com') RETURNING id").fetchone()[0]
    theirs = it.save_template(other, template())

    assert it.load_template(user_id, theirs) is None
    with pytest.raises(ValueError):
        it.set_default_template(user_id, accounts["Example Checking"], theirs)
