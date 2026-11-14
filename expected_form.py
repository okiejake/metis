"""The one form that adds and edits every expected transaction: a bill,
scheduled income or a one-time item (#97, #101, #104, #107, #108).

It opens prefilled from ids and enums in the query string, never from names or
amounts: category_id, kind, repeats, from_tx (an imported transaction),
from_suggestion (a suggestion's token) and next. Saving with from_tx links that
transaction; saving with from_suggestion marks the suggestion added.
"""

from datetime import date
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlencode

import services as db

ONE_TIME = "one-time"
REPEATS = (ONE_TIME, "weekly", "biweekly", "semimonthly", "monthly", "every_x_months", "yearly")
REPEATS_LABELS = {
    ONE_TIME: "one-time", "weekly": "weekly", "biweekly": "bi-weekly", "semimonthly": "semi-monthly",
    "monthly": "monthly", "every_x_months": "every x months", "yearly": "yearly",
}
PREFILL_KEYS = ("category_id", "kind", "repeats", "from_tx", "from_suggestion", "next")


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def current_amount(bill: Mapping[str, Any], today: Optional[date] = None) -> float:
    """A bill's amount in effect today."""
    today = today or date.today()
    amount = float(bill["amount"])
    for override in db.load_amount_overrides(int(bill["id"])):
        if db._as_date(override["effective_date"]) <= today:
            amount = float(override["amount"])
    return amount


def added_suggestion_key(user_id: int, token: str) -> Optional[str]:
    """The key of a suggestion already added as a bill, by its token."""
    for (key,) in db.fetch_all(
            "SELECT suggestion_key FROM dismissed_suggestions WHERE user_id = ? AND reason = 'added'", [user_id]):
        if db.suggestion_token(user_id, key) == token:
            return key
    return None


def _from_transaction(user_id: int, tx_id: Optional[int]) -> Dict[str, Any]:
    tx = db.load_imported_transaction_by_id(user_id, tx_id) if tx_id else None
    if not tx:
        return {}
    amount = float(tx["amount"])
    return {
        "name": db._suggest_clean_name(tx.get("merchant") or tx.get("description")),
        "kind": "income" if amount > 0 else "expense",
        "amount": abs(amount),
        "account_id": tx.get("account_id"),
        "category_id": tx.get("category_id"),
        "first_due_date": db._as_date(tx["tx_date"]),
        "from_tx": int(tx["id"]),
    }


def _from_suggestion(user_id: int, token: str) -> Dict[str, Any]:
    suggestion = db.find_bill_suggestion(user_id, token)
    if not suggestion:
        return {}
    return {
        "name": suggestion["name"],
        "kind": suggestion["kind"],
        "amount": suggestion["amount"],
        "account_id": suggestion["account_id"],
        "category_id": suggestion["category_id"],
        "repeats": suggestion["frequency_type"],
        "interval_months": suggestion["interval_months"],
        "first_due_date": db.parse_iso_date(suggestion["first_due_date"]),
        "from_suggestion": token,
    }


def blank(user_id: int, query: Mapping[str, Any], repeats: str = "monthly") -> Dict[str, Any]:
    """The values a new item's form opens with, from the query's ids and enums."""
    values: Dict[str, Any] = {
        "name": "", "kind": "expense", "amount": None, "category_id": None, "account_id": None,
        "repeats": repeats, "first_due_date": date.today(), "end_date": None, "interval_months": 2,
        "semimonthly_day1": 1, "semimonthly_day2": 15, "from_tx": None, "from_suggestion": "",
    }
    category_id = _int(query.get("category_id"))
    if category_id and db.load_category_by_id(user_id, category_id):
        values["category_id"] = category_id
    if query.get("kind") in db.VALID_KINDS:
        values["kind"] = query["kind"]
    if query.get("repeats") in REPEATS:
        values["repeats"] = query["repeats"]
    values.update(_from_transaction(user_id, _int(query.get("from_tx"))))
    if query.get("from_suggestion"):
        values.update(_from_suggestion(user_id, str(query["from_suggestion"])))
    return values


def from_bill(bill: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": bill["id"], "name": bill["name"], "kind": bill["kind"], "amount": current_amount(bill),
        "category_id": bill.get("category_id"), "account_id": bill.get("account_id"),
        "repeats": bill["frequency_type"], "first_due_date": bill["first_due_date"], "end_date": bill.get("end_date"),
        "interval_months": bill.get("interval_months") or 1, "semimonthly_day1": bill.get("semimonthly_day1") or 1,
        "semimonthly_day2": bill.get("semimonthly_day2") or 15, "from_tx": None, "from_suggestion": "",
    }


def from_one_time(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": item["id"], "name": item["name"], "kind": item["kind"], "amount": item["amount"],
        "category_id": item.get("category_id"), "account_id": item.get("account_id"), "repeats": ONE_TIME,
        "first_due_date": item["expected_date"], "end_date": None, "interval_months": 2, "semimonthly_day1": 1,
        "semimonthly_day2": 15, "from_tx": None, "from_suggestion": "",
    }


def heading(values: Mapping[str, Any], editing: bool) -> str:
    noun = ONE_TIME if values["repeats"] == ONE_TIME else ("income" if values["kind"] == "income" else "bill")
    return f"{'edit' if editing else 'add'} {noun}"


def form_query(form: Mapping[str, Any]) -> str:
    """The prefill part of a submitted form, to reopen it after an error."""
    kept = {key: str(form.get(key, "")) for key in PREFILL_KEYS if str(form.get(key, "")).strip()}
    return f"?{urlencode(kept)}" if kept else ""


