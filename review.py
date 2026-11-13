"""Review: the page where everything Metis cannot yet account for is listed and
settled, one decision per merchant.

A decision is stored per merchant (merchant_decisions) and applied to every
matching row, past and future: apply_review_decisions runs after each import.
A merchant can hold one decision of each kind. Rows a category decision set
carry its id in imported_transactions.decision_id; rows a transfer decision
marked carry it in transfer_decision_id. Every action is recorded
(review_actions) so Undo can reverse it completely.

This module reads and writes through services; services never imports it.
"""

import json
from datetime import date, timedelta
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
    """Reverse a decision by the kind recorded with the action, so it works
    even after the decision row was replaced."""
    decision_id = int(payload["decision_id"])
    with db.get_connection() as conn:
        if payload["kind"] == "transfer":
            conn.execute(
                "UPDATE imported_transactions SET is_transfer = FALSE, transfer_decision_id = NULL "
                "WHERE user_id = ? AND transfer_decision_id = ?",
                [user_id, decision_id],
            )
        else:
            conn.execute(
                "UPDATE imported_transactions SET category_id = NULL, category_via = NULL, decision_id = NULL "
                "WHERE user_id = ? AND decision_id = ?",
                [user_id, decision_id],
            )
        conn.execute("DELETE FROM merchant_decisions WHERE id = ? AND user_id = ?", [decision_id, user_id])
    _remove_created_category(user_id, payload.get("created_category_id"))


def _remove_created_category(user_id: int, category_id: Optional[int]) -> None:
    """Delete a category an undone action created, when nothing else uses it."""
    if not category_id:
        return
    uses = db.fetch_one(
        """
        SELECT (SELECT COUNT(*) FROM imported_transactions WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM transaction_splits WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM category_rules WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM recurring_items WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM manual_transactions WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM merchant_decisions WHERE user_id = ? AND category_id = ?)
        """,
        [user_id, category_id] * 6,
    )[0]
    if not uses:
        db.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", [category_id, user_id])


def _undo_charge(user_id: int, payload: Dict[str, Any]) -> None:
    db.execute(
        "UPDATE imported_transactions SET category_id = ?, category_via = ?, is_transfer = ?, decision_id = ?, "
        "transfer_decision_id = ? WHERE id = ? AND user_id = ?",
        [payload["category_id"], payload["category_via"], payload["is_transfer"],
         payload["decision_id"], payload.get("transfer_decision_id"), payload["tx_id"], user_id],
    )
    _remove_created_category(user_id, payload.get("created_category_id"))


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


def has_uncategorized(user_id: int, key: str) -> bool:
    """Whether the merchant still has uncategorized spending to decide."""
    return bool(_uncategorized_ids_by_key(user_id).get(key))


def _save_decision(user_id: int, key: str, kind: str, category_id: Optional[int]) -> int:
    """Store the merchant's decision of this kind, replacing an earlier one of
    the same kind. Returns its id."""
    with db.get_connection() as conn:
        conn.execute(
            "DELETE FROM merchant_decisions WHERE user_id = ? AND merchant_key = ? AND kind = ?", [user_id, key, kind]
        )
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


def confirm_category(user_id: int, key: str, category_id: int, created: bool = False) -> Optional[int]:
    """Give every uncategorized charge from this merchant the category, now and
    on every later import. `created` says the category was made for this
    action, so Undo removes it too. Returns the action id, or None when nothing
    was left to change."""
    tx_ids = _uncategorized_ids_by_key(user_id).get(key, set())
    if not tx_ids:
        return None
    decision_id = _save_decision(user_id, key, "category", category_id)
    _categorize(user_id, tx_ids, category_id, decision_id)
    return record_action(user_id, "decision", {
        "decision_id": decision_id, "kind": "category", "created_category_id": category_id if created else None,
    })


