"""Review: the page where everything Metis cannot yet account for is listed and
settled, one decision per merchant.

A decision is stored per merchant (merchant_decisions) and applied to every
matching row, past and future: apply_review_decisions runs after each import.
Rows a decision changed carry its id (imported_transactions.decision_id), and
every action is recorded (review_actions) so Undo can reverse it completely.

This module reads and writes through services; services never imports it.
"""

import json
from datetime import date
from typing import Any, Dict, List, Optional, Set, Union

import services as db
from merchants import merchant_key

# The whole of history, for decisions that cover a merchant's every charge.
ALL_TIME = (date(1900, 1, 1), date(9999, 12, 31))

# A category chosen for one charge in the picker; "transfer" marks it a transfer.
Choice = Union[int, str]


# --- Actions and Undo -------------------------------------------------------


def record_action(user_id: int, kind: str, payload: Dict[str, Any]) -> int:
    """Remember an action and what reversing it takes. Returns its id."""
    with db.get_connection() as conn:
        return int(conn.execute(
            "INSERT INTO review_actions (user_id, kind, payload) VALUES (?, ?, ?) RETURNING id",
            [user_id, kind, json.dumps(payload)],
        ).fetchone()[0])


def undo(user_id: int, action_id: int) -> str:
    """Reverse one recorded action completely and forget it. Returns the message
    to show."""
    row = db.fetch_one(
        "SELECT kind, payload FROM review_actions WHERE id = ? AND user_id = ?", [action_id, user_id]
    )
    if not row:
        return "Nothing to undo"
    kind, payload = row[0], json.loads(row[1])
    handler = _UNDO.get(kind)
    if handler:
        handler(user_id, payload)
    db.execute("DELETE FROM review_actions WHERE id = ? AND user_id = ?", [action_id, user_id])
    return "Undone"


def _undo_decision(user_id: int, payload: Dict[str, Any]) -> None:
    decision_id = int(payload["decision_id"])
    decision = db.fetch_one(
        "SELECT kind FROM merchant_decisions WHERE id = ? AND user_id = ?", [decision_id, user_id]
    )
    with db.get_connection() as conn:
        if decision and decision[0] == "transfer":
            conn.execute(
                "UPDATE imported_transactions SET is_transfer = FALSE, decision_id = NULL "
                "WHERE user_id = ? AND decision_id = ?",
                [user_id, decision_id],
            )
        else:
            conn.execute(
                "UPDATE imported_transactions SET category_id = NULL, category_via = NULL, decision_id = NULL "
                "WHERE user_id = ? AND decision_id = ?",
                [user_id, decision_id],
            )
        conn.execute("DELETE FROM merchant_decisions WHERE id = ? AND user_id = ?", [decision_id, user_id])


def _undo_charge(user_id: int, payload: Dict[str, Any]) -> None:
    db.execute(
        "UPDATE imported_transactions SET category_id = ?, category_via = ?, is_transfer = ?, decision_id = ? "
        "WHERE id = ? AND user_id = ?",
        [payload["category_id"], payload["category_via"], payload["is_transfer"],
         payload["decision_id"], payload["tx_id"], user_id],
    )


_UNDO = {"decision": _undo_decision, "charge": _undo_charge}


# --- Decisions --------------------------------------------------------------


def _uncategorized_ids_by_key(user_id: int) -> Dict[str, Set[int]]:
    """Every transaction with uncategorized spending, all history, by merchant.
    Built from spending_parts, so it counts exactly what Review counts."""
    found: Dict[str, Set[int]] = {}
    for part in db.spending_parts(user_id, *ALL_TIME):
        if part["category_id"] is None:
            found.setdefault(merchant_key(part["merchant"]), set()).add(int(part["tx_id"]))
    return found


def _save_decision(user_id: int, key: str, kind: str, category_id: Optional[int]) -> int:
    """Store the merchant's decision, replacing an earlier one. Returns its id."""
    with db.get_connection() as conn:
        conn.execute("DELETE FROM merchant_decisions WHERE user_id = ? AND merchant_key = ?", [user_id, key])
        return int(conn.execute(
            "INSERT INTO merchant_decisions (user_id, merchant_key, kind, category_id) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            [user_id, key, kind, category_id],
        ).fetchone()[0])


def _categorize(user_id: int, tx_ids: Set[int], category_id: int, decision_id: int) -> int:
    if not tx_ids:
        return 0
    ids = sorted(tx_ids)
    placeholders = ", ".join("?" for _ in ids)
    db.execute(
        f"UPDATE imported_transactions SET category_id = ?, category_via = 'merchant', decision_id = ? "
        f"WHERE user_id = ? AND id IN ({placeholders})",
        [category_id, decision_id, user_id, *ids],
    )
    return len(ids)


def confirm_category(user_id: int, key: str, category_id: int) -> Optional[int]:
    """Give every uncategorized charge from this merchant the category, now and
    on every later import. Returns the action id, or None when nothing was left
    to change."""
    tx_ids = _uncategorized_ids_by_key(user_id).get(key, set())
    if not tx_ids:
        return None
    decision_id = _save_decision(user_id, key, "category", category_id)
    _categorize(user_id, tx_ids, category_id, decision_id)
    return record_action(user_id, "decision", {"decision_id": decision_id})


def set_charge(user_id: int, tx_id: int, choice: Choice) -> Optional[int]:
    """Set one charge by hand: a category, or "transfer". A hand-picked category
    is never overwritten by a merchant decision or a rule. Returns the action
    id, or None when the charge already reads that way."""
    row = db.fetch_one(
        "SELECT category_id, category_via, is_transfer, decision_id FROM imported_transactions "
        "WHERE id = ? AND user_id = ?",
        [tx_id, user_id],
    )
    if not row:
        return None
    before = {"tx_id": tx_id, "category_id": row[0], "category_via": row[1],
              "is_transfer": bool(row[2]), "decision_id": row[3]}
    if choice == "transfer":
        if before["is_transfer"]:
            return None
        db.execute(
            "UPDATE imported_transactions SET is_transfer = TRUE WHERE id = ? AND user_id = ?", [tx_id, user_id]
        )
    else:
        category_id = int(choice)
        if before["category_id"] == category_id and before["category_via"] is None:
            return None
        db.execute(
            "UPDATE imported_transactions SET category_id = ?, category_via = NULL, decision_id = NULL "
            "WHERE id = ? AND user_id = ?",
            [category_id, tx_id, user_id],
        )
    return record_action(user_id, "charge", before)


def load_decisions(user_id: int) -> List[Dict[str, Any]]:
    return db.query_all(
        "SELECT id, merchant_key, kind, category_id FROM merchant_decisions WHERE user_id = ? ORDER BY id",
        [user_id],
    )


def apply_review_decisions(user_id: int) -> int:
    """Apply every stored decision to the rows it covers that are not yet
    changed. Idempotent; runs after each import. Returns how many rows changed."""
    decisions = load_decisions(user_id)
    if not decisions:
        return 0
    changed = 0
    by_key = _uncategorized_ids_by_key(user_id)
    for decision in decisions:
        if decision["kind"] == "category":
            changed += _categorize(
                user_id, by_key.get(decision["merchant_key"], set()),
                int(decision["category_id"]), int(decision["id"]),
            )
    return changed
