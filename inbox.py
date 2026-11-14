"""The inbox: the page where everything Metis cannot yet account for is listed and
settled, one decision per merchant.

Confirming a merchant's category makes a merchant rule (see the category rules
in services), applied to every matching row, past and future. Confirming a
merchant as a transfer makes a transfer rule (transfer_rules), applied after
each import by apply_transfer_rules; rows it marked carry its id in
imported_transactions.transfer_rule_id. Every action is recorded
(inbox_actions) so Undo can reverse it completely.

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
            "INSERT INTO inbox_actions (user_id, kind, payload) VALUES (?, ?, ?) RETURNING id",
            [user_id, kind, json.dumps(payload)],
        ).fetchone()[0])


def undo(user_id: int, action_id: int) -> str:
    """Reverse one recorded action completely and forget it. Returns the message
    to show."""
    row = db.fetch_one(
        "SELECT kind, payload FROM inbox_actions WHERE id = ? AND user_id = ?", [action_id, user_id]
    )
    if not row:
        return "Nothing to undo"
    kind, payload = row[0], json.loads(row[1])
    handler = _UNDO.get(kind)
    if handler:
        handler(user_id, payload)
    db.execute("DELETE FROM inbox_actions WHERE id = ? AND user_id = ?", [action_id, user_id])
    return "Undone"


def _undo_rule(user_id: int, payload: Dict[str, Any]) -> None:
    """Delete a merchant rule a confirm made; the rows it set are worked out
    again, so they go back to uncategorized unless another rule takes them."""
    if payload.get("rule_id"):
        db.delete_category_rule(user_id, int(payload["rule_id"]))
    _remove_created_category(user_id, payload.get("created_category_id"))


def _undo_transfer(user_id: int, payload: Dict[str, Any]) -> None:
    rule_id = int(payload["transfer_rule_id"])
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE imported_transactions SET is_transfer = FALSE, transfer_rule_id = NULL "
            "WHERE user_id = ? AND transfer_rule_id = ?",
            [user_id, rule_id],
        )
        conn.execute("DELETE FROM transfer_rules WHERE id = ? AND user_id = ?", [rule_id, user_id])


def _remove_created_category(user_id: int, category_id: Optional[int]) -> None:
    """Delete a category an undone action created, when nothing else uses it."""
    if not category_id:
        return
    uses = db.fetch_one(
        """
        SELECT (SELECT COUNT(*) FROM imported_transactions WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM transaction_splits WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM category_rules WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM bills WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM one_time_items WHERE user_id = ? AND category_id = ?)
             + (SELECT COUNT(*) FROM categories WHERE user_id = ? AND parent_id = ?)
        """,
        [user_id, category_id] * 6,
    )[0]
    if not uses:
        db.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", [category_id, user_id])


def _undo_charge(user_id: int, payload: Dict[str, Any]) -> None:
    db.execute(
        "UPDATE imported_transactions SET category_id = ?, category_via = ?, is_transfer = ?, category_rule_id = ?, "
        "transfer_rule_id = ? WHERE id = ? AND user_id = ?",
        [payload["category_id"], payload["category_via"], payload["is_transfer"],
         payload.get("category_rule_id"), payload.get("transfer_rule_id"), payload["tx_id"], user_id],
    )
    _remove_created_category(user_id, payload.get("created_category_id"))


_UNDO = {"rule": _undo_rule, "transfer": _undo_transfer, "charge": _undo_charge}


# --- Categories by merchant ---------------------------------------------------


def _uncategorized_ids_by_key(user_id: int) -> Dict[str, Set[int]]:
    """Every transaction with uncategorized spending, all history, by merchant.
    Built from spending_parts, so it counts exactly what the inbox counts."""
    found: Dict[str, Set[int]] = {}
    for part in db.spending_parts(user_id, *ALL_TIME):
        if part["category_id"] is None:
            found.setdefault(merchant_key(part["merchant"]), set()).add(int(part["tx_id"]))
    return found


def has_uncategorized(user_id: int, key: str) -> bool:
    """Whether the merchant still has uncategorized spending to decide."""
    return bool(_uncategorized_ids_by_key(user_id).get(key))


def confirm_category(user_id: int, key: str, category_id: int, created: bool = False) -> Optional[int]:
    """Make a merchant rule: every charge from this merchant takes the category,
    now and on every later import, unless picked by hand. `created` says the
    category was made for this action, so Undo removes it too. Returns the
    action id, or None when nothing was left to change."""
    if not _uncategorized_ids_by_key(user_id).get(key):
        return None
    rule_id = db.create_category_rule(user_id, category_id, key, match_on="merchant")
    return record_action(user_id, "rule", {
        "rule_id": rule_id, "created_category_id": category_id if created else None,
    })


def set_charge(user_id: int, tx_id: int, choice: Choice, created: bool = False) -> Optional[int]:
    """Set one charge by hand: a category, or "transfer". A hand-picked category
    is never overwritten by a rule. A card row is never a transfer, so
    statements are untouched. Returns the action id, or None when the charge
    already reads that way."""
    row = db.fetch_one(
        "SELECT category_id, category_via, is_transfer, category_rule_id, transfer_rule_id, account_id "
        "FROM imported_transactions WHERE id = ? AND user_id = ?",
        [tx_id, user_id],
    )
    if not row:
        return None
    before = {"tx_id": tx_id, "category_id": row[0], "category_via": row[1], "is_transfer": bool(row[2]),
              "category_rule_id": row[3], "transfer_rule_id": row[4],
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
            "UPDATE imported_transactions SET category_id = ?, category_via = NULL, category_rule_id = NULL "
            "WHERE id = ? AND user_id = ?",
            [category_id, tx_id, user_id],
        )
    return record_action(user_id, "charge", before)


# --- Transfers --------------------------------------------------------------


def _rows(user_id: int, where: str, params: List[Any]) -> List[Dict[str, Any]]:
    rows = db.query_all(
        f"""
        SELECT id, account_id, tx_date, amount, is_transfer, transfer_rule_id,
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