def set_charge(user_id: int, tx_id: int, choice: Choice, created: bool = False) -> Optional[int]:
    """Set one charge by hand: a category, or "transfer". A hand-picked category
    is never overwritten by a merchant decision or a rule. A card row is never
    a transfer, so statements are untouched. Returns the action id, or None
    when the charge already reads that way."""
    row = db.fetch_one(
        "SELECT category_id, category_via, is_transfer, decision_id, transfer_decision_id, account_id "
        "FROM imported_transactions WHERE id = ? AND user_id = ?",
        [tx_id, user_id],
    )
    if not row:
        return None
    before = {"tx_id": tx_id, "category_id": row[0], "category_via": row[1], "is_transfer": bool(row[2]),
              "decision_id": row[3], "transfer_decision_id": row[4],
              "created_category_id": int(choice) if created else None}
    if choice == "transfer":
        if row[5] is not None and int(row[5]) in _card_ids(user_id):
            raise ValueError("A card charge can't be a transfer")
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


# --- Transfers --------------------------------------------------------------


def _rows(user_id: int, where: str, params: List[Any]) -> List[Dict[str, Any]]:
    rows = db.query_all(
        f"""
        SELECT id, account_id, tx_date, amount, is_transfer, transfer_decision_id,
               COALESCE(NULLIF(merchant, ''), description) AS merchant
        FROM imported_transactions
        WHERE user_id = ? AND account_id IS NOT NULL AND {where}
        ORDER BY tx_date, id
        """,
        [user_id, *params],
    )
    for row in rows:
        row["tx_date"] = db._as_date(row["tx_date"])
        row["amount"] = float(row["amount"])
    return rows


def _card_ids(user_id: int) -> Set[int]:
    return {aid for aid, a in db.load_accounts_map(user_id).items() if a.get("account_type") == "credit_card"}


