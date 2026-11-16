"""The ledger page's view: its mode (#124), which accounts it shows, and which
rows a filter keeps.

A filter is a search of descriptions, bills and notes, a set of categories (with
"uncategorized"), or a merchant. It only hides rows. In cash flow, choosing the
account is not a filter: every account's ledger is its own real run-down. The
view lives in the address, so the back button works and a view can be linked to.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlencode

import services as db
from merchants import merchant_key

UNCATEGORIZED = "uncategorized"
ALL = "all"

# The ledger's two modes (#124): every account's transactions in one list, or
# one account's run-down with its forecast.
TRANSACTIONS = "transactions"
CASH_FLOW = "cash-flow"
MODES = (TRANSACTIONS, CASH_FLOW)


def merchant_token(text: Any) -> str:
    """A short, stable stand-in for a merchant, so no name sits in an address."""
    return hashlib.sha256(merchant_key(text).encode("utf-8")).hexdigest()[:12]


def _picks(values: Optional[Iterable[str]]) -> List[str]:
    """Category picks as written in the address: ids and "uncategorized".
    "all" among them means every category, so it clears the rest."""
    cleaned: List[str] = []
    for value in values or []:
        value = str(value).strip()
        if value == ALL:
            return []
        if (value.isdigit() or value == UNCATEGORIZED) and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _account_picks(values: Any) -> List[int]:
    """Account ids as written in the address: one, or several in a list."""
    values = values if isinstance(values, (list, tuple)) else [values]
    picked: List[int] = []
    for value in values:
        value = str(value or "").strip()
        if value.isdigit() and int(value) not in picked:
            picked.append(int(value))
    return picked


@dataclass(frozen=True)
class LedgerView:
    """What the ledger shows (#124). In transactions, account_ids are the
    accounts picked (none means all of them). In cash flow, account_id is the
    one account shown (none means the default checking account)."""
    mode: str = TRANSACTIONS
    account_id: Optional[int] = None
    account_ids: Tuple[int, ...] = field(default_factory=tuple)
    q: str = ""
    categories: Tuple[str, ...] = field(default_factory=tuple)
    merchant: str = ""
    start: str = ""
    end: str = ""

    @classmethod
    def from_params(cls, account: Any = "", q: str = "", category: Optional[Iterable[str]] = None,
                    merchant: str = "", start: str = "", end: str = "", mode: str = "") -> "LedgerView":
        """A window pinned in the address (start and end) is kept in every link
        the page makes; otherwise the saved date range applies. An unknown
        mode is transactions."""
        mode = mode if mode in MODES else TRANSACTIONS
        picked = _account_picks(account)
        pinned = bool((start or "").strip() and (end or "").strip())
        return cls(
            mode=mode,
            account_id=picked[0] if mode == CASH_FLOW and picked else None,
            account_ids=tuple(picked) if mode == TRANSACTIONS else (),
            q=(q or "").strip(),
            categories=tuple(_picks(category)),
            merchant="".join(ch for ch in (merchant or "") if ch.isalnum())[:32],
            start=start.strip() if pinned else "",
            end=end.strip() if pinned else "",
        )

    @property
    def transactions(self) -> bool:
        return self.mode == TRANSACTIONS

    @property
    def filtered(self) -> bool:
        return bool(self.q or self.categories or self.merchant)

    @property
    def category_ids(self) -> Set[int]:
        return {int(pick) for pick in self.categories if pick.isdigit()}

    def params(self, **changes: Any) -> List[Tuple[str, Any]]:
        """The view as address parameters."""
        values = {"account": list(self.account_ids) if self.transactions else self.account_id, "mode": self.mode,
                  "q": self.q, "category": list(self.categories), "merchant": self.merchant, "start": self.start,
                  "end": self.end}
        values.update(changes)
        pairs: List[Tuple[str, Any]] = []
        for key in ("account", "mode", "q", "category", "merchant", "start", "end"):
            value = values[key]
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                if item not in (None, ""):
                    pairs.append((key, item))
        return pairs

    def query(self, **changes: Any) -> str:
        return urlencode(self.params(**changes))

    def url(self, base: str = "/ledger", **changes: Any) -> str:
        query = self.query(**changes)
        return f"{base}?{query}" if query else base

    def switch(self, mode: str) -> str:
        """The address of the other mode, with the same search, categories and
        window. Cash flow keeps an account only when exactly one was picked;
        transactions opens on every account."""
        if mode == CASH_FLOW:
            account = self.account_ids[0] if len(self.account_ids) == 1 else (
                None if self.transactions else self.account_id)
            return self.url(mode=CASH_FLOW, account=account)
        return self.url(mode=TRANSACTIONS, account=[])

    def chips(self, category_names: Dict[int, str],
              account_names: Optional[Dict[int, str]] = None) -> List[Dict[str, str]]:
        """One chip per active filter, each with the address that removes it.
        In transactions, each picked account is one."""
        found: List[Dict[str, str]] = []
        for picked in self.account_ids:
            rest = [other for other in self.account_ids if other != picked]
            found.append({"label": (account_names or {}).get(picked, "account"), "url": self.url(account=rest)})
        if self.q:
            found.append({"label": f"search: {self.q}", "url": self.url(q="")})
        for pick in self.categories:
            label = UNCATEGORIZED if pick == UNCATEGORIZED else category_names.get(int(pick), "category")
            rest = [other for other in self.categories if other != pick]
            found.append({"label": label, "url": self.url(category=rest)})
        if self.merchant:
            found.append({"label": "merchant", "url": self.url(merchant="")})
        return found


def merchant_for_token(user_id: int, token: str) -> Optional[str]:
    """The merchant key a token stands for, among the user's imported rows."""
    if not token:
        return None
    for (text,) in db.fetch_all("SELECT DISTINCT COALESCE(NULLIF(merchant, ''), description) FROM imported_transactions "
                                "WHERE user_id = ?", [user_id]):
        if merchant_token(text) == token:
            return merchant_key(text)
    return None


def resolve_account(user_id: int, account_id: Optional[int]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """The account to show and whether the one asked for was not the user's:
    the default checking account unless the address names another."""
    if account_id is not None:
        account = db.load_account_by_id(user_id, account_id)
        if account:
            return account, False
        return db.default_checking_account(user_id), True
    return db.default_checking_account(user_id), False


def _with_children(user_id: int, picked: Set[int]) -> Set[int]:
    if not picked:
        return set()
    return picked | {int(c["id"]) for c in db.load_categories(user_id) if c.get("parent_id") in picked}


def filter_rows(user_id: int, rows: List[Dict[str, Any]], view: LedgerView,
                window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """The rows a filter keeps.

    - search: part of the description, the bill's name or the note, any case;
    - categories: an imported row by its category or any split part, a
      forecast row by its bill's; "uncategorized" is budget's definition;
      statements show only under "all";
    - merchant: that merchant's imported rows."""
    if not view.filtered:
        return rows
    wanted = _with_children(user_id, view.category_ids)
    uncategorized = UNCATEGORIZED in view.categories
    loose = db.uncategorized_transaction_ids(user_id, window_start, window_end) if uncategorized else set()
    needle = view.q.lower()

    def keep(row: Dict[str, Any]) -> bool:
        if view.merchant and (row["type"] != "actual" or merchant_token(row.get("merchant")) != view.merchant):
            return False
        lines = row.get("splits") or []
        if needle and needle not in " ".join(
                [row.get("description") or "", row.get("bill") or "", row.get("note") or ""]
                + [line.get("bill") or "" for line in lines]).lower():
            return False
        if wanted or uncategorized:
            if row["type"] == "card":
                return False
            ids = {row.get("category_id")} | {part["category_id"] for part in row.get("splits") or []}
            in_loose = row["type"] == "actual" and row.get("id") in loose
            if not (ids & wanted or in_loose):
                return False
        return True

    return [row for row in rows if keep(row)]
