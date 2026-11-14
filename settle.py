"""Settling one transaction where it is shown (#103, #105, #107): give it a
category, link it to a bill, make it a one-time item, or mark it unexpected.
Budget, the ledger and the inbox all call these, so each choice works the same
everywhere. Every choice is recorded, so Undo reverses it (see inbox.py).

After a category or a bill link, rule_prompt offers the rule that would do the
same for other transactions, and preview_count says how many it would match.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union

import services as db
from inbox import record_action, set_charge

RULE_SHARE = 0.10  # the prompt's amount range: this much either side


def _transaction(user_id: int, tx_id: int) -> Dict[str, Any]:
    tx = db.load_imported_transaction_by_id(user_id, tx_id)
    if not tx:
        raise ValueError("transaction not found")
    return tx


def resolve_category_choice(user_id: int, choice: str, new_name: str = "") -> Tuple[Union[int, str], bool]:
    """A picker value as (category id or "transfer", created). "new" reuses a
    category of that name, so a repeated submit doesn't fail, and otherwise
    creates it. Call only once there is something to change."""
    choice = str(choice or "").strip()
    if choice == "transfer":
        return "transfer", False
    if choice == "new":
        existing = db.fetch_one("SELECT id FROM categories WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1",
                                [user_id, (new_name or "").strip()])
        if existing:
            return int(existing[0]), False
        return db.create_category(user_id, new_name), True
    if not choice:
        raise ValueError("choose a category")
    category_id = int(choice)
    if not db.load_category_by_id(user_id, category_id):
        raise ValueError("category not found")
    return category_id, False


def assign_category(user_id: int, tx_id: int, choice: str, new_name: str = "") -> Optional[int]:
    """Give one transaction a category by hand. Returns the action id, or None
    when it already had that category."""
    _transaction(user_id, tx_id)
    picked, created = resolve_category_choice(user_id, choice, new_name)
    if picked == "transfer":
        raise ValueError("choose a category")
    return set_charge(user_id, tx_id, picked, created=created)


def bills_to_match(user_id: int, tx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Active bills of the transaction's kind, nearest in amount first."""
    import expected_form as ef

    amount = float(tx.get("signed", tx["amount"]))
    kind = "income" if amount > 0 else "expense"
    magnitude = abs(amount)
    bills = [bill for bill in db.load_active_bills(user_id) if bill["kind"] == kind]
    for bill in bills:
        bill["current_amount"] = ef.current_amount(bill)
    return sorted(bills, key=lambda b: (abs(b["current_amount"] - magnitude), b["name"].lower()))


def match(user_id: int, tx_id: int, bill_id: int) -> Optional[int]:
    """Link the transaction to a bill. It takes the bill's category, and an
    unexpected mark goes. Returns the action id, or None when it was already
    linked."""
    tx = _transaction(user_id, tx_id)
    if not db.load_bill_by_id(user_id, bill_id):
        raise ValueError("bill not found")
    if not db.confirm_reconciliation(user_id, "bill", bill_id, tx_id):
        return None
    return record_action(user_id, "match", {"tx_id": tx_id, "was_unexpected": bool(tx.get("unexpected"))})


def unmatch(user_id: int, tx_id: int) -> Optional[int]:
    """Remove the transaction's link to its expected transaction."""
    _transaction(user_id, tx_id)
    row = db.fetch_one("SELECT source_type, source_id, matched_via FROM expected_reconciliations "
                       "WHERE user_id = ? AND imported_transaction_id = ?", [user_id, tx_id])
    if not row:
        return None
    db.execute("DELETE FROM expected_reconciliations WHERE user_id = ? AND imported_transaction_id = ?",
               [user_id, tx_id])
    return record_action(user_id, "unmatch", {"tx_id": tx_id, "source_type": row[0], "source_id": int(row[1]),
                                              "matched_via": row[2]})


def make_one_time(user_id: int, tx_id: int) -> Optional[int]:
    """Make a one-time item with the transaction's name, amount, date, account
    and category, and link the two. Returns the action id."""
    tx = _transaction(user_id, tx_id)
    if db.fetch_one("SELECT 1 FROM expected_reconciliations WHERE user_id = ? AND imported_transaction_id = ?",
                    [user_id, tx_id]):
        return None
    amount = float(tx["amount"])
    row = db.fetch_one(
        "INSERT INTO one_time_items (name, kind, amount, expected_date, category_id, account_id, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [tx.get("merchant") or tx["description"], "income" if amount > 0 else "expense", round(abs(amount), 2),
         tx["tx_date"], tx.get("category_id"), tx.get("account_id"), user_id],
    )
    item_id = int(row[0])
    db.confirm_reconciliation(user_id, "one_time", item_id, tx_id)
    return record_action(user_id, "one_time", {"item_id": item_id, "was_unexpected": bool(tx.get("unexpected"))})


