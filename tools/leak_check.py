"""The leak check: compare the working tree, and any text about to be
published (a PR body, for example), against what is in the local database.

    python -m tools.leak_check [--db finance.duckdb] [--text pr-body.md ...]

It opens the database read-only, keeps nothing from it, and prints only where
a hit is and a masked form of what matched. Run it before opening a PR. It
needs the database to itself, so stop Metis first.

A hit is a reason to look, not proof: an invented name can happen to match a
real one.
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchants import merchant_key  # noqa: E402

Words = Tuple[str, ...]

MAX_WORDS = 5
MIN_SINGLE_WORD = 4
MIN_AMOUNT = 10.0
SYSTEM_DICTIONARY = "/usr/share/dict/words"

# Where names live, in the table names of any Metis version. Earlier wins when
# one text is found under two kinds.
TEXT_SOURCES = (
    ("name", "users", ("display_name", "email", "slug")),
    ("name", "bills", ("name",)),
    ("name", "recurring_items", ("name",)),
    ("name", "one_time_items", ("name",)),
    ("name", "manual_transactions", ("name",)),
    ("account", "accounts", ("name", "payment_pattern")),
    ("account", "import_account_links", ("label", "display_label")),
    ("account", "import_templates", ("name",)),
    ("merchant", "imported_transactions", ("merchant", "description")),
    ("merchant", "merchant_decisions", ("merchant_key",)),
    ("merchant", "transfer_rules", ("merchant_key",)),
    ("merchant", "category_rules", ("pattern",)),
    ("merchant", "expected_match_rules", ("pattern",)),
)
AMOUNT_SOURCES = (
    ("imported_transactions", "amount"),
    ("bills", "amount"),
    ("bills", "balance"),
    ("recurring_items", "amount"),
    ("one_time_items", "amount"),
    ("manual_transactions", "amount"),
    ("amount_overrides", "amount"),
    ("card_statements", "issuer_balance"),
    ("transaction_splits", "amount"),
)

# Words every bank statement and every finance app uses. Text made only of
# these says nothing about whose data it is.
GENERIC = frozenset("""
    a account acct ach adjustment an and annual at atm auto autopay balance bank bill by card cash ccd charge check
    checking co company corp credit debit dep deposit direct electronic example external fee fees for from id in inc
    interest internal llc ltd mobile monthly my new of on online other pay payment payments payroll pending personal
    pos posted ppd preauthorized purchase recurring ref refund return savings second statement tel test thank the to
    transfer transfers type user web wire withdrawal you
""".split())

_WORD = re.compile(r"[a-z0-9]+")
_AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\w.]|,\d)")


@dataclass(frozen=True)
class Hit:
    kind: str
    path: str
    line: int
    mask: str


@dataclass
class Tokens:
    texts: Dict[Words, str]
    amounts: Set[int]  # in cents


def words_of(text: str) -> Words:
    return tuple(_WORD.findall(str(text or "").lower()))


def _telling(words: Words, dictionary: Set[str]) -> bool:
    """Whether these words could identify someone's data, not just any finance app."""
    meaningful = [word for word in words if word not in GENERIC and not word.isdigit()]
    if not meaningful:
        return False
    if len(words) == 1:
        return len(words[0]) >= MIN_SINGLE_WORD and words[0] not in dictionary
    return any(len(word) >= 3 for word in meaningful)


def _load_dictionary(path: Optional[str]) -> Set[str]:
    try:
        with open(path or SYSTEM_DICTIONARY, encoding="utf-8", errors="ignore") as handle:
            return {line.strip().lower() for line in handle if line.strip()}
    except OSError:
        return set()


