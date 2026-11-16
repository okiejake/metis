"""The ledger page's view (v0.5.1): which account it shows, and which of that
account's rows a filter keeps.

A filter is a search of descriptions, bills and notes, a set of categories (with
"uncategorized"), or a merchant. It only hides rows. Choosing the account is not
a filter: every account's ledger is its own real run-down. The view lives in the
address, so the back button works and a filtered ledger can be linked to.
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


@dataclass(frozen=True)
class LedgerView:
    account_id: Optional[int] = None
    q: str = ""
    categories: Tuple[str, ...] = field(default_factory=tuple)
    merchant: str = ""
    start: str = ""
    end: str = ""

    @classmethod
    def from_params(cls, account: str = "", q: str = "", category: Optional[Iterable[str]] = None,
                    merchant: str = "", start: str = "", end: str = "") -> "LedgerView":
        """A window pinned in the address (start and end) is kept in every link
        the page makes; otherwise the saved date range applies."""
        account = str(account or "").strip()
        pinned = bool((start or "").strip() and (end or "").strip())
        return cls(
            account_id=int(account) if account.isdigit() else None,
            q=(q or "").strip(),
            categories=tuple(_picks(category)),
            merchant="".join(ch for ch in (merchant or "") if ch.isalnum())[:32],
            start=start.strip() if pinned else "",
            end=end.strip() if pinned else "",
        )

    @property
    def filtered(self) -> bool:
        return bool(self.q or self.categories or self.merchant)

    @property
    def category_ids(self) -> Set[int]:
        return {int(pick) for pick in self.categories if pick.isdigit()}

    def params(self, **changes: Any) -> List[Tuple[str, Any]]:
        """The view as address parameters."""
        values = {"account": self.account_id, "q": self.q, "category": list(self.categories),
                  "merchant": self.merchant, "start": self.start, "end": self.end}
        values.update(changes)
        pairs: List[Tuple[str, Any]] = []
        for key in ("account", "q", "category", "merchant", "start", "end"):
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

    def chips(self, category_names: Dict[int, str]) -> List[Dict[str, str]]:
        """One chip per active filter, each with the address that removes it."""
        found: List[Dict[str, str]] = []
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