def _mark_transfers(user_id: int, tx_ids: Set[int], rule_id: int) -> int:
    if not tx_ids:
        return 0
    ids = sorted(tx_ids)
    placeholders = ", ".join("?" for _ in ids)
    db.execute(
        f"UPDATE imported_transactions SET is_transfer = TRUE, transfer_rule_id = ? "
        f"WHERE user_id = ? AND id IN ({placeholders})",
        [rule_id, user_id, *ids],
    )
    return len(ids)


def _apply_transfer(user_id: int, key: str, rule_id: int, cards: Set[int]) -> int:
    """Mark the merchant's debits outside cards as transfers, then the arrival
    of each when it landed outside a card and is not one already."""
    outflows = [
        row for row in _rows(user_id, "amount < 0", [])
        if row["account_id"] not in cards and merchant_key(row["merchant"]) == key
    ]
    changed = _mark_transfers(
        user_id, {row["id"] for row in outflows if not row["is_transfer"]}, rule_id
    )
    decided = [row for row in outflows if not row["is_transfer"] or row["transfer_rule_id"] == rule_id]
    arrivals = {
        arrival["id"]
        for arrival in _pair(decided, _inflows_near(user_id, decided)).values()
        if arrival["account_id"] not in cards and not arrival["is_transfer"]
    }
    return changed + _mark_transfers(user_id, arrivals, rule_id)


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
    with db.get_connection() as conn:
        conn.execute("DELETE FROM transfer_rules WHERE user_id = ? AND merchant_key = ?", [user_id, key])
        rule_id = int(conn.execute(
            "INSERT INTO transfer_rules (user_id, merchant_key) VALUES (?, ?) RETURNING id", [user_id, key]
        ).fetchone()[0])
    _apply_transfer(user_id, key, rule_id, cards)
    return record_action(user_id, "transfer", {"transfer_rule_id": rule_id})


def load_transfer_rules(user_id: int) -> List[Dict[str, Any]]:
    return db.query_all("SELECT id, merchant_key FROM transfer_rules WHERE user_id = ? ORDER BY id", [user_id])


def apply_transfer_rules(user_id: int) -> int:
    """Apply every transfer rule to the rows it covers that are not yet marked.
    Idempotent; runs after each import. Returns how many rows changed."""
    rules = load_transfer_rules(user_id)
    if not rules:
        return 0
    cards = _card_ids(user_id)
    return sum(_apply_transfer(user_id, rule["merchant_key"], int(rule["id"]), cards) for rule in rules)


# --- Dismiss ----------------------------------------------------------------

# The inbox's own dismissals share dismissed_suggestions with the bill
# suggestions, so one "Restore dismissed" brings every list's rows back.
TRANSFER_DISMISS_PREFIX = "transfer:"


def suggestion_added(user_id: int, key: str) -> bool:
    """Whether a bill suggestion was already added as a bill."""
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


def _undo_bill(user_id: int, payload: Dict[str, Any]) -> None:
    """Remove a bill added from a suggestion, so it is suggested again."""
    bill_id = int(payload["bill_id"])
    with db.get_connection() as conn:
        conn.execute(
            "DELETE FROM amount_overrides WHERE bill_id IN (SELECT id FROM bills WHERE id = ? AND user_id = ?)",
            [bill_id, user_id],
        )
        for table in ("expected_match_rules", "expected_reconciliations"):
            conn.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND source_type = 'bill' AND source_id = ?",
                [user_id, bill_id],
            )
        conn.execute("DELETE FROM bills WHERE id = ? AND user_id = ?", [bill_id, user_id])
        conn.execute(
            "DELETE FROM dismissed_suggestions WHERE user_id = ? AND suggestion_key = ? AND reason = 'added'",
            [user_id, payload["suggestion_key"]],
        )


# --- Undoing a settle choice (see settle.py) -----------------------------------


def _restore_mark(user_id: int, tx_id: int, marked: Any) -> None:
    db.execute("UPDATE imported_transactions SET unexpected = ? WHERE id = ? AND user_id = ?",
               [bool(marked), tx_id, user_id])