# --- Saving ---------------------------------------------------------------------


def parse(user_id: int, form: Mapping[str, Any]) -> Dict[str, Any]:
    """Check a submitted form. Raises ValueError with a short reason."""
    name = str(form.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    kind = form.get("kind", "")
    if kind not in db.VALID_KINDS:
        raise ValueError("type must be income or expense")
    try:
        amount = db.parse_positive_float(str(form.get("amount", "")))
    except ValueError:
        raise ValueError("amount must be greater than 0")
    repeats = form.get("repeats", "")
    if repeats not in REPEATS:
        raise ValueError("choose how often it repeats")
    try:
        first = db.parse_iso_date(str(form.get("first_due_date", "")).strip())
    except ValueError:
        raise ValueError("enter a valid date")
    end = None
    if repeats != ONE_TIME:
        try:
            end = db.parse_optional_date(str(form.get("end_date", "")))
        except ValueError:
            raise ValueError("enter a valid end date")
        if end and end < first:
            raise ValueError("end date can't be before the first due date")
    return {
        "name": name, "kind": kind, "amount": amount, "repeats": repeats, "first_due_date": first, "end_date": end,
        "interval_months": db.parse_positive_int(str(form.get("interval_months", "1")), fallback=1),
        "semimonthly_day1": db.parse_day(str(form.get("semimonthly_day1", "1")), fallback=1),
        "semimonthly_day2": db.parse_day(str(form.get("semimonthly_day2", "15")), fallback=15),
        "category_id": db.parse_optional_category_id(user_id, str(form.get("category_id", ""))),
        "account_id": db.parse_optional_account_id(user_id, str(form.get("account_id", ""))),
    }


def create(user_id: int, values: Mapping[str, Any]) -> tuple:
    """Add a bill or a one-time item. Returns (source_type, id)."""
    if values["repeats"] == ONE_TIME:
        row = db.fetch_one(
            "INSERT INTO one_time_items (name, kind, amount, expected_date, category_id, account_id, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            [values["name"], values["kind"], values["amount"], values["first_due_date"], values["category_id"],
             values["account_id"], user_id],
        )
        return "one_time", int(row[0])
    row = db.fetch_one(
        """
        INSERT INTO bills (name, kind, amount, first_due_date, end_date, frequency_type, interval_months,
                           semimonthly_day1, semimonthly_day2, category_id, account_id, user_id, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
        RETURNING id
        """,
        [values["name"], values["kind"], values["amount"], values["first_due_date"], values["end_date"],
         values["repeats"], values["interval_months"], values["semimonthly_day1"], values["semimonthly_day2"],
         values["category_id"], values["account_id"], user_id],
    )
    return "bill", int(row[0])


def update_bill(user_id: int, bill: Mapping[str, Any], values: Mapping[str, Any], scope: str) -> None:
    """Save a bill. A changed amount applies to every due date, clearing earlier
    changes, or with scope "future" from today on only."""
    changed = abs(values["amount"] - current_amount(bill)) >= 0.005
    db.execute(
        """
        UPDATE bills
        SET name = ?, kind = ?, first_due_date = ?, end_date = ?, frequency_type = ?, interval_months = ?,
            semimonthly_day1 = ?, semimonthly_day2 = ?, category_id = ?, account_id = ?
        WHERE id = ? AND user_id = ?
        """,
        [values["name"], values["kind"], values["first_due_date"], values["end_date"], values["repeats"],
         values["interval_months"], values["semimonthly_day1"], values["semimonthly_day2"], values["category_id"],
         values["account_id"], bill["id"], user_id],
    )
    if not changed:
        return
    if scope == "future":
        db.save_amount_override(int(bill["id"]), date.today(), values["amount"])
    else:
        db.execute("UPDATE bills SET amount = ? WHERE id = ? AND user_id = ?", [values["amount"], bill["id"], user_id])
        db.delete_overrides_for_bill(int(bill["id"]))


def update_one_time(user_id: int, item_id: int, values: Mapping[str, Any]) -> None:
    db.execute(
        "UPDATE one_time_items SET name = ?, kind = ?, amount = ?, expected_date = ?, category_id = ?, account_id = ? "
        "WHERE id = ? AND user_id = ?",
        [values["name"], values["kind"], values["amount"], values["first_due_date"], values["category_id"],
         values["account_id"], item_id, user_id],
    )


def delete_item(user_id: int, source_type: str, item_id: int) -> None:
    """Delete a bill or a one-time item, with its links, its auto-match rules
    and a bill's amount changes, so the linked actuals are free again."""
    table = "bills" if source_type == "bill" else "one_time_items"
    with db.get_connection() as conn:
        owned = conn.execute(f"SELECT 1 FROM {table} WHERE id = ? AND user_id = ?", [item_id, user_id]).fetchone()
        if not owned:
            return
        if source_type == "bill":
            conn.execute("DELETE FROM amount_overrides WHERE bill_id = ?", [item_id])
        for linked in ("expected_reconciliations", "expected_match_rules"):
            conn.execute(f"DELETE FROM {linked} WHERE user_id = ? AND source_type = ? AND source_id = ?",
                         [user_id, source_type, item_id])
        conn.execute(f"DELETE FROM {table} WHERE id = ? AND user_id = ?", [item_id, user_id])


def link_transaction(user_id: int, source_type: str, item_id: int, tx_id: Optional[int]) -> None:
    """Link the transaction the form was opened from, when it is the user's."""
    if tx_id and db.load_imported_transaction_by_id(user_id, tx_id):
        db.confirm_reconciliation(user_id, source_type, item_id, tx_id)