def _set_unexpected(user_id: int, tx_id: int, marked: bool) -> Optional[int]:
    tx = _transaction(user_id, tx_id)
    if bool(tx.get("unexpected")) == marked:
        return None
    db.execute("UPDATE imported_transactions SET unexpected = ? WHERE id = ? AND user_id = ?", [marked, tx_id, user_id])
    return record_action(user_id, "unexpected", {"tx_id": tx_id, "before": not marked})


def mark_unexpected(user_id: int, tx_id: int) -> Optional[int]:
    """Mark the transaction unexpected: it leaves the unbudgeted lists, appears
    in the inbox, and auto-match rules skip it."""
    return _set_unexpected(user_id, tx_id, True)


def clear_unexpected(user_id: int, tx_id: int) -> Optional[int]:
    return _set_unexpected(user_id, tx_id, False)


# --- The rule prompt --------------------------------------------------------------

RULE_KINDS = ("category", "bill")


def rule_prompt(user_id: int, tx_id: int, kind: str, range_start: date) -> Optional[Dict[str, Any]]:
    """What the prompt opens with after a category is picked for one
    transaction, or it is matched to a bill. A category rule matches the name,
    this amount give or take 10%, from the start of the page's range with no
    end; a bill's auto-match rule matches the name at any amount. None when
    there is nothing to offer a rule for."""
    tx = db.load_imported_transaction_by_id(user_id, tx_id)
    if not tx or kind not in RULE_KINDS:
        return None
    prompt: Dict[str, Any] = {"kind": kind, "tx_id": tx_id, "pattern": db.suggest_rule_pattern(tx["description"]),
                              "amount_min": None, "amount_max": None, "date_from": None, "date_to": None,
                              "description": tx.get("merchant") or tx["description"]}
    if kind == "category":
        if not tx.get("category_id"):
            return None
        magnitude = abs(float(tx["amount"]))
        prompt.update(target_id=int(tx["category_id"]), target_name=tx.get("category_name") or "",
                      amount_min=round(magnitude * (1 - RULE_SHARE), 2),
                      amount_max=round(magnitude * (1 + RULE_SHARE), 2), date_from=range_start)
    else:
        row = db.fetch_one("SELECT r.source_id, b.name FROM expected_reconciliations r "
                           "JOIN bills b ON b.id = r.source_id AND b.user_id = r.user_id "
                           "WHERE r.user_id = ? AND r.imported_transaction_id = ? AND r.source_type = 'bill'",
                           [user_id, tx_id])
        if not row:
            return None
        prompt.update(target_id=int(row[0]), target_name=row[1])
    prompt["count"] = preview_count(user_id, kind, tx_id, prompt["pattern"], prompt["amount_min"],
                                    prompt["amount_max"], prompt["date_from"], prompt["date_to"])
    return prompt


def preview_count(user_id: int, kind: str, tx_id: Optional[int], pattern: str, amount_min: Optional[float],
                  amount_max: Optional[float], date_from: Optional[date], date_to: Optional[date]) -> int:
    """How many other transactions the rule would take: uncategorized ones for a
    category rule, unlinked ones of the bill's kind for an auto-match rule. A
    blank pattern matches nothing."""
    if not (pattern or "").strip():
        return 0
    rule = {"match_on": "description", "pattern": pattern.strip(), "amount_min": amount_min,
            "amount_max": amount_max, "date_from": date_from, "date_to": date_to}
    if kind == "category":
        rows = db.category_rule_candidates(user_id, "AND i.category_id IS NULL")
    else:
        tx = db.load_imported_transaction_by_id(user_id, tx_id) if tx_id else None
        sign = "i.amount > 0" if tx and float(tx["amount"]) > 0 else "i.amount < 0"
        rows = db.rule_rows(
            user_id, f"AND NOT i.is_transfer AND NOT i.unexpected AND {sign} AND i.id NOT IN "
                     "(SELECT imported_transaction_id FROM expected_reconciliations WHERE user_id = ?)", [user_id])
    return sum(1 for row in rows if int(row["id"]) != (tx_id or 0) and db._rule_matches(rule, row))