def _undo_match(user_id: int, payload: Dict[str, Any]) -> None:
    tx_id = int(payload["tx_id"])
    db.execute("DELETE FROM expected_reconciliations WHERE user_id = ? AND imported_transaction_id = ?",
               [user_id, tx_id])
    _restore_mark(user_id, tx_id, payload.get("was_unexpected"))


def _undo_unmatch(user_id: int, payload: Dict[str, Any]) -> None:
    db.confirm_reconciliation(user_id, payload["source_type"], int(payload["source_id"]), int(payload["tx_id"]),
                              matched_via=payload.get("matched_via") or "confirm")


def _undo_one_time(user_id: int, payload: Dict[str, Any]) -> None:
    import expected_form as ef

    item_id = int(payload["item_id"])
    linked = db.fetch_all("SELECT imported_transaction_id FROM expected_reconciliations "
                          "WHERE user_id = ? AND source_type = 'one_time' AND source_id = ?", [user_id, item_id])
    ef.delete_item(user_id, "one_time", item_id)
    for (tx_id,) in linked:
        _restore_mark(user_id, int(tx_id), payload.get("was_unexpected"))


def _undo_unexpected(user_id: int, payload: Dict[str, Any]) -> None:
    _restore_mark(user_id, int(payload["tx_id"]), payload.get("before"))


_UNDO.update({"dismiss": _undo_dismiss, "bill": _undo_bill, "match": _undo_match, "unmatch": _undo_unmatch,
              "one_time": _undo_one_time, "unexpected": _undo_unexpected})


# --- The lists ----------------------------------------------------------------


def _mode(values: List[Any]) -> Any:
    values = [v for v in values if v is not None]
    return db._mode(values) if values else None


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


def build_inbox(user_id: int, window_start: date, window_end: date) -> Dict[str, Any]:
    """Everything the inbox shows for the date range: the meter and the lists, in
    working order, with no row in two lists."""
    parts = db.spending_parts(user_id, window_start, window_end)
    spending = round(sum(p["spent"] for p in parts), 2)
    uncategorized = round(sum(p["spent"] for p in parts if p["category_id"] is None), 2)
    # Uncategorized refunds can push the raw share past 100; the meter reads 0..100.
    percent = max(0, min(100, round(100 * (spending - uncategorized) / spending))) if spending > 0.005 else None

    decided = {rule["merchant_key"] for rule in load_transfer_rules(user_id)}
    dismissed = db.load_dismissed_suggestion_keys(user_id)
    transfers = _transfer_rows(user_id, window_start, window_end, decided, dismissed)
    categories = _category_rows(user_id, parts, {row["key"] for row in transfers})
    bills = db.detect_bill_suggestions(user_id)
    imports = stale_accounts(user_id, date.today())
    negative = db.first_projected_negative(user_id, window_start, window_end)
    alerts = [{"kind": "negative", "date": negative.isoformat()}] if negative else []
    return {
        "alerts": alerts,
        "meter": {"percent": percent, "spending": spending, "uncategorized": uncategorized},
        "imports": imports,
        "transfers": transfers,
        "categories": categories,
        "bills": bills,
        "waiting": len(alerts) + len(imports) + len(transfers) + len(categories) + len(bills),
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
          (SELECT COUNT(*) FROM transfer_rules WHERE user_id = ?),
          (SELECT COUNT(*) FROM category_rules WHERE user_id = ?),
          (SELECT COUNT(*) FROM dismissed_suggestions WHERE user_id = ?),
          (SELECT COUNT(*) FROM bills WHERE user_id = ?),
          (SELECT COUNT(*) FROM categories WHERE user_id = ?),
          (SELECT SUM(CAST(unexpected AS INTEGER)) FROM imported_transactions WHERE user_id = ?),
          (SELECT COUNT(*) FROM transaction_splits WHERE user_id = ?),
          (SELECT COUNT(*) FROM expected_reconciliations WHERE user_id = ?),
          (SELECT COUNT(*) FROM accounts WHERE user_id = ?),
          -- the forecast behind the negative-balance alert
          (SELECT SUM(amount) FROM bills WHERE user_id = ?),
          (SELECT SUM(CAST(active AS INTEGER)) FROM bills WHERE user_id = ?),
          (SELECT COUNT(*) FROM one_time_items WHERE user_id = ?),
          (SELECT SUM(amount) FROM one_time_items WHERE user_id = ?),
          (SELECT value FROM settings WHERE key = ?),
          CURRENT_DATE
        """,
        [user_id] * 17 + [f"user:{user_id}:starting_balance"],
    ))


def inbox_waiting_count(user_id: int) -> int:
    """How many rows the inbox lists for the shared date range. Remembered until
    the data or the range changes, since every page shows it."""
    window_start, window_end, _invalid = db.resolve_forecast_window(user_id)
    stamp = (_data_stamp(user_id), window_start, window_end)
    cached = _WAITING.get(user_id)
    if cached and cached[0] == stamp:
        return cached[1]
    count = build_inbox(user_id, window_start, window_end)["waiting"]
    _WAITING[user_id] = (stamp, count)
    return count