def load_tokens(db_path: str, words: Optional[Set[str]] = None) -> Tokens:
    """What must not appear outside the database. `words` are everyday words
    that are never a hit on their own; by default the system dictionary."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    dictionary = words if words is not None else _load_dictionary(None)
    texts: Dict[Words, str] = {}
    amounts: Set[int] = set()
    connection = duckdb.connect(db_path, read_only=True)
    try:
        columns: Dict[str, Set[str]] = {}
        for table, column in connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'main'"
        ).fetchall():
            columns.setdefault(table, set()).add(column)

        def add(kind: str, value: object) -> None:
            for source in {str(value or ""), merchant_key(value) if kind == "merchant" else str(value or "")}:
                parts = words_of(source)
                for length in range(1, min(len(parts), MAX_WORDS) + 1):
                    candidate = parts[:length]
                    if _telling(candidate, dictionary):
                        texts.setdefault(candidate, kind)
                if kind == "name":  # each word of a person's name, too
                    for word in parts:
                        if _telling((word,), dictionary):
                            texts.setdefault((word,), kind)

        for kind, table, wanted in TEXT_SOURCES:
            for column in wanted:
                if column in columns.get(table, ()):
                    for (value,) in connection.execute(
                        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
                    ).fetchall():
                        add(kind, value)
        for table, column in AMOUNT_SOURCES:
            if column in columns.get(table, ()):
                for (value,) in connection.execute(
                    f'SELECT DISTINCT ABS("{column}") FROM "{table}" WHERE "{column}" IS NOT NULL'
                ).fetchall():
                    cents = round(float(value) * 100)
                    if cents % 100 and cents >= MIN_AMOUNT * 100:  # round figures are what tests invent
                        amounts.add(cents)
    finally:
        connection.close()
    return Tokens(texts, amounts)


def _mask(words: Words) -> str:
    return " ".join(word[0] + "*" * (len(word) - 1) for word in words)


def scan_text(text: str, tokens: Tokens, path: str) -> List[Hit]:
    best: Dict[Tuple[int, str], Words] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        parts = words_of(line)
        for start in range(len(parts)):
            for length in range(1, MAX_WORDS + 1):
                candidate = parts[start:start + length]
                kind = tokens.texts.get(candidate)
                if kind and len(candidate) > len(best.get((number, kind), ())):
                    best[(number, kind)] = candidate
        for match in _AMOUNT.finditer(line):
            cents = int(match.group(1).replace(",", "")) * 100 + int(match.group(2))
            if cents in tokens.amounts:
                best.setdefault((number, "amount"), ("***", "**"))
    return [
        Hit(kind, path, number, "***.**" if kind == "amount" else _mask(found))
        for (number, kind), found in sorted(best.items())
    ]


def working_tree_files(root: Path) -> List[str]:
    """Tracked files, and new ones git would add."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=True,
    )
    return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]


def scan(root: Path, tokens: Tokens, files: Optional[Iterable[str]] = None,
         texts: Sequence[str] = ()) -> List[Hit]:
    """Hits in the working tree under root, then in each extra text file."""
    names = list(files) if files is not None else working_tree_files(Path(root))
    hits: List[Hit] = []
    for name, path in [(name, Path(root) / name) for name in sorted(names)] + [(text, Path(text)) for text in texts]:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        hits += scan_text(raw.decode("utf-8", "ignore"), tokens, name)
    return hits


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=os.getenv("FINANCE_DB_PATH", "finance.duckdb"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--files", nargs="*", default=None, help="check only these files under the root")
    parser.add_argument("--text", action="append", default=[], help="a text file to check as well, such as a PR body")
    parser.add_argument("--words", default=None, help="a list of everyday words, one per line")
    args = parser.parse_args(argv)

    try:
        tokens = load_tokens(args.db, _load_dictionary(args.words) if args.words else None)
    except FileNotFoundError:
        print(f"no database at {args.db}: nothing to compare against")
        return 2
    except duckdb.Error as error:
        print(f"could not open the database read-only ({type(error).__name__}). stop metis and try again.")
        return 2
    hits = scan(Path(args.root), tokens, files=args.files, texts=args.text)
    for hit in hits:
        print(f"{hit.path}:{hit.line}  {hit.kind}  {hit.mask}")
    print(f"{len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