def _pair(outflows: List[Dict[str, Any]], inflows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Pair each outflow with an equal amount arriving in a different account
    within the card-payment matching window, one to one, closest date first."""
    window = db.PAYMENT_MATCH_WINDOW_DAYS
    used: Set[int] = set()
    pairs: Dict[int, Dict[str, Any]] = {}
    for out in outflows:
        best = None
        for arrival in inflows:
            if arrival["id"] in used or arrival["account_id"] == out["account_id"]:
                continue
            if abs(arrival["amount"] + out["amount"]) > 0.005:
                continue
            gap = abs((arrival["tx_date"] - out["tx_date"]).days)
            if gap <= window and (best is None or gap < best[0]):
                best = (gap, arrival)
        if best:
            used.add(best[1]["id"])
            pairs[out["id"]] = best[1]
    return pairs


def _inflows_near(user_id: int, outflows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not outflows:
        return []
    window = timedelta(days=db.PAYMENT_MATCH_WINDOW_DAYS)
    first = min(o["tx_date"] for o in outflows) - window
    last = max(o["tx_date"] for o in outflows) + window
    return _rows(user_id, "amount > 0 AND tx_date >= ? AND tx_date <= ?", [first, last])


def paired_outflows(user_id: int, window_start: date, window_end: date) -> Dict[int, Dict[str, Any]]:
    """Debits in the window, not yet transfers and not on a card, that an equal
    amount arriving in another account answers. Maps the debit's id to its
    arrival: tx_id, account_id, account_name, account_type. Card charges are
    never candidates, so card statements are never touched."""
    cards = _card_ids(user_id)
    outflows = [
        row for row in _rows(
            user_id, "NOT is_transfer AND amount < 0 AND tx_date >= ? AND tx_date <= ?", [window_start, window_end]
        )
        if row["account_id"] not in cards
    ]
    accounts = db.load_accounts_map(user_id)
    result: Dict[int, Dict[str, Any]] = {}
    for out_id, arrival in _pair(outflows, _inflows_near(user_id, outflows)).items():
        account = accounts.get(int(arrival["account_id"]), {})
        result[out_id] = {
            "tx_id": arrival["id"],
            "account_id": int(arrival["account_id"]),
            "account_name": account.get("name", ""),
            "account_type": account.get("account_type", ""),
        }
    return result


def _mark_transfers(user_id: int, tx_ids: Set[int], decision_id: int) -> int:
    if not tx_ids:
        return 0
    ids = sorted(tx_ids)
    placeholders = ", ".join("?" for _ in ids)
    db.execute(
        f"UPDATE imported_transactions SET is_transfer = TRUE, transfer_decision_id = ? "
        f"WHERE user_id = ? AND id IN ({placeholders})",
        [decision_id, user_id, *ids],
    )
    return len(ids)


def _apply_transfer(user_id: int, key: str, decision_id: int, cards: Set[int]) -> int:
    """Mark the merchant's debits outside cards as transfers, then the arrival
    of each when it landed outside a card and is not one already."""
    outflows = [
        row for row in _rows(user_id, "amount < 0", [])
        if row["account_id"] not in cards and merchant_key(row["merchant"]) == key
    ]
    changed = _mark_transfers(
        user_id, {row["id"] for row in outflows if not row["is_transfer"]}, decision_id
    )
    decided = [row for row in outflows if not row["is_transfer"] or row["transfer_decision_id"] == decision_id]
    arrivals = {
        arrival["id"]
        for arrival in _pair(decided, _inflows_near(user_id, decided)).values()
        if arrival["account_id"] not in cards and not arrival["is_transfer"]
    }
    return changed + _mark_transfers(user_id, arrivals, decision_id)


def confirm_transfer(user_id: int, key: str) -> Optional[int]:
    """Every debit from this merchant outside a card is a transfer between the
    owner's accounts, now and on every later import. Returns the action id, or
    None when nothing was left to change."""
    cards = _card_ids(user_id)
    pending = [
        row for row in _rows(user_id, "NOT is_transfer AND amount < 0", [])
        if row["account_id"] not in cards and merchant_key(row["merchant"]) == key
    ]
    if not pending:
        return None
    decision_id = _save_decision(user_id, key, "transfer", None)
    _apply_transfer(user_id, key, decision_id, cards)
    return record_action(user_id, "decision", {"decision_id": decision_id, "kind": "transfer"})


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
    cards = _card_ids(user_id)
    for decision in decisions:
        if decision["kind"] == "category":
            changed += _categorize(
                user_id, by_key.get(decision["merchant_key"], set()),
                int(decision["category_id"]), int(decision["id"]),
            )
        elif decision["kind"] == "transfer":
            changed += _apply_transfer(user_id, decision["merchant_key"], int(decision["id"]), cards)
    return changed


# --- Dismiss and budgets ----------------------------------------------------

# Review's own dismissals share dismissed_suggestions with the recurring
# detector, so one "Restore dismissed" brings every list's rows back.
TRANSFER_DISMISS_PREFIX = "transfer:"
BUDGET_DISMISS_PREFIX = "budget:"


def suggestion_added(user_id: int, key: str) -> bool:
    """Whether a recurring suggestion was already turned into a recurring item."""
    return bool(db.fetch_one(
        "SELECT 1 FROM dismissed_suggestions WHERE user_id = ? AND suggestion_key = ? AND reason = 'added'",
        [user_id, key],
    ))


def find_category_id(user_id: int, name: str) -> Optional[int]:
    """The id of the user's category with this name, ignoring case."""
    row = db.fetch_one(
        "SELECT id FROM categories WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1", [user_id, (name or "").strip()]
    )
    return int(row[0]) if row else None


def dismiss(user_id: int, key: str) -> Optional[int]:
    """Reject a suggestion for good. Returns the action id, or None when it was
    already dismissed."""
    if key in db.load_dismissed_suggestion_keys(user_id):
        return None
    db.dismiss_suggestion(user_id, key)
    return record_action(user_id, "dismiss", {"key": key})


def _undo_dismiss(user_id: int, payload: Dict[str, Any]) -> None:
    db.execute(
        "DELETE FROM dismissed_suggestions WHERE user_id = ? AND suggestion_key = ? AND reason = 'dismissed'",
        [user_id, payload["key"]],
    )


def confirm_budget(user_id: int, category_id: int, monthly: float) -> Optional[int]:
    """Set a category's monthly budget. Returns the action id, or None when the
    category does not exist."""
    category = db.load_category_by_id(user_id, category_id)
    if not category or category["budget_amount"] == monthly:
        return None
    db.set_category_budget(user_id, category_id, monthly)
    return record_action(user_id, "budget", {"category_id": category_id, "before": category["budget_amount"]})


def _undo_budget(user_id: int, payload: Dict[str, Any]) -> None:
    db.set_category_budget(user_id, int(payload["category_id"]), payload["before"])


def _undo_recurring(user_id: int, payload: Dict[str, Any]) -> None:
    """Remove a recurring item added from a suggestion, so it is suggested again."""
    item_id = int(payload["item_id"])
    with db.get_connection() as conn:
        conn.execute("DELETE FROM amount_overrides WHERE recurring_item_id = ?", [item_id])
        for table in ("expected_match_rules", "expected_reconciliations"):
            conn.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND source_type = 'recurring' AND source_id = ?",
                [user_id, item_id],
            )
        conn.execute("DELETE FROM recurring_items WHERE id = ? AND user_id = ?", [item_id, user_id])
        conn.execute(
            "DELETE FROM dismissed_suggestions WHERE user_id = ? AND suggestion_key = ? AND reason = 'added'",
            [user_id, payload["suggestion_key"]],
        )


_UNDO.update({"dismiss": _undo_dismiss, "budget": _undo_budget, "recurring": _undo_recurring})


def suggested_budget(monthly: List[float]) -> Optional[float]:
    """The median month, rounded up to the nearest 10. None when there is no
    history or the median month had no spending."""
    if not monthly:
        return None
    middle = db._median([float(m) for m in monthly])
    if middle <= 0:
        return None
    return float(-(-middle // 10) * 10)


# --- The four lists ---------------------------------------------------------


def _mode(values: List[Any]) -> Any:
    values = [v for v in values if v is not None]
    return db._mode(values) if values else None


def _recent_months(user_id: int, today: date) -> List[tuple]:
    """Up to six complete calendar months before today, starting no earlier
    than the month of the first imported row. Each is (first day, last day)."""
    row = db.fetch_one("SELECT MIN(tx_date) FROM imported_transactions WHERE user_id = ?", [user_id])
    first = db._as_date(row[0]) if row and row[0] else None
    if first is None:
        return []
    months = []
    end = today.replace(day=1) - timedelta(days=1)
    while len(months) < 6:
        start = end.replace(day=1)
        if start < first.replace(day=1):
            break
        months.append((start, end))
        end = start - timedelta(days=1)
    return months


def _monthly_by_category(user_id: int, today: date) -> Dict[int, List[float]]:
    months = _recent_months(user_id, today)
    if not months:
        return {}
    totals: Dict[int, List[float]] = {}
    index = {start: i for i, (start, _end) in enumerate(months)}
    for part in db.spending_parts(user_id, months[-1][0], months[0][1]):
        if part["category_id"] is None:
            continue
        series = totals.setdefault(part["category_id"], [0.0] * len(months))
        series[index[part["tx_date"].replace(day=1)]] += part["spent"]
    return totals


def _transfer_rows(user_id: int, window_start: date, window_end: date,
                   decided: Set[str], dismissed: Set[str]) -> List[Dict[str, Any]]:
    cards = _card_ids(user_id)
    paired = paired_outflows(user_id, window_start, window_end)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in _rows(user_id, "NOT is_transfer AND amount < 0 AND tx_date >= ? AND tx_date <= ?",
                     [window_start, window_end]):
        if row["account_id"] not in cards:
            groups.setdefault(merchant_key(row["merchant"]), []).append(row)
    listed = []
    for key, charges in groups.items():
        matched = [c for c in charges if c["id"] in paired]
        if key in decided or TRANSFER_DISMISS_PREFIX + key in dismissed or len(matched) * 2 < len(charges):
            continue
        listed.append({
            "key": key,
            "name": db._suggest_clean_name(_mode([c["merchant"] for c in charges])),
            "count": len(charges),
            "total": round(-sum(c["amount"] for c in charges), 2),
            "arrived_in": _mode([paired[c["id"]]["account_name"] for c in matched]),
            "charges": [{"tx_id": c["id"], "tx_date": c["tx_date"], "description": c["merchant"],
                         "amount": c["amount"]} for c in charges],
        })
    return sorted(listed, key=lambda r: r["total"], reverse=True)


def _category_suggestions(user_id: int) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT COALESCE(NULLIF(merchant, ''), description), category_id FROM imported_transactions "
        "WHERE user_id = ? AND category_id IS NOT NULL",
        [user_id],
    )
    by_key: Dict[str, List[int]] = {}
    for text, category_id in rows:
        by_key.setdefault(merchant_key(text), []).append(int(category_id))
    return {key: _mode(ids) for key, ids in by_key.items()}


def _category_rows(user_id: int, parts: List[Dict[str, Any]], skip: Set[str]) -> List[Dict[str, Any]]:
    cards = _card_ids(user_id)
    groups: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for part in parts:
        if part["category_id"] is not None:
            continue
        key = merchant_key(part["merchant"])
        if key in skip:
            continue
        charge = groups.setdefault(key, {}).setdefault(part["tx_id"], {
            "tx_id": part["tx_id"], "tx_date": part["tx_date"], "description": part["merchant"], "amount": 0.0,
            "on_card": part["account_id"] is not None and int(part["account_id"]) in cards,
        })
        charge["amount"] = round(charge["amount"] + part["spent"], 2)
    suggestions = _category_suggestions(user_id)
    splits = db.split_counts_by_transaction(user_id)
    listed = []
    for key, charges in groups.items():
        total = round(sum(c["amount"] for c in charges.values()), 2)
        # A merchant whose uncategorized rows net to a refund is listed too: the
        # refund still needs a category.
        if abs(total) <= 0.005:
            continue
        rows = sorted(charges.values(), key=lambda c: (c["tx_date"], c["tx_id"]))
        for charge in rows:
            charge["split_count"] = splits.get(charge["tx_id"], 0)
        listed.append({
            "key": key,
            "name": db._suggest_clean_name(_mode([c["description"] for c in rows])),
            "count": len(rows),
            "total": total,
            "suggested_category_id": suggestions.get(key),
            "can_transfer": any(not c["on_card"] for c in rows),
            "charges": rows,
        })
    return sorted(listed, key=lambda r: r["total"], reverse=True)


def _budget_rows(user_id: int, window_start: date, window_end: date, dismissed: Set[str]) -> List[Dict[str, Any]]:
    summary = db.build_budget_summary(user_id, window_start, window_end)
    monthly = _monthly_by_category(user_id, date.today())
    listed = []
    for category in summary["categories"]:
        category_id = category.get("id")
        if (not category_id or category["has_target"] or category["actual"] <= 0.005
                or f"{BUDGET_DISMISS_PREFIX}{category_id}" in dismissed):
            continue
        series = monthly.get(int(category_id), [])
        listed.append({
            "category_id": int(category_id),
            "name": category["name"],
            "color": category["color"],
            "typical": round(db._median(series), 2) if series else None,
            "suggested": suggested_budget(series),
            "actual": category["actual"],
        })
    return sorted(listed, key=lambda r: r["actual"], reverse=True)


# --- Stale accounts ---------------------------------------------------------

STALE_DISMISS_PREFIX = "stale:"
STALE_MARGIN = 1.2      # flagged when more than 20% past the usual gap
STALE_MIN_DAYS = 5      # transaction days needed before a gap means anything


def stale_accounts(user_id: int, today: date) -> List[Dict[str, Any]]:
    """Accounts that look overdue for an import, in account order. The usual gap
    is the average number of days between an account's transaction days. An
    account with nothing imported is listed too. A dismissal names the account's
    last transaction day, so the next import brings the flag back."""
    days: Dict[int, List[date]] = {}
    for account_id, tx_date in db.fetch_all(
            "SELECT DISTINCT account_id, tx_date FROM imported_transactions "
            "WHERE user_id = ? AND account_id IS NOT NULL ORDER BY tx_date", [user_id]):
        days.setdefault(int(account_id), []).append(db._as_date(tx_date))
    dismissed = db.load_dismissed_suggestion_keys(user_id)

    rows = []
    for account in db.load_accounts(user_id):
        held = days.get(int(account["id"]), [])
        last = held[-1] if held else None
        key = f"{STALE_DISMISS_PREFIX}{account['id']}:{last.isoformat() if last else 'none'}"
        row = {"key": key, "account_id": int(account["id"]), "name": account["name"],
               "default_template_id": account["default_template_id"], "last_date": last,
               "days_since": None, "usual_gap": None}
        if key in dismissed:
            continue
        if last is None:
            rows.append(row)
            continue
        if len(held) < STALE_MIN_DAYS:
            continue
        usual = (held[-1] - held[0]).days / (len(held) - 1)
        since = (today - last).days
        if since > usual * STALE_MARGIN:
            rows.append({**row, "days_since": since, "usual_gap": round(usual, 1)})
    return rows


def build_review(user_id: int, window_start: date, window_end: date) -> Dict[str, Any]:
    """Everything Review shows for the date range: the meter and the lists, in
    working order, with no row in two lists."""
    parts = db.spending_parts(user_id, window_start, window_end)
    spending = round(sum(p["spent"] for p in parts), 2)
    uncategorized = round(sum(p["spent"] for p in parts if p["category_id"] is None), 2)
    # Uncategorized refunds can push the raw share past 100; the meter reads 0..100.
    percent = max(0, min(100, round(100 * (spending - uncategorized) / spending))) if spending > 0.005 else None

    decided = {d["merchant_key"] for d in load_decisions(user_id) if d["kind"] == "transfer"}
    dismissed = db.load_dismissed_suggestion_keys(user_id)
    transfers = _transfer_rows(user_id, window_start, window_end, decided, dismissed)
    categories = _category_rows(user_id, parts, {row["key"] for row in transfers})
    budgets = _budget_rows(user_id, window_start, window_end, dismissed)
    recurring = db.detect_recurring_suggestions(user_id)
    imports = stale_accounts(user_id, date.today())
    return {
        "meter": {"percent": percent, "spending": spending, "uncategorized": uncategorized},
        "imports": imports,
        "transfers": transfers,
        "categories": categories,
        "budgets": budgets,
        "recurring": recurring,
        "waiting": len(imports) + len(transfers) + len(categories) + len(budgets) + len(recurring),
    }


# --- Waiting count ----------------------------------------------------------

_WAITING: Dict[int, tuple] = {}


def _data_stamp(user_id: int) -> tuple:
    """Cheap fingerprint of everything the lists depend on."""
    return tuple(db.fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM imported_transactions WHERE user_id = ?),
          (SELECT MAX(id) FROM imported_transactions WHERE user_id = ?),
          (SELECT SUM(COALESCE(category_id, 0)) FROM imported_transactions WHERE user_id = ?),
          (SELECT SUM(CAST(is_transfer AS INTEGER)) FROM imported_transactions WHERE user_id = ?),
          (SELECT COUNT(*) FROM merchant_decisions WHERE user_id = ?),
          (SELECT COUNT(*) FROM dismissed_suggestions WHERE user_id = ?),
          (SELECT COUNT(*) FROM recurring_items WHERE user_id = ?),
          (SELECT COUNT(*) FROM categories WHERE user_id = ?),
          (SELECT SUM(budget_amount) FROM categories WHERE user_id = ?),
          (SELECT COUNT(*) FROM transaction_splits WHERE user_id = ?),
          (SELECT COUNT(*) FROM expected_reconciliations WHERE user_id = ?),
          (SELECT COUNT(*) FROM accounts WHERE user_id = ?),
          CURRENT_DATE
        """,
        [user_id] * 12,
    ))


def review_waiting_count(user_id: int) -> int:
    """How many rows Review lists for the shared date range. Remembered until
    the data or the range changes, since every page shows it."""
    window_start, window_end, _invalid = db.resolve_forecast_window(user_id)
    stamp = (_data_stamp(user_id), window_start, window_end)
    cached = _WAITING.get(user_id)
    if cached and cached[0] == stamp:
        return cached[1]
    count = build_review(user_id, window_start, window_end)["waiting"]
    _WAITING[user_id] = (stamp, count)
    return count
