import base64
import calendar
import csv
import hashlib
import io
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus

import duckdb
from fastapi import Request
from fastapi.responses import RedirectResponse, Response

import legacy_schema
import migrations
from base_path import configured_base_path
from date_range import DEFAULT_RULE, RangeRule, resolve_rule, rule_from_saved, rule_to_saved
from merchants import legacy_merchant_key, merchant_key

DB_PATH = os.getenv("FINANCE_DB_PATH", "finance.duckdb")
VALID_KINDS = {"income", "expense"}
VALID_FREQUENCIES = {"weekly", "biweekly", "semimonthly", "monthly", "yearly", "every_x_months"}
DEFAULT_CATEGORY_COLOR = "#64748b"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FORECAST_WINDOW_SETTINGS_KEY = "forecast_window"
FORECAST_WINDOW_FALLBACK_START_KEY = "ledger_window_start"
FORECAST_WINDOW_FALLBACK_END_KEY = "ledger_window_end"
DEFAULT_USER_SLUG = "personal"
DEFAULT_USER_DISPLAY_NAME = "Personal"
USER_SLUG_COOKIE = "metis_user"
USER_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365 * 5
USER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")



def format_currency(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"




def _connection_config() -> Dict[str, Any]:
    """Optional caps for a machine with little to spare. DuckDB otherwise takes
    most of the memory and every core. METIS_DB_MEMORY_LIMIT takes DuckDB's own
    format, e.g. "128MB"."""
    config: Dict[str, Any] = {}
    memory_limit = os.getenv("METIS_DB_MEMORY_LIMIT", "").strip()
    if memory_limit:
        config["memory_limit"] = memory_limit
    threads = os.getenv("METIS_DB_THREADS", "").strip()
    if threads:
        config["threads"] = int(threads)
    return config


# One open database per file for the life of the process. Opening the file is
# far slower than any query Metis runs, and every query used to open and close
# it. Each `with get_connection() as conn:` block gets its own cursor on the
# shared database and runs alone: DuckDB refuses two simultaneous writes to the
# same row, and with queries this quick, taking turns costs nothing noticeable.
# The lock is re-entrant, so a block may call code that opens another.
_DATABASE: Dict[str, duckdb.DuckDBPyConnection] = {}
_DATABASE_LOCK = threading.RLock()


def _database() -> duckdb.DuckDBPyConnection:
    database = _DATABASE.get(DB_PATH)
    if database is None:
        for path in list(_DATABASE):  # tests move DB_PATH between files
            _DATABASE.pop(path).close()
        database = _DATABASE[DB_PATH] = duckdb.connect(DB_PATH, config=_connection_config())
    return database


@contextmanager
def get_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    with _DATABASE_LOCK:
        cursor = _database().cursor()
        try:
            yield cursor
        finally:
            cursor.close()


def close_database() -> None:
    """Close the shared database, writing everything to the file. The next
    query opens it again."""
    with _DATABASE_LOCK:
        for path in list(_DATABASE):
            _DATABASE.pop(path).close()


def table_has_column(conn: duckdb.DuckDBPyConnection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(row[1] == column_name for row in rows)


def column_is_nullable(conn: duckdb.DuckDBPyConnection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(row[1] == column_name and not row[3] for row in rows)


def categories_has_global_name_unique(conn: duckdb.DuckDBPyConnection) -> bool:
    rows = conn.execute(
        """
        SELECT constraint_column_names
        FROM duckdb_constraints()
        WHERE table_name = 'categories' AND constraint_type = 'UNIQUE'
        """
    ).fetchall()
    return any(list(row[0]) == ["name"] for row in rows)


def migrate_card_payment_signs(conn: duckdb.DuckDBPyConnection) -> int:
    """Repair card payments that were imported as debits, returning how many.

    A payment is money *in* to a card and must be positive, exactly like a refund;
    that is what pays the balance down. Two importers used to store it negative,
    which reads as "owe more", so a card's balance climbed with every payment made
    against it. Consumers all took ABS() and so never noticed, but any balance the
    forecast derives from these rows is wrong until they are fixed.

    Only rows the issuer itself called a credit are touched, so a legitimately
    negative transfer — a payment reversal, a cash-advance adjustment — keeps its
    sign. Flipping only negatives makes this idempotent: once repaired, nothing
    matches. Credit tokens are pooled across templates, which is safe because the
    token still has to be the one the row was actually imported with."""
    credit_values = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT credit_value FROM import_templates "
            "WHERE credit_value IS NOT NULL AND TRIM(credit_value) <> ''"
        ).fetchall()
    ]
    # 'Credit' is what the built-in Visa parser records; templates name their own.
    tokens = sorted({"Credit", *credit_values})
    where = f"""
            is_transfer AND amount < 0
              AND raw_type IN ({', '.join('?' for _ in tokens)})
              AND account_id IN (SELECT id FROM accounts WHERE account_type = 'credit_card')
            """
    affected = conn.execute(
        f"SELECT COUNT(*) FROM imported_transactions WHERE {where}", tokens
    ).fetchone()[0]
    if affected:
        conn.execute(f"UPDATE imported_transactions SET amount = -amount WHERE {where}", tokens)
    return int(affected)


# Labels the removed built-in formats stamped on rows before fingerprinting them.
# Rows imported back then still carry these keys, so the repair below looks for
# them. Each says what kind of export it was and names no institution; templates
# use their account's name, which the repair's candidates already cover.
BUILT_IN_IMPORT_LABELS = ("Checking", "Visa", "Card")
FINGERPRINT_MAX_OCCURRENCE = 4


def migrate_card_payment_fingerprints(conn: duckdb.DuckDBPyConnection) -> Dict[str, int]:
    """Re-key card payments whose sign migrate_card_payment_signs flipped, and
    remove the duplicates the stale keys already let in. Returns counts.

    A fingerprint hashes the signed amount, so a payment flipped to positive
    still carries the key of its old negative self. Re-importing the same export
    computes the positive key, finds no match, and inserts the payment a second
    time — which pays the card down twice and makes the next statement look
    overpaid.

    The key also hashes the export's label, which is not stored, so it is
    recovered by trying every label the row could have been imported under. A
    row is only touched when a candidate reproduces its stored key exactly, so
    nothing is re-keyed or removed on a guess. Of a proven duplicate pair the
    later import goes, unless something references it; then the earlier one
    goes instead, and if both are referenced both stay."""
    candidates = set(BUILT_IN_IMPORT_LABELS)
    for sql in (
        "SELECT DISTINCT name FROM accounts",
        "SELECT DISTINCT account FROM imported_transactions",
        "SELECT DISTINCT COALESCE(NULLIF(display_label, ''), label) FROM import_account_links",
    ):
        candidates.update(str(row[0]) for row in conn.execute(sql).fetchall() if row[0])

    rows = conn.execute(
        """
        SELECT id, user_id, tx_date, amount, description, fingerprint
        FROM imported_transactions
        WHERE is_transfer AND amount > 0
          AND account_id IN (SELECT id FROM accounts WHERE account_type = 'credit_card')
        ORDER BY id
        """
    ).fetchall()
    referenced = {
        int(row[0])
        for row in conn.execute(
            "SELECT imported_transaction_id FROM expected_reconciliations "
            "UNION SELECT imported_transaction_id FROM transaction_splits"
        ).fetchall()
    }

    rekeyed = removed = 0
    for tx_id, user_id, tx_date, amount, description, stored in rows:
        tx_date = tx_date.date() if isinstance(tx_date, datetime) else tx_date
        current_key = None
        for label in candidates:
            for occurrence in range(FINGERPRINT_MAX_OCCURRENCE):
                legacy = {"account": label, "tx_date": tx_date, "amount": -amount, "description": description}
                if compute_import_fingerprint(legacy, occurrence) == stored:
                    current_key = compute_import_fingerprint({**legacy, "amount": amount}, occurrence)
                    break
            if current_key:
                break
        if current_key is None:
            continue  # already current, or imported under a label we cannot recover

        twin = conn.execute(
            "SELECT id FROM imported_transactions WHERE user_id = ? AND fingerprint = ? AND id <> ?",
            [user_id, current_key, tx_id],
        ).fetchone()
        if twin is None:
            conn.execute(
                "UPDATE imported_transactions SET fingerprint = ? WHERE id = ?", [current_key, tx_id]
            )
            rekeyed += 1
        elif int(twin[0]) not in referenced:
            conn.execute("DELETE FROM imported_transactions WHERE id = ?", [int(twin[0])])
            conn.execute(
                "UPDATE imported_transactions SET fingerprint = ? WHERE id = ?", [current_key, tx_id]
            )
            removed += 1
        elif tx_id not in referenced:
            conn.execute("DELETE FROM imported_transactions WHERE id = ?", [tx_id])
            removed += 1
    return {"rekeyed": rekeyed, "removed": removed}


# The layouts the removed built-in formats read, as templates. Checking and
# Visa exports shared one layout. Column names are the exports' own; none names
# an institution.
LEGACY_TEMPLATES = (
    {"name": "Checking and Visa export", "date_field": "Processed Date", "description_field": "Description",
     "type_field": "Credit or Debit", "credit_value": "Credit", "amount_field": "Amount", "amount_sign": "standard"},
    {"name": "Card export", "date_field": "Clearing Date", "date_fallback_field": "Transaction Date",
     "description_field": "Description", "merchant_field": "Merchant", "type_field": "Type",
     "amount_field": "Amount (USD)", "amount_sign": "inverted"},
)
LEGACY_TEMPLATE_FIELDS = ("name", "date_field", "date_fallback_field", "description_field", "merchant_field",
                          "type_field", "credit_value", "amount_field", "amount_sign")


def migrate_legacy_import_templates(conn: duckdb.DuckDBPyConnection) -> None:
    """On a database's first start with templates, give each user who already
    imported the old built-in layouts as templates, and set each account's
    default where its rows show which layout it used. Runs once per database, so
    a later user never gets them and a template the user deletes stays deleted."""
    if conn.execute("SELECT 1 FROM applied_migrations WHERE name = 'legacy_templates'").fetchone():
        return
    conn.execute("INSERT INTO applied_migrations (name) VALUES ('legacy_templates')")
    users = conn.execute("SELECT DISTINCT user_id FROM imported_transactions").fetchall()
    for (user_id,) in users:
        ids = {}
        for template in LEGACY_TEMPLATES:
            if conn.execute("SELECT 1 FROM import_templates WHERE user_id = ? AND LOWER(name) = LOWER(?)",
                            [user_id, template["name"]]).fetchone():
                continue
            ids[template["name"]] = conn.execute(
                f"INSERT INTO import_templates (user_id, {', '.join(LEGACY_TEMPLATE_FIELDS)}) "
                f"VALUES (?, {', '.join('?' for _ in LEGACY_TEMPLATE_FIELDS)}) RETURNING id",
                [user_id, *[template.get(field) for field in LEGACY_TEMPLATE_FIELDS]],
            ).fetchone()[0]
        shared, cleared = ids.get("Checking and Visa export"), ids.get("Card export")
        for account_id, account_type in conn.execute(
                "SELECT id, account_type FROM accounts WHERE user_id = ? AND default_template_id IS NULL",
                [user_id]).fetchall():
            types = {row[0] or "" for row in conn.execute(
                "SELECT DISTINCT raw_type FROM imported_transactions WHERE user_id = ? AND account_id = ?",
                [user_id, account_id]).fetchall()}
            if not types:
                continue
            # Only the cleared-card export typed rows as anything but Credit/Debit.
            default = cleared if account_type == "credit_card" and types - {"Credit", "Debit", ""} else shared
            if default:
                conn.execute("UPDATE accounts SET default_template_id = ? WHERE id = ?", [default, account_id])


def migrate_categories_to_user_scoped_uniqueness(
    conn: duckdb.DuckDBPyConnection, default_user_id: int
) -> None:
    if not categories_has_global_name_unique(conn):
        return

    conn.execute("DROP TABLE IF EXISTS categories_user_scope_tmp")
    conn.execute(
        """
        CREATE TABLE categories_user_scope_tmp (
            id BIGINT PRIMARY KEY DEFAULT nextval('categories_id_seq'),
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#64748b',
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.execute(
        """
        INSERT INTO categories_user_scope_tmp (id, name, color, user_id, created_at)
        SELECT id,
               name,
               COALESCE(NULLIF(TRIM(color), ''), ?),
               COALESCE(user_id, ?),
               created_at
        FROM categories
        """,
        [DEFAULT_CATEGORY_COLOR, default_user_id],
    )
    conn.execute("DROP TABLE categories")
    conn.execute("ALTER TABLE categories_user_scope_tmp RENAME TO categories")


def normalize_user_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not USER_SLUG_RE.match(cleaned):
        raise ValueError("User handle must use lowercase letters, numbers, _ or -, and start with a letter/number")
    return cleaned


def _looks_like_email(value: str) -> bool:
    """One @, something before it, no spaces, and a domain with a dot that has
    something on both sides. Plain string checks rather than a pattern, so the
    time taken grows with the length of the input, not its square."""
    local, at, domain = value.partition("@")
    if not at or not local or "@" in domain or any(ch.isspace() for ch in value):
        return False
    head, dot, tail = domain.rpartition(".")
    return bool(dot and head and tail)


def normalize_user_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not cleaned:
        return ""
    if not _looks_like_email(cleaned):
        raise ValueError("Please enter a valid email address")
    return cleaned


def slugify_user_name(display_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", display_name.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("User name must include letters or numbers")
    return normalize_user_slug(cleaned[:32])


# Browsers drop these from a URL wherever they appear, so a check that reads the
# raw string sees something the browser never resolves.
_URL_IGNORED_CHARS = str.maketrans("", "", "\t\r\n")


def resolve_safe_redirect_target(target: str) -> str:
    """Reduce an attacker-controllable `next` to a same-site absolute path, or the
    dashboard. This is what stops a signed-in user being bounced off-site.

    Normalize the way a browser will before testing the shape: tab, newline and
    carriage return are removed wherever they sit, and a backslash reads as a
    forward slash in the authority position. Skipping that leaves `/\\host` and
    `/<tab>/host` passing a plain "starts with / but not //" test, only for the
    browser to resolve both as the protocol-relative `//host`.
    """
    cleaned = target.strip().translate(_URL_IGNORED_CHARS).replace("\\", "/")
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        return cleaned
    return "/dashboard"


def create_user_record(display_name: str, email: str) -> str:
    cleaned_name = display_name.strip()
    if not cleaned_name:
        raise ValueError("User name is required")
    normalized_email = normalize_user_email(email)

    base_slug = slugify_user_name(cleaned_name)
    with get_connection() as conn:
        index = 0
        chosen_slug = base_slug
        while True:
            existing = conn.execute(
                "SELECT id FROM users WHERE slug = ? LIMIT 1",
                [chosen_slug],
            ).fetchone()
            if not existing:
                break
            index += 1
            suffix = f"-{index}"
            allowed_prefix_length = max(1, 32 - len(suffix))
            chosen_slug = normalize_user_slug(f"{base_slug[:allowed_prefix_length]}{suffix}")

        conn.execute(
            "INSERT INTO users (slug, display_name, email) VALUES (?, ?, ?)",
            [chosen_slug, cleaned_name, normalized_email],
        )
    return chosen_slug


def delete_user_and_data(user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM amount_overrides WHERE bill_id IN (SELECT id FROM bills WHERE user_id = ?)", [user_id])
        for table in (
            "bills", "one_time_items", "imported_transactions", "expected_reconciliations", "expected_match_rules",
            "category_rules", "transaction_splits", "saved_ranges", "sessions", "card_statements",
            "transfer_rules", "inbox_actions", "dismissed_suggestions", "import_templates", "import_uploads",
            "import_account_links", "accounts", "categories",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM settings WHERE key LIKE ?", [f"user:{user_id}:%"])
        conn.execute("DELETE FROM users WHERE id = ?", [user_id])


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM duckdb_tables() WHERE table_name = ?", [table_name]).fetchone())


# Every table and index of this version. A new database is made from this; an
# older one is brought to it by legacy_schema.py and then the steps in
# migrations.py, after which this only fills in indexes.
SEQUENCES = (
    "users", "categories", "bills", "one_time_items", "amount_overrides", "imported_transactions",
    "expected_reconciliations", "expected_match_rules", "accounts", "import_templates", "import_uploads",
    "category_rules", "transaction_splits", "sessions", "card_statements", "transfer_rules",
    "inbox_actions", "saved_ranges",
)
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY DEFAULT nextval('users_id_seq'),
        slug TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        -- NULL means "not set yet", which puts the app into first-run setup.
        password_hash TEXT,
        failed_attempts INTEGER DEFAULT 0,
        locked_until TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # One level: a category with a parent has no children (#100). Its budget is
    # its expected transactions, so it has no budget of its own (#104).
    migrations.CATEGORIES_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    # Scheduled money out (kind 'expense') and in (kind 'income'). A bill recurs
    # on the day of first_due_date. balance, interest_rate, biller_url and the
    # charge_ columns belong to bills in depth.
    """
    CREATE TABLE IF NOT EXISTS bills (
        id BIGINT PRIMARY KEY DEFAULT nextval('bills_id_seq'),
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        amount DOUBLE NOT NULL,
        first_due_date DATE NOT NULL,
        end_date DATE,
        frequency_type TEXT NOT NULL,
        interval_months INTEGER,
        semimonthly_day1 INTEGER,
        semimonthly_day2 INTEGER,
        category_id BIGINT,
        account_id BIGINT,
        user_id BIGINT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        balance DOUBLE,
        balance_as_of DATE,
        interest_rate DOUBLE,
        biller_url TEXT,
        charge_frequency TEXT,
        charge_first_date DATE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # A single expected transaction.
    """
    CREATE TABLE IF NOT EXISTS one_time_items (
        id BIGINT PRIMARY KEY DEFAULT nextval('one_time_items_id_seq'),
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        amount DOUBLE NOT NULL,
        expected_date DATE NOT NULL,
        category_id BIGINT,
        account_id BIGINT,
        user_id BIGINT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # A bill's amount from a date on.
    """
    CREATE TABLE IF NOT EXISTS amount_overrides (
        id BIGINT PRIMARY KEY DEFAULT nextval('amount_overrides_id_seq'),
        bill_id BIGINT NOT NULL,
        effective_date DATE NOT NULL,
        amount DOUBLE NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # category_via says how a row got its category: 'rule' (set by the rule in
    # category_rule_id, so safe to work out again) or NULL (picked by hand; a
    # rule never changes it). transfer_rule_id is the transfer rule that marked
    # it. unexpected is the owner's explicit mark, with an optional note.
    migrations.IMPORTED_TRANSACTIONS_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    # An actual linked to the expected transaction it settles. source_type is
    # 'bill' or 'one_time'.
    """
    CREATE TABLE IF NOT EXISTS expected_reconciliations (
        id BIGINT PRIMARY KEY DEFAULT nextval('expected_reconciliations_id_seq'),
        user_id BIGINT NOT NULL,
        imported_transaction_id BIGINT NOT NULL,
        source_type TEXT NOT NULL,
        source_id BIGINT NOT NULL,
        matched_via TEXT NOT NULL DEFAULT 'confirm',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS expected_match_rules (
        id BIGINT PRIMARY KEY DEFAULT nextval('expected_match_rules_id_seq'),
        user_id BIGINT NOT NULL,
        source_type TEXT NOT NULL,
        source_id BIGINT NOT NULL,
        pattern TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        amount_min DOUBLE,
        amount_max DOUBLE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id BIGINT PRIMARY KEY DEFAULT nextval('sessions_id_seq'),
        user_id BIGINT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS saved_ranges (
        id BIGINT PRIMARY KEY DEFAULT nextval('saved_ranges_id_seq'),
        user_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        rule TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transaction_splits (
        id BIGINT PRIMARY KEY DEFAULT nextval('transaction_splits_id_seq'),
        user_id BIGINT NOT NULL,
        imported_transaction_id BIGINT NOT NULL,
        category_id BIGINT NOT NULL,
        amount DOUBLE NOT NULL,
        note TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS category_rules (
        id BIGINT PRIMARY KEY DEFAULT nextval('category_rules_id_seq'),
        user_id BIGINT NOT NULL,
        category_id BIGINT NOT NULL,
        pattern TEXT NOT NULL,
        amount_min DOUBLE,
        amount_max DOUBLE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        match_on TEXT DEFAULT 'description',
        date_from DATE,
        date_to DATE
    )
    """,
    # payment_pattern is how a payment to this card appears on the funding
    # account's statement. Per account, so no issuer name lives in the code.
    # paid_from_account_id: the checking account that pays a card's statements.
    migrations.ACCOUNTS_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    # Templates stand alone, shared by any number of accounts; an account
    # points at its default one.
    """
    CREATE TABLE IF NOT EXISTS import_templates (
        id BIGINT PRIMARY KEY DEFAULT nextval('import_templates_id_seq'),
        user_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        account_id BIGINT,
        date_field TEXT NOT NULL,
        date_fallback_field TEXT,
        date_format TEXT,
        amount_field TEXT,
        amount_in_field TEXT,
        amount_out_field TEXT,
        amount_sign TEXT NOT NULL DEFAULT 'standard',
        type_field TEXT,
        credit_value TEXT,
        description_field TEXT NOT NULL,
        merchant_field TEXT,
        signature TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # One-time data steps already taken on this database (see migrations.py).
    """
    CREATE TABLE IF NOT EXISTS applied_migrations (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # A file uploaded while its template is being mapped. Deleted when the
    # template is saved, or a day later.
    """
    CREATE TABLE IF NOT EXISTS import_uploads (
        id BIGINT PRIMARY KEY DEFAULT nextval('import_uploads_id_seq'),
        user_id BIGINT NOT NULL,
        filename TEXT NOT NULL,
        content BLOB NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # One row per statement a card has closed (see sync_card_statements). Only
    # the dates and the issuer's printed balance live here; every other figure
    # is summed from imported rows so a re-import is never stale.
    """
    CREATE TABLE IF NOT EXISTS card_statements (
        id BIGINT PRIMARY KEY DEFAULT nextval('card_statements_id_seq'),
        user_id BIGINT NOT NULL,
        account_id BIGINT NOT NULL,
        close_date DATE NOT NULL,
        due_date DATE NOT NULL,
        issuer_balance DOUBLE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, account_id, close_date)
    )
    """,
    # Import labels are matched case-insensitively but shown back as written.
    """
    CREATE TABLE IF NOT EXISTS import_account_links (
        user_id BIGINT NOT NULL,
        label TEXT NOT NULL,
        display_label TEXT,
        account_id BIGINT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, label)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dismissed_suggestions (
        user_id BIGINT NOT NULL,
        suggestion_key TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT 'dismissed',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, suggestion_key)
    )
    """,
    # A merchant whose debits outside cards are transfers between the owner's
    # accounts, now and on later imports (see inbox.py).
    migrations.TRANSFER_RULES_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    # Every inbox action, with what Undo needs to reverse it (JSON).
    """
    CREATE TABLE IF NOT EXISTS inbox_actions (
        id BIGINT PRIMARY KEY DEFAULT nextval('inbox_actions_id_seq'),
        user_id BIGINT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_categories_user_id ON categories(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_user_name_unique ON categories(user_id, name)",
    "CREATE INDEX IF NOT EXISTS idx_bills_user_id ON bills(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_bills_account ON bills(user_id, account_id)",
    "CREATE INDEX IF NOT EXISTS idx_one_time_items_user_id ON one_time_items(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_one_time_items_account ON one_time_items(user_id, account_id)",
    "CREATE INDEX IF NOT EXISTS idx_imported_user_date ON imported_transactions(user_id, tx_date)",
    "CREATE INDEX IF NOT EXISTS idx_imported_user_account ON imported_transactions(user_id, account_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_fingerprint ON imported_transactions(user_id, fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_import_templates_user ON import_templates(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_import_unique "
    "ON expected_reconciliations(user_id, imported_transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_reconciliation_source ON expected_reconciliations(user_id, source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_match_rules_source ON expected_match_rules(user_id, source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_category_rules_user ON category_rules(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_category_rules_category ON category_rules(user_id, category_id)",
    "CREATE INDEX IF NOT EXISTS idx_transaction_splits_tx ON transaction_splits(user_id, imported_transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_name_unique ON accounts(user_id, name)",
)


def _create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for name in SEQUENCES:
        conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {name}_id_seq START 1")
    for statement in SCHEMA:
        conn.execute(statement)
    for statement in INDEXES:
        conn.execute(statement)


def _seed_defaults(conn: duckdb.DuckDBPyConnection) -> None:
    """The rows every database starts with: the default profile, an opening
    balance, and a per-user copy of any setting saved before settings were
    per user."""
    conn.execute(
        "INSERT INTO users (slug, display_name) VALUES (?, ?) ON CONFLICT DO NOTHING",
        [DEFAULT_USER_SLUG, DEFAULT_USER_DISPLAY_NAME],
    )
    default_user_row = conn.execute("SELECT id FROM users WHERE slug = ? LIMIT 1", [DEFAULT_USER_SLUG]).fetchone()
    if not default_user_row:
        default_user_row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    default_user_id = int(default_user_row[0])
    conn.execute("INSERT INTO settings (key, value) VALUES ('starting_balance', '0') ON CONFLICT DO NOTHING")
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key NOT LIKE 'user:%:%'").fetchall():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT DO NOTHING",
            [f"user:{default_user_id}:{key}", value],
        )


def init_db() -> None:
    """Bring the database to this version's shape, whatever version made it.

    A new database is created in that shape. An older one is first brought to
    the v0.4.0 shape by the steps that version ran at every start, and then
    taken the rest of the way by the named steps in migrations.py, each of
    which runs once, inside a transaction."""
    with get_connection() as conn:
        new_database = not _table_exists(conn, "users")
        if not new_database:
            step_names = {name for name, _step in migrations.STEPS}
            if not migrations.applied(conn) & step_names:
                legacy_schema.bring_to_v0_4_0(conn)
            migrations.run(conn, migrations.STEPS)
            # Guarded by its own record, and reads only tables no step renames.
            migrate_legacy_import_templates(conn)
        _create_schema(conn)
        if new_database:
            migrations.record(conn, migrations.STEPS)
        _seed_defaults(conn)


def load_all_users() -> List[Dict[str, Any]]:
    """All profiles, each flagged with whether it can actually sign in. A profile
    without a password is inert — its data is there but nobody can reach it."""
    rows = query_all("""
            SELECT id, slug, display_name, email,
                   (password_hash IS NOT NULL) AS has_password
            FROM users
            ORDER BY LOWER(display_name), id
            """)
    for row in rows:
        row["has_password"] = bool(row["has_password"])
    return rows


def load_user_by_slug(user_slug: str) -> Optional[Dict[str, Any]]:
    row = fetch_one("""
            SELECT id, slug, display_name, email
            FROM users
            WHERE slug = ?
            LIMIT 1
            """, [user_slug])
    if not row:
        return None
    return {"id": int(row[0]), "slug": row[1], "display_name": row[2], "email": row[3] or ""}


def load_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_one("""
            SELECT id, slug, display_name, email
            FROM users
            WHERE id = ?
            LIMIT 1
            """, [user_id])
    if not row:
        return None
    return {"id": int(row[0]), "slug": row[1], "display_name": row[2], "email": row[3] or ""}


def get_default_user() -> Dict[str, Any]:
    default_user = load_user_by_slug(DEFAULT_USER_SLUG)
    if default_user:
        return default_user
    row = fetch_one("SELECT id, slug, display_name, email FROM users ORDER BY id LIMIT 1")
    if row:
        return {"id": int(row[0]), "slug": row[1], "display_name": row[2], "email": row[3] or ""}
    raise RuntimeError("No users available; database initialization failed")


def get_current_user(request: Request) -> Dict[str, Any]:
    """The signed-in user. The session gate in app.py resolves this before any
    route runs, so reaching here without one means the gate was bypassed —
    which is a bug, not a case to paper over with a default user."""
    cached = getattr(request.state, "current_user", None)
    if cached:
        return cached
    raise RuntimeError("No authenticated user on the request; the session gate did not run")


# ---------------------------------------------------------------------------
# Authentication
#
# Passwords are scrypt-hashed with a per-user random salt, using stdlib hashlib
# and secrets — no new dependency, in keeping with how CSV import stays on the
# stdlib. Sessions are server-side rows keyed by the SHA-256 of a random token:
# the cookie holds the token, the database holds only its digest, so a leaked
# database file yields no usable session cookies.
#
# Identity comes from the session and nothing else. There is deliberately no
# ?user= override and no default-user fallback — both were identity bypasses.
# ---------------------------------------------------------------------------

PASSWORD_MIN_LENGTH = 10
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SESSION_COOKIE = "metis_session"
SESSION_TTL_DAYS = 30
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_MINUTES = 15


def _scrypt_maxmem(n: int, r: int) -> int:
    """OpenSSL caps scrypt memory at 32 MB by default and raises rather than
    degrading, while n=2**15, r=8 needs ~33 MB. Derive the ceiling from the
    parameters (with headroom) so raising the work factor later still works."""
    return 128 * n * r * 2


def hash_password(password: str) -> str:
    """scrypt with a fresh 16-byte salt. Stored as a self-describing string so
    the work factors can be raised later without invalidating old hashes."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
        maxmem=_scrypt_maxmem(SCRYPT_N, SCRYPT_R),
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(derived).decode()
    )


def verify_password(password: str, stored: Optional[str]) -> bool:
    """Constant-time check against a stored hash, honoring that hash's own work
    factors rather than the current constants."""
    if not stored:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        n, r, p_factor = int(n_raw), int(r_raw), int(p_raw)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=n, r=r, p=p_factor,
            dklen=len(expected),
            maxmem=_scrypt_maxmem(n, r),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(derived, expected)


def validate_password(password: str, confirm: Optional[str] = None) -> str:
    """Length is the only rule worth enforcing — composition rules push people
    toward predictable substitutions without adding real entropy."""
    if confirm is not None and password != confirm:
        raise ValueError("Those passwords don't match")
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Use at least {PASSWORD_MIN_LENGTH} characters")
    return password


def set_user_password(user_id: int, password: str, confirm: Optional[str] = None) -> None:
    """Set a password and sign out everywhere. Any other live session could be
    whoever you are changing the password because of."""
    validate_password(password, confirm)
    execute(
        "UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
        [hash_password(password), user_id],
    )
    destroy_user_sessions(user_id)


def any_user_has_password() -> bool:
    """False on a database that predates auth, which routes everything to setup."""
    row = fetch_one("SELECT COUNT(*) FROM users WHERE password_hash IS NOT NULL")
    return bool(row and int(row[0]) > 0)


def users_needing_password() -> List[Dict[str, Any]]:
    return query_all(
        "SELECT id, slug, display_name, email FROM users "
        "WHERE password_hash IS NULL ORDER BY id"
    )


def _lockout_remaining(locked_until: Any) -> int:
    if not locked_until:
        return 0
    if isinstance(locked_until, date) and not isinstance(locked_until, datetime):
        locked_until = datetime.combine(locked_until, datetime.min.time())
    remaining = (locked_until - datetime.now()).total_seconds()
    return max(0, int(remaining // 60) + 1) if remaining > 0 else 0


def authenticate(identifier: str, password: str) -> Dict[str, Any]:
    """Verify a sign-in by slug, display name, or email.

    Raises ValueError with a message safe to show. Failures are counted per user
    and trigger a timed lockout, because a login form on a LAN with no rate limit
    is brute-forceable. The wrong-credentials message is identical whether or not
    the account exists, so the form can't be used to enumerate users."""
    generic = "Those details don't match an account"
    cleaned = (identifier or "").strip()
    if not cleaned or not password:
        raise ValueError(generic)

    row = fetch_one(
        "SELECT id, slug, display_name, email, password_hash, failed_attempts, locked_until "
        "FROM users WHERE LOWER(slug) = LOWER(?) OR LOWER(display_name) = LOWER(?) "
        "   OR (email <> '' AND LOWER(email) = LOWER(?)) LIMIT 1",
        [cleaned, cleaned, cleaned],
    )
    if not row:
        # Spend comparable time so a missing account isn't detectably faster.
        verify_password(password, hash_password("timing-equalizer"))
        raise ValueError(generic)

    user_id, slug, display_name, email, password_hash, failed, locked_until = row
    waiting = _lockout_remaining(locked_until)
    if waiting:
        raise ValueError(f"Too many attempts. Try again in {waiting} minute{'s' if waiting != 1 else ''}")

    if not verify_password(password, password_hash):
        attempts = int(failed or 0) + 1
        if attempts >= MAX_FAILED_ATTEMPTS:
            execute(
                "UPDATE users SET failed_attempts = 0, locked_until = ? WHERE id = ?",
                [datetime.now() + timedelta(minutes=LOCKOUT_MINUTES), int(user_id)],
            )
            raise ValueError(f"Too many attempts. Try again in {LOCKOUT_MINUTES} minutes")
        execute("UPDATE users SET failed_attempts = ? WHERE id = ?", [attempts, int(user_id)])
        raise ValueError(generic)

    execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", [int(user_id)]
    )
    return {"id": int(user_id), "slug": slug, "display_name": display_name, "email": email or ""}


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int) -> str:
    """Issue a session and return the raw token, which is never stored."""
    token = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        [user_id, _token_digest(token), datetime.now() + timedelta(days=SESSION_TTL_DAYS)],
    )
    return token


def load_session_user(token: str) -> Optional[Dict[str, Any]]:
    """Resolve a session cookie to its user, sliding the expiry forward so active
    use doesn't get logged out on a fixed schedule. Expired rows are deleted on
    sight rather than left to accumulate."""
    if not token:
        return None
    digest = _token_digest(token)
    row = fetch_one(
        "SELECT s.user_id, s.expires_at, u.slug, u.display_name, u.email "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? LIMIT 1",
        [digest],
    )
    if not row:
        return None

    user_id, expires_at, slug, display_name, email = row
    if isinstance(expires_at, date) and not isinstance(expires_at, datetime):
        expires_at = datetime.combine(expires_at, datetime.min.time())
    if expires_at <= datetime.now():
        execute("DELETE FROM sessions WHERE token_hash = ?", [digest])
        return None

    execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
        [datetime.now(), datetime.now() + timedelta(days=SESSION_TTL_DAYS), digest],
    )
    return {"id": int(user_id), "slug": slug, "display_name": display_name, "email": email or ""}


def destroy_session(token: str) -> None:
    if token:
        execute("DELETE FROM sessions WHERE token_hash = ?", [_token_digest(token)])


def destroy_user_sessions(user_id: int) -> None:
    execute("DELETE FROM sessions WHERE user_id = ?", [user_id])


def purge_expired_sessions() -> None:
    execute("DELETE FROM sessions WHERE expires_at <= ?", [datetime.now()])


def secure_cookies_enabled() -> bool:
    """Set METIS_SECURE_COOKIES=1 once Metis is served over HTTPS. Left off by
    default because a Secure cookie is simply never sent over plain HTTP, which
    would lock out a LAN-only deployment with no visible reason."""
    return os.getenv("METIS_SECURE_COOKIES", "").strip().lower() in {"1", "true", "yes"}


def attach_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=secure_cookies_enabled(),
        path=configured_base_path() or "/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=configured_base_path() or "/")


def template_context(request: Request, message: str, err: int, **kwargs: Any) -> Dict[str, Any]:
    signed_in = getattr(request.state, "current_user", None)
    context = {
        "request": request,
        "message": message,
        "is_error": bool(err),
        # Sign-in and first-run setup render without a user — and deliberately
        # without the user list, which would let the login page enumerate accounts.
        "current_user": signed_in or {"display_name": "", "email": "", "slug": ""},
        "users": load_all_users() if signed_in else [],
        "chrome": signed_in is not None,
    }
    context.update(kwargs)
    return context


def rows_to_dicts(cursor: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _run(conn: duckdb.DuckDBPyConnection, sql: str, params: Optional[Sequence[Any]]):
    return conn.execute(sql) if params is None else conn.execute(sql, params)


def query_all(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Open a connection, run a SELECT, return rows as dicts."""
    with get_connection() as conn:
        return rows_to_dicts(_run(conn, sql, params))


def fetch_one(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[tuple]:
    """Open a connection, run a query, return the first raw row tuple (or None)."""
    with get_connection() as conn:
        return _run(conn, sql, params).fetchone()


def fetch_all(sql: str, params: Optional[Sequence[Any]] = None) -> List[tuple]:
    """Open a connection, run a query, return all raw row tuples."""
    with get_connection() as conn:
        return _run(conn, sql, params).fetchall()


def execute(sql: str, params: Optional[Sequence[Any]] = None) -> None:
    """Open a connection and run a single write statement."""
    with get_connection() as conn:
        _run(conn, sql, params)


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_optional_date(value: str) -> Optional[date]:
    cleaned = value.strip()
    if not cleaned:
        return None
    return parse_iso_date(cleaned)


def parse_positive_float(value: str) -> float:
    amount = float(value)
    if amount <= 0:
        raise ValueError("Amount must be greater than 0")
    return amount


def parse_optional_float(value: str) -> Optional[float]:
    """Blank -> None, meaning 'no value' (a cleared budget, an unbounded rule).
    Tolerates the $ and thousands separators people paste in."""
    cleaned = (value or "").strip().replace(",", "").lstrip("$").strip()
    if not cleaned:
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        raise ValueError("Enter a valid amount")
    if amount < 0:
        raise ValueError("Amount cannot be negative")
    return amount


def parse_optional_balance(value: str) -> Optional[float]:
    """Like parse_optional_float, but a card can carry a credit, so a balance may
    be negative ("-$12.00")."""
    cleaned = (value or "").strip().replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        raise ValueError("Enter a valid balance")


def parse_day(value: str, fallback: int) -> int:
    cleaned = value.strip()
    if not cleaned:
        return fallback
    day = int(cleaned)
    if day < 1 or day > 31:
        raise ValueError("Day values must be between 1 and 31")
    return day


def parse_positive_int(value: str, fallback: int) -> int:
    cleaned = value.strip()
    if not cleaned:
        return fallback
    parsed = int(cleaned)
    if parsed < 1:
        raise ValueError("Value must be at least 1")
    return parsed


def parse_form_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def redirect_with_message(
    path: str, message: str, is_error: bool = False, undo: Optional[int] = None
) -> RedirectResponse:
    """Redirect within this site with a message, and an Undo when an action id
    is given. A #fragment stays at the end, so the page opens at that place."""
    path, has_fragment, fragment = resolve_safe_redirect_target(path).partition("#")
    separator = "&" if "?" in path else "?"
    url = f"{path}{separator}msg={quote_plus(message)}&err={1 if is_error else 0}"
    if undo is not None:
        url += f"&undo={int(undo)}"
    if has_fragment:
        url += f"#{fragment}"
    return RedirectResponse(url=url, status_code=303)


def build_setting_storage_key(user_id: int, key: str) -> str:
    return f"user:{user_id}:{key}"


def get_setting_value(user_id: int, key: str) -> Optional[str]:
    storage_key = build_setting_storage_key(user_id, key)
    row = fetch_one("SELECT value FROM settings WHERE key = ?", [storage_key])
    if not row or row[0] is None:
        return None
    return str(row[0])


def set_setting_value(user_id: int, key: str, value: str) -> None:
    storage_key = build_setting_storage_key(user_id, key)
    execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """, [storage_key, value])


def get_setting_float(user_id: int, key: str, default: float = 0.0) -> float:
    raw_value = get_setting_value(user_id, key)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def set_setting_float(user_id: int, key: str, value: float) -> None:
    set_setting_value(user_id, key, str(value))


def parse_optional_iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return parse_iso_date(cleaned)
    except ValueError:
        return None


def load_range_rule(user_id: int) -> RangeRule:
    """The saved date range rule. An older two-date value reads as day/absolute,
    the oldest per-key dates likewise, and anything unreadable as the default."""
    raw = get_setting_value(user_id, FORECAST_WINDOW_SETTINGS_KEY)
    if raw:
        try:
            rule = rule_from_saved(json.loads(raw))
        except json.JSONDecodeError:
            rule = None
        if rule:
            return rule
    legacy = rule_from_saved({
        "start": get_setting_value(user_id, FORECAST_WINDOW_FALLBACK_START_KEY),
        "end": get_setting_value(user_id, FORECAST_WINDOW_FALLBACK_END_KEY),
    })
    return legacy or DEFAULT_RULE


def save_range_rule(user_id: int, rule: RangeRule) -> None:
    set_setting_value(user_id, FORECAST_WINDOW_SETTINGS_KEY, json.dumps(rule_to_saved(rule)))


def save_forecast_window(user_id: int, window_start: date, window_end: date) -> None:
    """Save two fixed dates as a day/absolute rule."""
    save_range_rule(user_id, RangeRule("day", "absolute", window_start.isoformat(), window_end.isoformat()))


def resolve_forecast_window(
    user_id: int, start: str = "", end: str = "", today: Optional[date] = None
) -> tuple[date, date, bool]:
    """The range a page shows: the saved rule resolved for today, with any
    ?start=/?end= from a link taking over that side for this view only. Link
    dates are never saved. The flag is True when a link date didn't parse."""
    saved_start, saved_end = resolve_rule(load_range_rule(user_id), today or date.today())
    invalid_filter = False
    try:
        window_start = parse_iso_date(start) if start else saved_start
        window_end = parse_iso_date(end) if end else saved_end
    except ValueError:
        window_start, window_end = saved_start, saved_end
        invalid_filter = True

    if window_end < window_start:
        window_start, window_end = window_end, window_start
    return window_start, window_end, invalid_filter


def _saved_range_row(row: tuple) -> Optional[Dict[str, Any]]:
    range_id, name, raw_rule, created_at = row
    try:
        rule = rule_from_saved(json.loads(raw_rule))
    except json.JSONDecodeError:
        rule = None
    if not rule:
        return None
    return {"id": int(range_id), "name": name, "created": created_at.date().isoformat(), "rule": rule_to_saved(rule)}


def load_saved_ranges(user_id: int) -> List[Dict[str, Any]]:
    """A user's saved date filters, newest first. Rows whose rule no longer
    reads are left out rather than breaking the picker."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, rule, created_at FROM saved_ranges WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            [user_id],
        ).fetchall()
    return [saved for saved in map(_saved_range_row, rows) if saved]


def saved_range_name_taken(user_id: int, name: str) -> bool:
    row = fetch_one("SELECT 1 FROM saved_ranges WHERE user_id = ? AND lower(name) = lower(?)", [user_id, name])
    return row is not None


def save_saved_range(user_id: int, name: str, rule: RangeRule) -> Dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO saved_ranges (user_id, name, rule) VALUES (?, ?, ?) RETURNING id, name, rule, created_at",
            [user_id, name, json.dumps(rule_to_saved(rule))],
        ).fetchone()
    return _saved_range_row(row)


def delete_saved_range(user_id: int, range_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM saved_ranges WHERE id = ? AND user_id = ? RETURNING id", [range_id, user_id]
        ).fetchone()
    return row is not None


def range_first_year(user_id: int, today: date) -> int:
    """The first year the picker's dropdowns offer: the earliest transaction's
    year, or ten years back if that is earlier."""
    row = fetch_one("SELECT MIN(tx_date) FROM imported_transactions WHERE user_id = ?", [user_id])
    earliest = row[0].year if row and row[0] else today.year
    return min(earliest, today.year - 10)


LABEL_SEPARATOR = " › "


def category_join(alias: str, id_expr: str, user_expr: str, kind: str = "LEFT") -> str:
    """Join a category and its parent, for category_label(alias)."""
    return (f"{kind} JOIN categories {alias} ON {alias}.id = {id_expr} AND {alias}.user_id = {user_expr} "
            f"LEFT JOIN categories {alias}_parent ON {alias}_parent.id = {alias}.parent_id "
            f"AND {alias}_parent.user_id = {alias}.user_id")


def category_label(alias: str) -> str:
    """A category's name as shown: a sub-category reads with its parent
    (`food › groceries`). NULL when the join found no category."""
    return (f"CASE WHEN {alias}_parent.id IS NULL THEN {alias}.name "
            f"ELSE {alias}_parent.name || '{LABEL_SEPARATOR}' || {alias}.name END")


def load_categories(user_id: int) -> List[Dict[str, Any]]:
    """Every category, each parent followed by its children, by name. Each has
    its label and parent_id."""
    rows = query_all(f"""
            SELECT c.id, c.name, c.color, c.parent_id, {category_label("c")} AS label
            FROM categories c
            LEFT JOIN categories c_parent ON c_parent.id = c.parent_id AND c_parent.user_id = c.user_id
            WHERE c.user_id = ?
            ORDER BY LOWER(COALESCE(c_parent.name, c.name)), c.parent_id IS NOT NULL, LOWER(c.name), c.id
            """, [user_id])
    for row in rows:
        row["color"] = safe_hex_color(row.get("color"))
        row["parent_id"] = int(row["parent_id"]) if row["parent_id"] is not None else None
    return rows


def _check_parent(user_id: int, category_id: Optional[int], parent_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """The parent a category may take, or None for top-level. One level only:
    the parent must be top-level, and a category with children takes none."""
    if parent_id is None:
        return None
    parent = load_category_by_id(user_id, parent_id)
    if not parent:
        raise ValueError("parent not found")
    if category_id is not None and int(parent_id) == int(category_id):
        raise ValueError("a category can't be its own parent")
    if parent["parent_id"] is not None:
        raise ValueError("a sub-category can't have sub-categories")
    if category_id is not None and category_children(user_id, category_id):
        raise ValueError("a category with sub-categories can't have a parent")
    return parent


def create_category(user_id: int, name: str, color: Optional[str] = None, parent_id: Optional[int] = None) -> int:
    """Add a category and return its id. Names are unique per user, ignoring
    case, sub-categories included. A sub-category starts with its parent's
    color unless one is given."""
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ValueError("Category name is required")
    parent = _check_parent(user_id, None, parent_id)
    if color:
        normalized_color = normalize_hex_color(color)
    else:
        normalized_color = parent["color"] if parent else DEFAULT_CATEGORY_COLOR
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM categories WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1",
            [user_id, cleaned_name],
        ).fetchone()
        if existing:
            raise ValueError("A category with this name already exists")
        return int(conn.execute(
            "INSERT INTO categories (name, color, user_id, parent_id) VALUES (?, ?, ?, ?) RETURNING id",
            [cleaned_name, normalized_color, user_id, parent["id"] if parent else None],
        ).fetchone()[0])


def set_category_parent(user_id: int, category_id: int, parent_id: Optional[int]) -> None:
    """Move a category under a parent, or with None promote it to top-level."""
    _check_parent(user_id, category_id, parent_id)
    execute("UPDATE categories SET parent_id = ? WHERE id = ? AND user_id = ?", [parent_id, category_id, user_id])


def load_category_by_id(user_id: int, category_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_one("""
            SELECT id, name, color, parent_id
            FROM categories
            WHERE id = ? AND user_id = ?
            """, [category_id, user_id])
    if not row:
        return None
    return {
        "id": int(row[0]),
        "name": row[1],
        "color": safe_hex_color(row[2]),
        "parent_id": int(row[3]) if row[3] is not None else None,
    }


def category_children(user_id: int, category_id: int) -> List[int]:
    return [int(row[0]) for row in fetch_all(
        "SELECT id FROM categories WHERE user_id = ? AND parent_id = ? ORDER BY id", [user_id, category_id])]


def delete_category(user_id: int, category_id: int, children: str = "") -> None:
    """Delete a category, its rules and splits; what pointed at it is left
    without a category. A parent with children needs `children`: "move" makes
    them top-level, "delete" deletes them the same way."""
    kids = category_children(user_id, category_id)
    if kids and children not in ("move", "delete"):
        raise ValueError("choose what happens to its sub-categories")
    doomed = [category_id] + (kids if children == "delete" else [])
    with get_connection() as conn:
        if kids and children == "move":
            conn.execute("UPDATE categories SET parent_id = NULL WHERE user_id = ? AND parent_id = ?",
                         [user_id, category_id])
        for doomed_id in doomed:
            for table in ("bills", "one_time_items"):
                conn.execute(f"UPDATE {table} SET category_id = NULL WHERE category_id = ? AND user_id = ?",
                             [doomed_id, user_id])
            conn.execute(
                "UPDATE imported_transactions SET category_id = NULL, category_via = NULL, category_rule_id = NULL "
                "WHERE category_id = ? AND user_id = ?", [doomed_id, user_id])
            conn.execute("DELETE FROM category_rules WHERE category_id = ? AND user_id = ?", [doomed_id, user_id])
            conn.execute("DELETE FROM transaction_splits WHERE category_id = ? AND user_id = ?", [doomed_id, user_id])
            conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", [doomed_id, user_id])
    resync_category_rules(user_id)


def parse_optional_category_id(user_id: int, value: str) -> Optional[int]:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise ValueError("Invalid category selection") from exc
    if parsed < 1:
        raise ValueError("Invalid category selection")
    if not load_category_by_id(user_id, parsed):
        raise ValueError("Selected category does not exist")
    return parsed


def normalize_hex_color(value: str) -> str:
    cleaned = value.strip()
    if not HEX_COLOR_RE.match(cleaned):
        raise ValueError("Color must be a hex value like #3b82f6")
    return cleaned.lower()


def safe_hex_color(value: Any) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if HEX_COLOR_RE.match(cleaned):
            return cleaned.lower()
    return DEFAULT_CATEGORY_COLOR


def add_months(month_anchor: date, months: int) -> date:
    month_index = (month_anchor.year * 12 + (month_anchor.month - 1)) + months
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, 1)


def day_in_month(year: int, month: int, desired_day: int) -> date:
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(desired_day, max_day))


def iter_every_n_days(start_anchor: date, window_start: date, window_end: date, step_days: int):
    if window_end < start_anchor:
        return

    if window_start <= start_anchor:
        current = start_anchor
    else:
        days_since_start = (window_start - start_anchor).days
        jumps = (days_since_start + step_days - 1) // step_days
        current = start_anchor + timedelta(days=step_days * jumps)

    while current <= window_end:
        yield current
        current += timedelta(days=step_days)


def iter_monthly(start_anchor: date, window_start: date, window_end: date, interval_months: int, day: int):
    month_cursor = date(start_anchor.year, start_anchor.month, 1)
    end_month = date(window_end.year, window_end.month, 1)

    while month_cursor <= end_month:
        candidate = day_in_month(month_cursor.year, month_cursor.month, day)
        if candidate >= start_anchor and window_start <= candidate <= window_end:
            yield candidate
        month_cursor = add_months(month_cursor, interval_months)


def iter_semimonthly(start_anchor: date, window_start: date, window_end: date, day1: int, day2: int):
    start_point = start_anchor if start_anchor > window_start else window_start
    month_cursor = date(start_point.year, start_point.month, 1)
    end_month = date(window_end.year, window_end.month, 1)
    days = sorted({day1, day2})

    while month_cursor <= end_month:
        for day in days:
            candidate = day_in_month(month_cursor.year, month_cursor.month, day)
            if candidate >= start_anchor and window_start <= candidate <= window_end:
                yield candidate
        month_cursor = add_months(month_cursor, 1)


def _resolve_amount(
    base_amount: float, overrides: List[Dict[str, Any]], occurrence_date: date
) -> float:
    effective = base_amount
    for ovr in overrides:
        ovr_date = ovr["effective_date"]
        if isinstance(ovr_date, datetime):
            ovr_date = ovr_date.date()
        if ovr_date <= occurrence_date:
            effective = float(ovr["amount"])
        else:
            break
    return effective


def bill_due_dates(
    bill: Dict[str, Any],
    window_start: date,
    window_end: date,
    overrides: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """A bill's due dates inside the window, each with the amount due that day:
    {date, amount}. This is the one definition of a bill's schedule. Budget,
    the ledger forecast, card statements, the negative balance alert and the
    reconcile page all read it.

    Due dates run from first_due_date by the bill's frequency until its end
    date. Monthly, every-N-months and yearly bills fall on the first due
    date's day, or on the last day of a month too short to hold it."""
    first = _as_date(bill["first_due_date"])
    end_date = _as_date(bill["end_date"]) if bill.get("end_date") else None
    effective_start = max(window_start, first)
    effective_end = min(window_end, end_date) if end_date else window_end
    if effective_start > effective_end:
        return []

    frequency = bill["frequency_type"]
    if frequency == "weekly":
        dates = iter_every_n_days(first, effective_start, effective_end, 7)
    elif frequency == "biweekly":
        dates = iter_every_n_days(first, effective_start, effective_end, 14)
    elif frequency == "semimonthly":
        day1 = int(bill.get("semimonthly_day1") or 1)
        day2 = int(bill.get("semimonthly_day2") or 15)
        dates = iter_semimonthly(first, effective_start, effective_end, day1, day2)
    else:
        step = {"monthly": 1, "yearly": 12}.get(frequency) or int(bill.get("interval_months") or 1)
        dates = iter_monthly(first, effective_start, effective_end, step, first.day)

    base_amount = float(bill["amount"])
    return [
        {"date": due, "amount": _resolve_amount(base_amount, overrides or [], due)}
        for due in dates
    ]


def bill_item_type(bill: Dict[str, Any]) -> str:
    """What a scheduled row is called on the ledger: income, or a bill."""
    return "income" if bill["kind"] == "income" else "bill"


def generate_bill_transactions(
    bill: Dict[str, Any],
    window_start: date,
    window_end: date,
    overrides: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """A bill's due dates in the window as ledger rows."""
    kind = bill["kind"]
    rows: List[Dict[str, Any]] = []
    for due in bill_due_dates(bill, window_start, window_end, overrides):
        amount = due["amount"]
        rows.append(
            {
                "date": due["date"],
                "description": bill["name"],
                "type": bill_item_type(bill),
                "bill": "",
                "income": amount if kind == "income" else 0.0,
                "expense": amount if kind == "expense" else 0.0,
                "delta": amount if kind == "income" else -amount,
                "kind": kind,
                "id": bill["id"],
                "category_id": bill.get("category_id"),
                "category_name": bill.get("category_name") or "",
                "category_color": bill.get("category_color") or "",
                "account_id": bill.get("account_id"),
            }
        )
    return rows


BILL_COLUMNS = (
    "b.id, b.name, b.kind, b.amount, b.first_due_date, b.end_date, b.frequency_type, b.interval_months, "
    "b.semimonthly_day1, b.semimonthly_day2, b.active, b.created_at, b.category_id, b.account_id, "
    "b.balance, b.balance_as_of, b.interest_rate, b.biller_url, b.charge_frequency, b.charge_first_date, "
    f"{category_label('c')} AS category_name, c.color AS category_color"
)


def _load_bills(user_id: int, where: str = "", params: Sequence[Any] = (), order: str = "b.kind, b.name, b.id") -> List[Dict[str, Any]]:
    rows = query_all(f"""
            SELECT {BILL_COLUMNS}
            FROM bills b
            {category_join("c", "b.category_id", "b.user_id")}
            WHERE b.user_id = ? {where}
            ORDER BY {order}
            """, [user_id, *params])
    for row in rows:
        row["id"] = int(row["id"])
        row["category_color"] = safe_hex_color(row.get("category_color"))
        for key in ("first_due_date", "end_date", "balance_as_of", "charge_first_date"):
            if row.get(key) is not None:
                row[key] = _as_date(row[key])
    return rows


def load_all_bills(user_id: int) -> List[Dict[str, Any]]:
    """Every bill and scheduled income, active ones first."""
    return _load_bills(user_id, order="b.active DESC, b.kind, b.name, b.id")


def load_active_bills(user_id: int) -> List[Dict[str, Any]]:
    return _load_bills(user_id, "AND b.active = TRUE")


def load_bill_by_id(user_id: int, bill_id: int) -> Optional[Dict[str, Any]]:
    rows = _load_bills(user_id, "AND b.id = ?", [bill_id])
    return rows[0] if rows else None


ONE_TIME_COLUMNS = (
    "o.id, o.name, o.kind, o.amount, o.expected_date, o.category_id, o.account_id, "
    f"{category_label('c')} AS category_name, c.color AS category_color"
)


def _load_one_time_items(user_id: int, where: str = "", params: Sequence[Any] = (),
                         order: str = "o.expected_date, o.id") -> List[Dict[str, Any]]:
    rows = query_all(f"""
            SELECT {ONE_TIME_COLUMNS}
            FROM one_time_items o
            {category_join("c", "o.category_id", "o.user_id")}
            WHERE o.user_id = ? {where}
            ORDER BY {order}
            """, [user_id, *params])
    for row in rows:
        row["id"] = int(row["id"])
        row["amount"] = float(row["amount"])
        row["expected_date"] = _as_date(row["expected_date"])
        row["category_name"] = row.get("category_name") or ""
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_all_one_time_items(user_id: int) -> List[Dict[str, Any]]:
    """Every one-time item, newest first."""
    return _load_one_time_items(user_id, order="o.expected_date DESC, o.id DESC")


def load_one_time_item_by_id(user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    rows = _load_one_time_items(user_id, "AND o.id = ?", [item_id])
    return rows[0] if rows else None


def load_one_time_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """One-time items expected in the window, as ledger rows."""
    transactions: List[Dict[str, Any]] = []
    for item in _load_one_time_items(user_id, "AND o.expected_date >= ? AND o.expected_date <= ?", [window_start, window_end]):
        kind, amount = item["kind"], item["amount"]
        transactions.append(
            {
                "date": item["expected_date"],
                "description": item["name"],
                "type": "one-time",
                "bill": "",
                "income": amount if kind == "income" else 0.0,
                "expense": amount if kind == "expense" else 0.0,
                "delta": amount if kind == "income" else -amount,
                "kind": kind,
                "id": item["id"],
                "category_id": item.get("category_id"),
                "category_name": item["category_name"],
                "category_color": item["category_color"],
                "account_id": item.get("account_id"),
            }
        )
    return transactions


def load_amount_overrides(bill_id: int) -> List[Dict[str, Any]]:
    return query_all("SELECT id, bill_id, effective_date, amount FROM amount_overrides "
            "WHERE bill_id = ? ORDER BY effective_date ASC", [bill_id])


def load_amount_overrides_for_bills(bill_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not bill_ids:
        return {}
    placeholders = ", ".join("?" for _ in bill_ids)
    rows = query_all(f"SELECT id, bill_id, effective_date, amount FROM amount_overrides "
            f"WHERE bill_id IN ({placeholders}) ORDER BY effective_date ASC", bill_ids)
    result: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["bill_id"]), []).append(row)
    return result


def save_amount_override(bill_id: int, effective_date: date, amount: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM amount_overrides WHERE bill_id = ? AND effective_date = ?",
            [bill_id, effective_date],
        )
        conn.execute(
            "INSERT INTO amount_overrides (bill_id, effective_date, amount) VALUES (?, ?, ?)",
            [bill_id, effective_date, amount],
        )


def delete_overrides_for_bill(bill_id: int) -> None:
    execute("DELETE FROM amount_overrides WHERE bill_id = ?", [bill_id])


def summarize_frequency(bill: Dict[str, Any]) -> str:
    frequency = bill["frequency_type"]
    first = _as_date(bill["first_due_date"])
    if frequency == "weekly":
        return "Every week"
    if frequency == "biweekly":
        return "Every 2 weeks"
    if frequency == "semimonthly":
        return f"Twice monthly ({bill.get('semimonthly_day1') or 1}, {bill.get('semimonthly_day2') or 15})"
    if frequency == "monthly":
        return f"Monthly (day {first.day})"
    if frequency == "yearly":
        return f"Yearly ({first.strftime('%b')} {first.day})"
    interval = bill.get("interval_months") or 1
    return f"Every {interval} month(s) (day {first.day})"


# How close (in days) a checking payment and a card's own payment row must be to
# be treated as the same payment.
PAYMENT_MATCH_WINDOW_DAYS = 7

# A statement cycle runs about a month. A configured close landing within this
# many days of a statement that was recorded with a corrected close date is that
# same cycle (an issuer moving a close for a weekend), not a new one.
MIN_STATEMENT_CYCLE_DAYS = 20


LAST_DAY_SENTINEL = 0  # statement_day / due_day == 0 means "last day of month"


def _next_month_first(anchor: date) -> date:
    return add_months(date(anchor.year, anchor.month, 1), 1)


def resolve_cycle_day(day: Optional[int], year: int, month: int) -> date:
    """Resolve a configured cycle day to a real date in the given month.
    0 (LAST_DAY_SENTINEL) → the month's last day; None defaults to the 1st."""
    if day is None:
        day = 1
    day = int(day)
    if day <= LAST_DAY_SENTINEL:
        return day_in_month(year, month, 31)  # day_in_month clamps to the last day
    return day_in_month(year, month, day)


def card_cycle_for_charge(account: Dict[str, Any], charge_date: date) -> tuple[date, date]:
    """For a charge on a credit card, return (statement_close_date, payment_due_date).
    Close = next statement_day on/after the charge; due = next due_day after close.
    Supports 'last day of month' (stored as 0)."""
    statement_day = account.get("statement_day")

    close = resolve_cycle_day(statement_day, charge_date.year, charge_date.month)
    if close < charge_date:
        nm = _next_month_first(charge_date)
        close = resolve_cycle_day(statement_day, nm.year, nm.month)
    return close, _due_after_close(account, close)


def _due_after_close(account: Dict[str, Any], close: date) -> date:
    """The first configured due day after a statement closes."""
    due_day = account.get("due_day")
    due = resolve_cycle_day(due_day, close.year, close.month)
    if due <= close:
        nm = _next_month_first(close)
        due = resolve_cycle_day(due_day, nm.year, nm.month)
    return due


def cash_effect_date(account: Optional[Dict[str, Any]], charge_date: date) -> date:
    """When a charge actually moves checking cash: its own date for checking /
    unassigned items; the card's payment due date for credit-card items."""
    if account and account.get("account_type") == "credit_card":
        return card_cycle_for_charge(account, charge_date)[1]
    return charge_date


def account_cycle_status(account: Dict[str, Any], today: Optional[date] = None) -> Dict[str, date]:
    today = today or date.today()
    close, due = card_cycle_for_charge(account, today)
    return {"next_close": close, "next_due": due}


ACCOUNT_COLUMNS = ("id, name, account_type, statement_day, due_day, payment_pattern, default_template_id, "
                   "paid_from_account_id")


def load_accounts(user_id: int) -> List[Dict[str, Any]]:
    return query_all(f"SELECT {ACCOUNT_COLUMNS} FROM accounts "
                     "WHERE user_id = ? ORDER BY account_type, LOWER(name), id", [user_id])


def load_account_by_id(user_id: int, account_id: int) -> Optional[Dict[str, Any]]:
    rows = query_all(f"SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE id = ? AND user_id = ?", [account_id, user_id])
    if not rows:
        return None
    account = rows[0]
    account["payment_pattern"] = account["payment_pattern"] or ""
    return account


def load_accounts_map(user_id: int) -> Dict[int, Dict[str, Any]]:
    return {int(account["id"]): account for account in load_accounts(user_id)}


def default_checking_account(user_id: int) -> Optional[Dict[str, Any]]:
    """The user's checking account with the lowest id: the ledger's default
    account, and the one that pays any card with no paid-from of its own."""
    rows = query_all(f"SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE user_id = ? AND account_type = 'checking' "
                     "ORDER BY id LIMIT 1", [user_id])
    return rows[0] if rows else None


def paying_account(user_id: int, card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The checking account that pays a card's statements: the one it names, or
    the default checking account when it names none (or one that is gone)."""
    named = card.get("paid_from_account_id")
    if named:
        account = load_account_by_id(user_id, int(named))
        if account and account.get("account_type") == "checking":
            return account
    return default_checking_account(user_id)


def parse_optional_account_id(user_id: int, value: str) -> Optional[int]:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise ValueError("Invalid account selection") from exc
    if parsed < 1 or not load_account_by_id(user_id, parsed):
        raise ValueError("Selected account does not exist")
    return parsed


def _apply_cash_flow_deferral(
    rows: List[Dict[str, Any]],
    accounts: Dict[int, Dict[str, Any]],
    window_start: date,
    window_end: date,
) -> List[Dict[str, Any]]:
    """Keep non-card expected rows on their own dates (within window). Credit-card
    rows are dropped here and handled by forecast_card_payments (cycle-based and
    actual-aware) so nothing double-counts."""
    direct: List[Dict[str, Any]] = []
    for row in rows:
        account = accounts.get(row.get("account_id")) if row.get("account_id") else None
        if account and account.get("account_type") == "credit_card":
            continue
        if window_start <= row["date"] <= window_end:
            direct.append(row)
    return direct


def _account_scope_sql(account: Optional[Dict[str, Any]], prefix: str = "") -> tuple[str, List[Any]]:
    """SQL predicate + params selecting one account's imported rows by
    account_id, with a name fallback for rows not yet anchored (account_id
    NULL). With no account at all (a user with no checking account yet), the
    rows imported under the seeded name 'Checking'. `prefix` qualifies the
    imported_transactions columns (e.g. "i.")."""
    if account is None:
        return f"({prefix}account_id IS NULL AND {prefix}account = ?)", ["Checking"]
    return (f"({prefix}account_id = ? OR ({prefix}account_id IS NULL AND {prefix}account = ?))",
            [int(account["id"]), account["name"]])


def _same_account(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return int(a["id"]) == int(b["id"])


def is_default_checking(user_id: int, account: Optional[Dict[str, Any]]) -> bool:
    """Whether this is the account the starting balance and unassigned
    expected items belong to. No account at all stands in for it."""
    return _same_account(account, default_checking_account(user_id))


def _account_date(user_id: int, account: Optional[Dict[str, Any]], aggregate: str) -> Optional[date]:
    clause, params = _account_scope_sql(account)
    row = fetch_one(f"SELECT {aggregate}(tx_date) FROM imported_transactions WHERE user_id = ? AND {clause}",
                    [user_id, *params])
    return _as_date(row[0]) if row and row[0] is not None else None


def account_cutover(user_id: int, account: Optional[Dict[str, Any]]) -> Optional[date]:
    """The latest date imported for an account: its actuals run to here, and
    its forecast starts from here."""
    return _account_date(user_id, account, "MAX")


def latest_checking_actual_date(user_id: int) -> Optional[date]:
    return account_cutover(user_id, default_checking_account(user_id))


def account_first_date(user_id: int, account: Optional[Dict[str, Any]]) -> Optional[date]:
    """The earliest date imported for an account."""
    return _account_date(user_id, account, "MIN")


def first_checking_actual_date(user_id: int) -> Optional[date]:
    return account_first_date(user_id, default_checking_account(user_id))


def _ledger_line(is_transfer: bool, linked: bool, unexpected: bool) -> Optional[str]:
    """The line on an imported row: matched, marked unexpected, or not settled
    yet. A transfer, which card payments are, gets none."""
    if is_transfer:
        return None
    if linked:
        return "matched"
    if unexpected:
        return "unexpected"
    return "unsettled"


def _splits_for(user_id: int, tx_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Split parts for several transactions at once, oldest first."""
    if not tx_ids:
        return {}
    placeholders = ", ".join("?" for _ in tx_ids)
    rows = query_all(f"""
                SELECT ts.imported_transaction_id AS tx_id, ts.category_id, ts.amount,
                       {category_label("c")} AS category_name, c.color AS category_color
                FROM transaction_splits ts
                {category_join("c", "ts.category_id", "ts.user_id", "")}
                WHERE ts.user_id = ? AND ts.imported_transaction_id IN ({placeholders})
                ORDER BY ts.id
                """, [user_id, *tx_ids])
    found: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        found.setdefault(int(row["tx_id"]), []).append({
            "category_id": int(row["category_id"]),
            "category_name": row.get("category_name") or "",
            "category_color": safe_hex_color(row.get("category_color")),
            "amount": round(float(row["amount"]), 2),
        })
    return found


def load_actual_transactions(
    user_id: int, account: Optional[Dict[str, Any]], window_start: date, window_end: date
) -> List[Dict[str, Any]]:
    """One account's imported transactions mapped to the ledger row schema.
    Amounts are signed (negative = money out). `description` is the bank's text
    as imported; `bill` names the bill, income or one-time item it is matched
    to. A matched row takes that item's category unless it has none."""
    scope_clause, scope_params = _account_scope_sql(account, "i.")
    rows = query_all(f"""
                SELECT i.id, i.tx_date, i.description, i.merchant, i.amount, i.is_transfer, i.unexpected, i.note,
                       COALESCE(b.name, ot.name) AS expected_name,
                       ec.id AS expected_category_id,
                       {category_label("ec")} AS expected_category_name, ec.color AS expected_category_color,
                       ic.id AS import_category_id,
                       {category_label("ic")} AS import_category_name, ic.color AS import_category_color
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN bills b
                       ON r.source_type = 'bill' AND b.id = r.source_id AND b.user_id = i.user_id
                LEFT JOIN one_time_items ot
                       ON r.source_type = 'one_time' AND ot.id = r.source_id AND ot.user_id = i.user_id
                {category_join("ec", "COALESCE(b.category_id, ot.category_id)", "i.user_id")}
                {category_join("ic", "i.category_id", "i.user_id")}
                WHERE i.user_id = ? AND {scope_clause}
                  AND i.tx_date >= ? AND i.tx_date <= ?
                ORDER BY i.tx_date, i.id
                """, [user_id, *scope_params, window_start, window_end])
    splits = _splits_for(user_id, [int(row["id"]) for row in rows])

    transactions: List[Dict[str, Any]] = []
    for row in rows:
        amount = float(row["amount"])
        expected_name = row.get("expected_name")
        linked = bool(expected_name)
        prefix = "expected" if linked and row.get("expected_category_id") is not None else "import"
        category_id = row.get(f"{prefix}_category_id")
        is_transfer, unexpected = bool(row["is_transfer"]), bool(row["unexpected"])
        transactions.append(
            {
                "date": _as_date(row["tx_date"]),
                "description": row.get("description") or row.get("merchant") or "",
                "merchant": row.get("merchant") or row.get("description") or "",
                "bill": expected_name or "",
                "linked": linked,
                "type": "actual",
                "income": amount if amount > 0 else 0.0,
                "expense": -amount if amount < 0 else 0.0,
                "delta": amount,
                "kind": "income" if amount > 0 else "expense",
                "id": row["id"],
                "category_id": int(category_id) if category_id is not None else None,
                "category_name": row.get(f"{prefix}_category_name") or "",
                "category_color": safe_hex_color(row.get(f"{prefix}_category_color")),
                "is_transfer": is_transfer,
                "unexpected": unexpected,
                "note": row.get("note") or "",
                "splits": splits.get(int(row["id"]), []),
                "line": _ledger_line(is_transfer, linked, unexpected),
                "account_id": int(account["id"]) if account else None,
            }
        )
    return transactions


def load_actual_checking_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """The default checking account's imported transactions as ledger rows."""
    return load_actual_transactions(user_id, default_checking_account(user_id), window_start, window_end)


def _account_total_before(user_id: int, account: Optional[Dict[str, Any]], cutoff_date: date) -> float:
    clause, params = _account_scope_sql(account)
    row = fetch_one(f"""
            SELECT COALESCE(SUM(amount), 0) FROM imported_transactions
            WHERE user_id = ? AND {clause} AND tx_date < ?
            """, [user_id, *params, cutoff_date])
    return float(row[0] if row else 0.0)


def account_balance_before(user_id: int, account: Optional[Dict[str, Any]], cutoff_date: date) -> float:
    """An account's balance at the start of cutoff_date: the default checking
    account starts from the starting balance setting, any other from zero, and
    every imported row before the date counts. A card's is negative while money
    is owed: charges are stored negative and payments positive."""
    opening = get_setting_float(user_id, "starting_balance", 0.0) if is_default_checking(user_id, account) else 0.0
    return opening + _account_total_before(user_id, account, cutoff_date)


def checking_actual_balance_before(user_id: int, cutoff_date: date, opening_balance: float) -> float:
    """opening_balance + the default checking account's imported rows strictly
    before cutoff_date: the running-balance seed for any window start."""
    return float(opening_balance) + _account_total_before(user_id, default_checking_account(user_id), cutoff_date)


def _card_expected_charges(
    user_id: int, card_id: int, window_start: date, window_end: date
) -> List[Dict[str, Any]]:
    """Expected (bill and one-time) charges assigned to a card as {date, delta}."""
    charges: List[Dict[str, Any]] = []
    active_bills = load_active_bills(user_id)
    overrides_by_id = load_amount_overrides_for_bills([bill["id"] for bill in active_bills])
    for bill in active_bills:
        if bill.get("account_id") != card_id:
            continue
        for row in generate_bill_transactions(
            bill, window_start, window_end, overrides=overrides_by_id.get(bill["id"], [])
        ):
            charges.append(
                {
                    "date": row["date"],
                    "delta": float(row["delta"]),
                    "description": row.get("description") or "",
                    "kind": "bill",
                    "category_name": bill.get("category_name") or "",
                    "category_color": safe_hex_color(bill.get("category_color")),
                }
            )
    for row in load_one_time_transactions(user_id, window_start, window_end):
        if row.get("account_id") == card_id:
            charges.append(
                {
                    "date": row["date"],
                    "delta": float(row["delta"]),
                    "description": row.get("description") or "",
                    "kind": "one_time",
                    "category_name": row.get("category_name") or "",
                    "category_color": safe_hex_color(row.get("category_color")),
                }
            )
    return charges


# ---------------------------------------------------------------------------
# Credit card statements
#
# A statement is a record of its own, not a view over whatever dates a card is
# configured with today. Every cycle a card closes is written to card_statements
# with the close and due dates it actually had, so changing a card's cycle days
# later leaves its history alone and one statement's dates can be corrected by
# themselves.
#
# The figures are not stored. They are summed from imported rows every time, so
# an import that arrives late — or is re-run to fill a gap — lands in the
# statement it belongs to without anything to rebuild. The one figure a person
# can supply is the balance the issuer printed. That is ground truth: the next
# statement carries it in, and a statement whose imported rows do not add up to
# it says by how much, which is how a missing transaction shows itself.
# ---------------------------------------------------------------------------

STATEMENT_CENT = 0.005
STATEMENT_STATUS_LABELS = {
    "open": "Open",
    "estimated": "Estimated",
    "paid": "Paid",
    "partial": "Partly paid",
    "unpaid": "Unpaid",
    "past_due": "Past due",
    "no_balance": "No balance",
}


def card_cycle_configured(card: Dict[str, Any]) -> bool:
    return (
        card.get("account_type") == "credit_card"
        and card.get("statement_day") is not None
        and card.get("due_day") is not None
    )


def _close_before(card: Dict[str, Any], close: date) -> date:
    """The configured close in the month before `close`: where a statement with
    no recorded predecessor is taken to have opened."""
    prior = add_months(date(close.year, close.month, 1), -1)
    return resolve_cycle_day(card.get("statement_day"), prior.year, prior.month)


def _next_cycle(card: Dict[str, Any], after: date) -> tuple[date, date]:
    """(close, due) of the statement following one that closed on `after`. A
    configured close only days away is the cycle `after` already closed — one
    recorded with a corrected date — so the one after that is taken instead."""
    close, due = card_cycle_for_charge(card, after + timedelta(days=1))
    if (close - after).days < MIN_STATEMENT_CYCLE_DAYS:
        close, due = card_cycle_for_charge(card, close + timedelta(days=1))
    return close, due


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def _card_rows(user_id: int, card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every imported row on a card, oldest first, with its category."""
    clause, params = _account_scope_sql(card, "i.")
    rows = query_all(f"""
            SELECT i.id, i.tx_date, i.description, i.merchant, i.amount, i.is_transfer,
                   {category_label("c")} AS category_name, c.color AS category_color
            FROM imported_transactions i
            {category_join("c", "i.category_id", "i.user_id")}
            WHERE i.user_id = ? AND {clause}
            ORDER BY i.tx_date, i.id
            """, [user_id, *params])
    for row in rows:
        row["tx_date"] = _as_date(row["tx_date"])
        row["amount"] = float(row["amount"])
        row["is_transfer"] = bool(row["is_transfer"])
    return rows


def load_recorded_statements(user_id: int, account_id: int) -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT id, close_date, due_date, issuer_balance FROM card_statements "
        "WHERE user_id = ? AND account_id = ? ORDER BY close_date",
        [user_id, account_id],
    )
    for row in rows:
        row["close_date"] = _as_date(row["close_date"])
        row["due_date"] = _as_date(row["due_date"])
    return rows


def sync_card_statements(user_id: int, card: Dict[str, Any], today: Optional[date] = None) -> None:
    """Record every statement a card has closed — from the cycle holding its first
    imported row through the last one to close before today.

    History imported after the fact is backfilled in front of the first record.
    A statement already recorded is never re-dated, so neither a later import nor
    a change to the card's cycle days moves one."""
    if not card_cycle_configured(card):
        return
    today = today or date.today()
    clause, params = _account_scope_sql(card)
    row = fetch_one(
        f"SELECT MIN(tx_date) FROM imported_transactions WHERE user_id = ? AND {clause}",
        [user_id, *params],
    )
    first = _as_date(row[0]) if row and row[0] else None
    if first is None:
        return

    recorded = load_recorded_statements(user_id, int(card["id"]))
    new: List[tuple[date, date]] = []
    if recorded:
        earliest = recorded[0]["close_date"]
        while first <= _close_before(card, earliest):
            earliest = _close_before(card, earliest)
            new.append((earliest, _due_after_close(card, earliest)))
        close, due = _next_cycle(card, recorded[-1]["close_date"])
    else:
        close, due = card_cycle_for_charge(card, first)
    while close < today:
        new.append((close, due))
        close, due = _next_cycle(card, close)

    if new:
        # Pages sync as they load, so two can race to record the same statement.
        with get_connection() as conn:
            conn.executemany(
                "INSERT INTO card_statements (user_id, account_id, close_date, due_date) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [[user_id, int(card["id"]), close, due] for close, due in new],
            )


def _statement_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """An imported row as a line on a statement."""
    amount = row["amount"]
    if row["is_transfer"]:
        kind = "payment"
    elif amount > 0:
        kind = "credit"
    else:
        kind = "charge"
    return {
        "id": row["id"],
        "date": row["tx_date"],
        "description": row.get("merchant") or row.get("description") or "",
        "delta": amount,
        "kind": kind,
        "category_name": row.get("category_name") or "",
        "category_color": safe_hex_color(row.get("category_color")),
    }


def build_card_statements(
    user_id: int,
    card: Dict[str, Any],
    until: date,
    cutover: Optional[date],
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Every statement on a card, oldest first: each one it has closed, the one
    open now, and estimates for those after it through the first due on or after
    `until`.

    A statement's balance is what it carried in, plus its charges, less its
    credits and payments — the running balance of the card, not the cycle's
    charges alone. A card paid down during the month overpays its open statement,
    and that surplus belongs to the next one. Where an issuer's printed balance
    has been entered, it replaces the computed one and is what carries forward.

    Charges expected on the card but not imported yet count only after the card's
    last imported row, so nothing is counted both ways. Every statement with
    something left to pay whose due date falls after the checking `cutover` is
    assumed paid in full that day; that assumed payment is what the ledger shows,
    and it is carried into the statement it lands in so the next balance is
    right."""
    if not card_cycle_configured(card):
        return []
    today = today or date.today()
    sync_card_statements(user_id, card, today)
    rows = _card_rows(user_id, card)
    card_cutover = rows[-1]["tx_date"] if rows else None

    periods = load_recorded_statements(user_id, int(card["id"]))
    if periods:
        close, due = _next_cycle(card, periods[-1]["close_date"])
    else:
        anchor = rows[0]["tx_date"] if rows else today
        if cutover is not None:
            anchor = min(anchor, cutover + timedelta(days=1))
        close, due = card_cycle_for_charge(card, min(anchor, today))
    while not periods or periods[-1]["close_date"] < today or periods[-1]["due_date"] < until:
        periods.append({"id": None, "close_date": close, "due_date": due, "issuer_balance": None})
        close, due = _next_cycle(card, close)

    first_start = _close_before(card, periods[0]["close_date"]) + timedelta(days=1)
    expected_from = first_start if card_cutover is None else card_cutover + timedelta(days=1)
    expected = [
        charge
        for charge in _card_expected_charges(
            user_id, int(card["id"]), expected_from, periods[-1]["close_date"]
        )
        if card_cutover is None or charge["date"] > card_cutover
    ]

    statements: List[Dict[str, Any]] = []
    carried = round(-sum(row["amount"] for row in rows if row["tx_date"] < first_start), 2)
    assumed_payments: List[tuple[date, float]] = []
    for index, period in enumerate(periods):
        start = statements[-1]["close_date"] + timedelta(days=1) if statements else first_start
        close, due = period["close_date"], period["due_date"]
        next_close = periods[index + 1]["close_date"] if index + 1 < len(periods) else None

        activity = [_statement_item(row) for row in rows if start <= row["tx_date"] <= close]
        charges = -sum(item["delta"] for item in activity if item["kind"] == "charge")
        credits = sum(item["delta"] for item in activity if item["kind"] == "credit")
        payments = sum(item["delta"] for item in activity if item["kind"] == "payment")
        expected_items = [charge for charge in expected if start <= charge["date"] <= close]
        expected_net = -sum(charge["delta"] for charge in expected_items)
        assumed_in = sum(amount for when, amount in assumed_payments if start <= when <= close)
        computed = round(carried + charges - credits - payments + expected_net - assumed_in, 2)

        issuer = period["issuer_balance"]
        balance = round(float(issuer), 2) if issuer is not None else computed
        # Payments after the close pay this statement off, up to the next close;
        # past that they belong to the next statement, which counts them itself.
        # Taken per row and never below zero: a reversal adds to what is owed and
        # already weighs on the balance, so it must not also cancel a real payment.
        paid_after = [
            _statement_item(row)
            for row in rows
            if row["is_transfer"] and row["tx_date"] > close
            and (next_close is None or row["tx_date"] <= next_close)
        ]
        paid_since_close = round(sum((max(item["delta"], 0.0) for item in paid_after), 0.0), 2)
        remaining = round(max(balance - paid_since_close, 0.0), 2)
        billed = remaining > STATEMENT_CENT and (cutover is None or due > cutover)
        if billed:
            assumed_payments.append((due, remaining))

        closed = close < today
        awaiting_import = card_cutover is None or card_cutover < close
        if not closed:
            status = "open" if start <= today else "estimated"
        elif balance <= STATEMENT_CENT:
            status = "no_balance"
        elif remaining <= STATEMENT_CENT:
            status = "paid"
        elif paid_since_close > STATEMENT_CENT:
            status = "partial"
        elif due < today and card_cutover is not None and card_cutover >= due:
            status = "past_due"
        else:
            status = "unpaid"

        statements.append(
            {
                "id": period["id"],
                "account_id": int(card["id"]),
                "card_name": card["name"],
                "label": f"{card['name']} {'statement' if closed else 'estimated statement'}",
                "period_start": start,
                "close_date": close,
                "due_date": due,
                "closed": closed,
                "status": status,
                "status_label": STATEMENT_STATUS_LABELS[status],
                "awaiting_import": awaiting_import,
                "imported_through": card_cutover,
                "previous_balance": carried,
                "charges": round(charges, 2),
                "credits": round(credits, 2),
                "payments": round(payments, 2),
                "expected_charges": round(expected_net, 2),
                "assumed_payments": round(assumed_in, 2),
                "computed_balance": computed,
                "issuer_balance": round(float(issuer), 2) if issuer is not None else None,
                "difference": round(float(issuer) - computed, 2) if issuer is not None else None,
                "balance": balance,
                "paid_since_close": paid_since_close,
                "remaining": remaining,
                "billed": billed,
                "activity": activity,
                "expected_items": expected_items,
                "paid_after": paid_after,
            }
        )
        carried = balance
    return statements


def load_card_statement(
    user_id: int, card: Dict[str, Any], close_date: date, cutover: Optional[date],
    today: Optional[date] = None,
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """One statement on a card by its close date, with the full list beside it
    so a page can step to its neighbours."""
    today = today or date.today()
    until = max(today, _due_after_close(card, close_date)) if card_cycle_configured(card) else today
    statements = build_card_statements(user_id, card, until, cutover, today=today)
    for statement in statements:
        if statement["close_date"] == close_date:
            return statement, statements
    return None, statements


def update_card_statement(
    user_id: int,
    statement_id: int,
    close_date: date,
    due_date: date,
    issuer_balance: Optional[float],
    today: Optional[date] = None,
) -> None:
    """Correct a recorded statement's dates or enter the balance its issuer
    printed. The close has to stay between its neighbours' — moving it past one
    would hand that statement's rows to this one — and must already be past."""
    today = today or date.today()
    row = fetch_one(
        "SELECT account_id, close_date FROM card_statements WHERE id = ? AND user_id = ?",
        [statement_id, user_id],
    )
    if not row:
        raise ValueError("Statement not found")
    account_id, current = int(row[0]), _as_date(row[1])
    neighbours = [
        statement["close_date"]
        for statement in load_recorded_statements(user_id, account_id)
        if statement["close_date"] != current
    ]
    before = [close for close in neighbours if close < current]
    after = [close for close in neighbours if close > current]
    if before and close_date <= max(before):
        raise ValueError(f"Close date must be after the previous statement's ({max(before)})")
    if after and close_date >= min(after):
        raise ValueError(f"Close date must be before the next statement's ({min(after)})")
    if close_date >= today:
        raise ValueError("A statement can only close on a date that has passed")
    if due_date <= close_date:
        raise ValueError("Due date must be after the close date")
    execute(
        "UPDATE card_statements SET close_date = ?, due_date = ?, issuer_balance = ? "
        "WHERE id = ? AND user_id = ?",
        [close_date, due_date, issuer_balance, statement_id, user_id],
    )


def _statement_row(card: Dict[str, Any], statement: Dict[str, Any], incoming: bool = False) -> Dict[str, Any]:
    """A statement on a ledger, on its due date, for what is left to pay on it:
    going out of the account that pays it, or coming in on the card itself."""
    amount = statement["remaining"]
    return {
        "date": statement["due_date"],
        "description": statement["label"],
        "bill": "",
        "type": "card",
        "income": amount if incoming else 0.0,
        "expense": 0.0 if incoming else amount,
        "delta": amount if incoming else -amount,
        "kind": "income" if incoming else "expense",
        "category_id": None,
        "category_name": "",
        "category_color": "",
        "account_id": int(card["id"]),
        "statement": statement,
        "line": None,
    }


def forecast_card_payments(
    user_id: int,
    window_start: date,
    window_end: date,
    cutover: Optional[date],
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """One row per card statement still to be paid, on its due date: what the
    statement closed at, less what has been paid toward it since.

    Only statements due after the checking `cutover` appear — the checking
    actuals already show anything paid before then. Statements before the window
    are still worked out, because what they leave behind is carried into the
    ones inside it."""
    payments: List[Dict[str, Any]] = []
    for card in load_accounts(user_id):
        for statement in build_card_statements(user_id, card, window_end, cutover, today=today):
            if statement["billed"] and window_start <= statement["due_date"] <= window_end:
                payments.append(_statement_row(card, statement))
    return payments


def statement_rows(
    user_id: int,
    payer: Optional[Dict[str, Any]],
    window_start: date,
    window_end: date,
    cutover: Optional[date],
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Every statement due inside the window and after the paying account's
    `cutover`, for each card `payer` pays, whatever is left to pay on it. One
    paid in full shows for nothing, so it is never counted twice; the account's
    actual payment rows already cover any due on or before the cutover."""
    rows: List[Dict[str, Any]] = []
    for card in load_accounts(user_id):
        if card.get("account_type") != "credit_card" or not _same_account(paying_account(user_id, card), payer):
            continue
        for statement in build_card_statements(user_id, card, window_end, cutover, today=today):
            due = statement["due_date"]
            if window_start <= due <= window_end and (cutover is None or due > cutover):
                rows.append(_statement_row(card, statement))
    return rows


def match_card_payments(user_id: int) -> List[Dict[str, Any]]:
    """Pair a checking transfer debit with a card's own payment row (both flagged
    is_transfer) by matching magnitude + near date. Rows are anchored by
    account_id, so matches survive account renames. Returns the matched pairs."""
    accounts = load_accounts_map(user_id)
    checking_ids = {aid for aid, a in accounts.items() if a.get("account_type") == "checking"}
    card_ids = {aid for aid, a in accounts.items() if a.get("account_type") == "credit_card"}

    rows = query_all("SELECT id, account_id, account, tx_date, amount FROM imported_transactions "
                "WHERE user_id = ? AND is_transfer", [user_id])
    checking_txns: List[Dict[str, Any]] = []
    card_txns: List[Dict[str, Any]] = []
    for row in rows:
        tx_date = row["tx_date"]
        row["tx_date"] = tx_date.date() if isinstance(tx_date, datetime) else tx_date
        if row["account_id"] in card_ids:
            card_txns.append(row)
        elif row["account_id"] in checking_ids:
            checking_txns.append(row)

    pairs: List[Dict[str, Any]] = []
    used: set = set()
    # Match earliest card payments first for stable, deterministic pairing.
    for card_tx in sorted(card_txns, key=lambda r: (r["tx_date"], r["id"])):
        best = None
        best_diff = None
        for check_tx in checking_txns:
            if check_tx["id"] in used:
                continue
            if abs(abs(check_tx["amount"]) - abs(card_tx["amount"])) > 0.005:
                continue
            diff = abs((check_tx["tx_date"] - card_tx["tx_date"]).days)
            if diff > PAYMENT_MATCH_WINDOW_DAYS:
                continue
            if best_diff is None or diff < best_diff:
                best, best_diff = check_tx, diff
        if best is not None:
            used.add(best["id"])
            pairs.append(
                {
                    "card_row_id": int(card_tx["id"]),
                    "card_account_id": int(card_tx["account_id"]),
                    "checking_row_id": int(best["id"]),
                    "amount": round(abs(card_tx["amount"]), 2),
                    "card_date": card_tx["tx_date"],
                    "checking_date": best["tx_date"],
                }
            )
    return pairs


def card_balance_summary(
    user_id: int, card: Dict[str, Any], today: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Where a card's most recently closed statement stands: what it closed at,
    what has been paid toward it since, and what is left."""
    today = today or date.today()
    statements = build_card_statements(
        user_id, card, today, latest_checking_actual_date(user_id), today=today
    )
    closed = [statement for statement in statements if statement["closed"]]
    if not closed:
        return None
    last = closed[-1]
    return {
        "statement_close": last["close_date"],
        "due_date": last["due_date"],
        "statement_owed": last["balance"],
        "payments_applied": last["paid_since_close"],
        "amount_due": last["remaining"],
        "paid": last["status"] == "paid",
        "status": last["status"],
    }


def spending_parts(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """Every piece of spending in the window: one row per transaction remainder
    and one per split part. This is the one definition of spending that Budget
    and the inbox both read.

    Spending is money going out that is not a transfer. Money coming in on a
    card is a refund and reduces spending in its category; money coming in on
    any other account is income and is not here at all. A row's category is its
    effective one (the reconciled expected item's, else its own); a split part
    has its own, and only what is left after splitting keeps the row's.

    Each dict: tx_id, tx_date, account_id, merchant, category_id (None when
    uncategorized), category_name, category_color, spent (> 0 spending,
    < 0 refund)."""
    rows = query_all(f"""
            WITH tx AS (
                SELECT i.id, i.tx_date, i.account_id, i.amount,
                       COALESCE(NULLIF(i.merchant, ''), i.description) AS merchant,
                       COALESCE(ec.id, ic.id) AS cat_id,
                       COALESCE({category_label("ec")}, {category_label("ic")}) AS cat_name,
                       COALESCE(ec.color, ic.color) AS cat_color
                FROM imported_transactions i
                LEFT JOIN accounts acct
                       ON acct.id = i.account_id AND acct.user_id = i.user_id
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN bills b
                       ON r.source_type = 'bill' AND b.id = r.source_id AND b.user_id = i.user_id
                LEFT JOIN one_time_items ot
                       ON r.source_type = 'one_time' AND ot.id = r.source_id AND ot.user_id = i.user_id
                {category_join("ec", "COALESCE(b.category_id, ot.category_id)", "i.user_id")}
                {category_join("ic", "i.category_id", "i.user_id")}
                WHERE i.user_id = ? AND NOT i.is_transfer
                  AND i.tx_date >= ? AND i.tx_date <= ?
                  AND (i.amount < 0 OR COALESCE(acct.account_type = 'credit_card', FALSE))
            ),
            allocated AS (
                SELECT imported_transaction_id AS tx_id, COALESCE(SUM(amount), 0) AS part_total
                FROM transaction_splits WHERE user_id = ? GROUP BY 1
            ),
            parts AS (
                SELECT tx.id AS tx_id, tx.tx_date, tx.account_id, tx.merchant,
                       tx.cat_id AS category_id, tx.cat_name AS category_name,
                       tx.cat_color AS category_color,
                       -SIGN(tx.amount) * (ABS(tx.amount) - COALESCE(a.part_total, 0)) AS spent
                FROM tx LEFT JOIN allocated a ON a.tx_id = tx.id
                UNION ALL
                SELECT tx.id, tx.tx_date, tx.account_id, tx.merchant,
                       sc.id, {category_label("sc")}, sc.color, -SIGN(tx.amount) * ts.amount
                FROM tx
                JOIN transaction_splits ts
                       ON ts.imported_transaction_id = tx.id AND ts.user_id = ?
                {category_join("sc", "ts.category_id", "?", "")}
            )
            SELECT * FROM parts WHERE ABS(spent) > 0.005
            ORDER BY tx_date, tx_id
            """, [user_id, window_start, window_end, user_id, user_id, user_id])
    for row in rows:
        row["tx_date"] = _as_date(row["tx_date"])
        row["spent"] = round(float(row["spent"]), 2)
        row["category_id"] = int(row["category_id"]) if row["category_id"] is not None else None
    return rows


def income_total(user_id: int, window_start: date, window_end: date) -> float:
    """Money arriving in a non-card account that is not a transfer."""
    row = fetch_one("""
            SELECT COALESCE(SUM(i.amount), 0)
            FROM imported_transactions i
            LEFT JOIN accounts acct ON acct.id = i.account_id AND acct.user_id = i.user_id
            WHERE i.user_id = ? AND NOT i.is_transfer AND i.amount > 0
              AND i.tx_date >= ? AND i.tx_date <= ?
              AND NOT COALESCE(acct.account_type = 'credit_card', FALSE)
            """, [user_id, window_start, window_end])
    return round(float(row[0] if row else 0.0), 2)


def _item_actuals(user_id: int, window_start: date, window_end: date) -> Dict[tuple, float]:
    """What the actuals linked to each expected transaction add up to in the
    window, as a positive amount: (source_type, source_id) -> amount. Split
    parts are taken off, since money moved to another category is no longer
    this item's."""
    rows = fetch_all("""
            WITH allocated AS (
                SELECT imported_transaction_id AS tx_id, COALESCE(SUM(amount), 0) AS part_total
                FROM transaction_splits WHERE user_id = ? GROUP BY 1
            )
            SELECT r.source_type, r.source_id,
                   COALESCE(SUM(SIGN(i.amount) * (ABS(i.amount) - COALESCE(a.part_total, 0))), 0) AS total
            FROM imported_transactions i
            JOIN expected_reconciliations r
                   ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
            LEFT JOIN allocated a ON a.tx_id = i.id
            WHERE i.user_id = ? AND NOT i.is_transfer
              AND i.tx_date >= ? AND i.tx_date <= ?
            GROUP BY 1, 2
            """, [user_id, user_id, window_start, window_end])
    return {(source_type, int(source_id)): round(abs(float(total)), 2) for source_type, source_id, total in rows}


def _apply_bar_geometry(categories: List[Dict[str, Any]]) -> None:
    """Size every category bar against one shared scale so the widths are
    comparable at a glance.

    The largest category — by whichever is greater, its budget or its actual —
    fills the track, and every other category is drawn in proportion to it. A
    small category reads as a short bar. Scaling each bar to its own total
    instead made every row look full, which hid the one thing the bars exist to
    show: relative size."""
    scale = max((max(cat["actual"], cat["budgeted"]) for cat in categories), default=0.0)
    for cat in categories:
        if scale <= 0.005:
            cat["fill_pct"] = cat["over_pct"] = cat["neutral_pct"] = 0.0
        elif cat["has_target"]:
            cat["fill_pct"] = round(min(cat["actual"], cat["budgeted"]) / scale * 100, 2)
            cat["over_pct"] = round(cat["over"] / scale * 100, 2)
            cat["neutral_pct"] = 0.0
        else:
            # Nothing to fill toward, so the bar just shows the spend, muted.
            cat["fill_pct"] = 0.0
            cat["over_pct"] = 0.0
            cat["neutral_pct"] = round(cat["actual"] / scale * 100, 2)


def next_due_date(bill: Dict[str, Any], linked: List[Dict[str, Any]],
                  overrides: Optional[List[Dict[str, Any]]] = None, today: Optional[date] = None) -> Optional[date]:
    """The first due date from today on that no linked payment has settled."""
    today = today or date.today()
    start = today - timedelta(days=RECONCILE_OCCURRENCE_TOLERANCE_DAYS)
    for occurrence in bill_occurrence_status(bill, start, today + timedelta(days=800), linked, overrides):
        if occurrence["date"] >= today and not occurrence["paid"]:
            return occurrence["date"]
    return None


def _budget_row(key: str, category: Optional[Dict[str, Any]], name: str) -> Dict[str, Any]:
    return {
        "key": key,
        "id": category["id"] if category else None,
        "name": name,
        "color": safe_hex_color(category["color"] if category else None),
        "parent_id": category["parent_id"] if category else None,
        "own_budgeted": 0.0,
        "own_actual": 0.0,
        "bills": [],
        "one_time": [],
        "children": [],
    }


def _finish_row(row: Dict[str, Any]) -> None:
    row["budgeted"] = round(row["own_budgeted"] + sum(child["own_budgeted"] for child in row["children"]), 2)
    row["actual"] = round(row["own_actual"] + sum(child["own_actual"] for child in row["children"]), 2)
    row["difference"] = round(row["budgeted"] - row["actual"], 2)
    # No target means nothing to exceed — without this guard every unbudgeted
    # category reads as "over" by the whole of its spend.
    row["has_target"] = row["budgeted"] > 0.005
    row["over"] = round(max(0.0, row["actual"] - row["budgeted"]), 2) if row["has_target"] else 0.0


def build_budget_summary(user_id: int, window_start: date, window_end: date,
                         today: Optional[date] = None) -> Dict[str, Any]:
    """Budget for the window (#104). A category's budget is exactly its
    expected transactions: its active bills' due dates and its one-time items
    in the window. Its actual is its spending, by effective category. A parent
    adds up its own and its children's.

    Returns rows (every top-level category, each with its children), income
    (scheduled and one-time income against money arriving outside cards), and
    uncategorized (expected transactions with no category against spending
    with none). Each row lists its bills, whatever their status, and its
    one-time items in the window. categories is every category row, children
    and uncategorized included, flat."""
    today = today or date.today()
    categories = load_categories(user_id)
    by_id = {c["id"]: _budget_row(str(c["id"]), c, c["label"]) for c in categories}
    income = _budget_row("income", None, "income")
    uncategorized = _budget_row("uncategorized", None, "Uncategorized")
    accounts = load_accounts_map(user_id)

    def row_for(kind: str, category_id: Any) -> Dict[str, Any]:
        if kind == "income":
            return income
        return by_id.get(int(category_id)) if category_id and int(category_id) in by_id else uncategorized

    def account_name(account_id: Any) -> str:
        account = accounts.get(int(account_id)) if account_id else None
        return account["name"] if account else ""

    item_actuals = _item_actuals(user_id, window_start, window_end)
    bills = load_all_bills(user_id)
    overrides = load_amount_overrides_for_bills([bill["id"] for bill in bills])
    linked = load_reconciliations_by_item(user_id, "bill")
    for bill in sorted(bills, key=lambda b: (b["name"].lower(), b["id"])):
        bill_overrides = overrides.get(bill["id"], [])
        due = bill_due_dates(bill, window_start, window_end, overrides=bill_overrides) if bill["active"] else []
        budgeted = round(sum(d["amount"] for d in due), 2)
        actual = item_actuals.get(("bill", bill["id"]), 0.0)
        status = bill_status(bill, today)
        target = row_for(bill["kind"], bill.get("category_id"))
        target["own_budgeted"] += budgeted
        target["bills"].append({
            "id": bill["id"], "name": bill["name"], "kind": bill["kind"], "status": status,
            "active": bool(bill["active"]), "cadence": summarize_frequency(bill),
            "account_name": account_name(bill.get("account_id")), "budgeted": budgeted, "actual": actual,
            "difference": round(budgeted - actual, 2),
            "next_due": next_due_date(bill, linked.get(bill["id"], []), bill_overrides, today)
            if status == "active" else None,
        })

    settled = {int(source_id) for (source_id,) in fetch_all(
        "SELECT source_id FROM expected_reconciliations WHERE user_id = ? AND source_type = 'one_time'", [user_id])}
    for item in _load_one_time_items(user_id, "AND o.expected_date >= ? AND o.expected_date <= ?",
                                     [window_start, window_end]):
        actual = item_actuals.get(("one_time", item["id"]), 0.0)
        target = row_for(item["kind"], item.get("category_id"))
        target["own_budgeted"] += item["amount"]
        target["one_time"].append({
            "id": item["id"], "name": item["name"], "kind": item["kind"], "date": item["expected_date"],
            "account_name": account_name(item.get("account_id")), "budgeted": round(item["amount"], 2),
            "actual": actual, "difference": round(item["amount"] - actual, 2), "settled": item["id"] in settled,
        })

    for part in spending_parts(user_id, window_start, window_end):
        row_for("expense", part["category_id"])["own_actual"] += part["spent"]
    income["own_actual"] = income_total(user_id, window_start, window_end)

    tops: List[Dict[str, Any]] = []
    for row in by_id.values():
        parent = by_id.get(row["parent_id"]) if row["parent_id"] else None
        (parent["children"] if parent else tops).append(row)
    every = list(by_id.values()) + [income, uncategorized]
    for row in every:
        if not row["children"]:
            _finish_row(row)
    for row in every:
        if row["children"]:
            _finish_row(row)

    def size(row: Dict[str, Any]) -> tuple:
        return (-max(row["budgeted"], row["actual"]), row["name"].lower())

    tops.sort(key=size)
    flat: List[Dict[str, Any]] = []
    for row in tops:
        row["children"].sort(key=size)
        flat.append(row)
        flat.extend(row["children"])
    flat.append(uncategorized)
    _apply_bar_geometry(flat)

    total_budgeted = round(sum(row["budgeted"] for row in tops) + uncategorized["budgeted"], 2)
    total_actual = round(sum(row["actual"] for row in tops) + uncategorized["actual"], 2)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "rows": tops,
        "income": income,
        "uncategorized": uncategorized,
        "categories": flat,
        "totals": {
            "budgeted": total_budgeted,
            "actual": total_actual,
            "difference": round(total_budgeted - total_actual, 2),
        },
        "bill_count": sum(1 for bill in bills if bill_status(bill, today) == "active"),
        "one_time_count": sum(len(row["one_time"]) for row in every),
    }


def uncategorized_transaction_ids(user_id: int, window_start: date, window_end: date) -> set:
    """The transactions budget lists as uncategorized: spending with no
    category left on it, as spending_parts counts it."""
    return {int(part["tx_id"]) for part in spending_parts(user_id, window_start, window_end)
            if part["category_id"] is None}


def unsettled_transactions(user_id: int, window_start: date, window_end: date) -> Dict[Any, List[Dict[str, Any]]]:
    """What budget lists as not settled yet in the window, newest first, keyed
    by where it is listed:

    - a category id: spending with that category, no link to an expected
      transaction and no unexpected mark (#105). Refunds are not listed;
    - "income": money arriving outside cards that is not a transfer, with no
      link and no mark;
    - "uncategorized": spending with no category (#103), as spending_parts
      counts it.

    Each: id, tx_date, name, description, amount (positive), account_id,
    account_name, kind."""
    accounts = load_accounts_map(user_id)
    found: Dict[Any, List[Dict[str, Any]]] = {"income": [], "uncategorized": []}

    def entry(tx_id: int, tx_date: date, name: str, description: str, amount: float, account_id: Any) -> Dict[str, Any]:
        account = accounts.get(int(account_id)) if account_id else None
        return {"id": int(tx_id), "tx_date": _as_date(tx_date), "name": name, "description": description,
                "amount": round(abs(float(amount)), 2), "signed": round(float(amount), 2),
                "account_id": int(account_id) if account_id else None,
                "account_name": account["name"] if account else "", "kind": "income" if amount > 0 else "expense"}

    for row in query_all("""
            SELECT i.id, i.tx_date, COALESCE(NULLIF(i.merchant, ''), i.description) AS name, i.description,
                   i.amount, i.account_id, i.category_id,
                   COALESCE(acct.account_type = 'credit_card', FALSE) AS on_card
            FROM imported_transactions i
            LEFT JOIN accounts acct ON acct.id = i.account_id AND acct.user_id = i.user_id
            WHERE i.user_id = ? AND NOT i.is_transfer AND NOT i.unexpected
              AND i.tx_date >= ? AND i.tx_date <= ?
              AND i.id NOT IN (SELECT imported_transaction_id FROM expected_reconciliations WHERE user_id = ?)
            ORDER BY i.tx_date DESC, i.id DESC
            """, [user_id, window_start, window_end, user_id]):
        amount = float(row["amount"])
        if amount > 0 and not row["on_card"]:
            found["income"].append(entry(row["id"], row["tx_date"], row["name"], row["description"], amount,
                                         row["account_id"]))
        elif amount < 0 and row["category_id"] is not None:
            found.setdefault(int(row["category_id"]), []).append(
                entry(row["id"], row["tx_date"], row["name"], row["description"], amount, row["account_id"]))

    loose: Dict[int, Dict[str, Any]] = {}
    for part in spending_parts(user_id, window_start, window_end):
        if part["category_id"] is not None:
            continue
        held = loose.setdefault(int(part["tx_id"]), {"part": part, "spent": 0.0})
        held["spent"] += part["spent"]
    descriptions = {int(tx_id): text for tx_id, text in fetch_all(
        "SELECT id, description FROM imported_transactions WHERE user_id = ? AND tx_date >= ? AND tx_date <= ?",
        [user_id, window_start, window_end])} if loose else {}
    for tx_id, held in sorted(loose.items(), key=lambda kv: (kv[1]["part"]["tx_date"], kv[0]), reverse=True):
        part = held["part"]
        found["uncategorized"].append(entry(tx_id, part["tx_date"], part["merchant"], descriptions.get(tx_id, ""),
                                            -held["spent"], part["account_id"]))
    return found


LIST_PAGE_SIZE = 10


def page_of(rows: List[Any], page: int, size: int = LIST_PAGE_SIZE) -> Dict[str, Any]:
    """One page of a list, clamped to the pages there are. Pages count from 1."""
    pages = max(1, -(-len(rows) // size))
    page = min(max(1, int(page or 1)), pages)
    return {"rows": rows[(page - 1) * size: page * size], "page": page, "pages": pages, "total": len(rows)}


def _ledger_sort_key(tx: Dict[str, Any]):
    return (tx["date"], 0 if tx["kind"] == "income" else 1, tx["description"].lower(), tx.get("id", 0))


def _expected_rows(
    user_id: int, window_start: date, window_end: date, paid_through: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Expected (bill and one-time) rows for a window, every account's.
    `paid_through` is the actuals cutover: due dates already settled by an
    actual on/before it are dropped, so a bill paid ahead of its due date is not
    counted twice."""
    raw: List[Dict[str, Any]] = []
    active_bills = load_active_bills(user_id)
    overrides_by_id = load_amount_overrides_for_bills([bill["id"] for bill in active_bills])
    settled_dates = (
        reconciled_occurrence_dates(user_id, active_bills, paid_through, window_end)
        if paid_through is not None
        else {}
    )
    for bill in active_bills:
        rows = generate_bill_transactions(bill, window_start, window_end, overrides=overrides_by_id.get(bill["id"], []))
        settled = settled_dates.get(bill["id"])
        if settled:
            rows = [row for row in rows if row["date"] not in settled]
        raw.extend(rows)

    one_time_rows = load_one_time_transactions(user_id, window_start, window_end)
    if paid_through is not None:
        settled_one_time = settled_one_time_ids(user_id, paid_through)
        if settled_one_time:
            one_time_rows = [row for row in one_time_rows if int(row["id"]) not in settled_one_time]
    raw.extend(one_time_rows)
    return [row for row in raw if window_start <= row["date"] <= window_end]


def collect_window_transactions(
    user_id: int,
    window_start: date,
    window_end: date,
    paid_through: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Expected rows for a window on every account but cards, whose charges
    are counted through their statements instead."""
    transactions = _apply_cash_flow_deferral(_expected_rows(user_id, window_start, window_end, paid_through),
                                             load_accounts_map(user_id), window_start, window_end)
    return sorted(transactions, key=_ledger_sort_key)


def _checking_forecast(
    user_id: int, account: Optional[Dict[str, Any]], window_start: date, window_end: date,
    cutover: Optional[date], today: Optional[date],
) -> List[Dict[str, Any]]:
    """A checking account's forecast: its bills, income and one-time items
    (and those with no account, on the default one), then a row for each
    statement of every card it pays.

    The cutover date itself belongs to both halves. An import taken that day is
    only as complete as the bank was at the time, so a bill due that day may
    simply not have posted yet. Expected occurrences dated on the cutover that
    no actual has settled are kept and flagged `due_not_posted`. Earlier dates
    get no such benefit of the doubt: the same import covered them in full."""
    default = is_default_checking(user_id, account)
    own_id = int(account["id"]) if account else None

    def belongs(row: Dict[str, Any]) -> bool:
        return row.get("account_id") == own_id if row.get("account_id") else default

    forecast_start = max(window_start, cutover) if cutover else window_start
    # Card statements keep the old boundary — a statement settles a whole cycle
    # at once and is skipped when due on or before the cutover.
    card_start = max(window_start, cutover + timedelta(days=1)) if cutover else window_start
    rows: List[Dict[str, Any]] = []
    if forecast_start <= window_end:
        for row in _expected_rows(user_id, forecast_start, window_end, paid_through=cutover):
            if belongs(row):
                if cutover is not None and row["date"] == cutover:
                    row["due_not_posted"] = True
                rows.append(row)
    if card_start <= window_end:
        rows.extend(statement_rows(user_id, account, card_start, window_end, cutover, today=today))
    return rows


def _card_forecast(
    user_id: int, card: Dict[str, Any], window_start: date, window_end: date,
    cutover: Optional[date], today: Optional[date],
) -> List[Dict[str, Any]]:
    """A card's forecast after its last import: its expected charges, and on
    each due date the statement payment coming in from the account that pays
    it. Both are what build_card_statements counts, so the card's ledger and its
    statements agree."""
    start = max(window_start, cutover + timedelta(days=1)) if cutover else window_start
    if start > window_end:
        return []
    rows = [row for row in _expected_rows(user_id, start, window_end) if row.get("account_id") == int(card["id"])]
    payer = paying_account(user_id, card)
    for statement in build_card_statements(user_id, card, window_end, account_cutover(user_id, payer), today=today):
        if statement["billed"] and start <= statement["due_date"] <= window_end:
            rows.append(_statement_row(card, statement, incoming=True))
    return rows


def collect_account_transactions(
    user_id: int,
    account: Optional[Dict[str, Any]],
    window_start: date,
    window_end: date,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """One account's ledger for a window: its imported rows up to its cutover
    (the latest date imported for it), then its forecast — one coherent,
    non-double-counted timeline. `account` None stands for a user with no
    checking account yet."""
    cutover = account_cutover(user_id, account)
    transactions: List[Dict[str, Any]] = []
    if cutover is not None and window_start <= min(window_end, cutover):
        transactions.extend(load_actual_transactions(user_id, account, window_start, min(window_end, cutover)))
    if account is not None and account.get("account_type") == "credit_card":
        transactions.extend(_card_forecast(user_id, account, window_start, window_end, cutover, today))
    else:
        transactions.extend(_checking_forecast(user_id, account, window_start, window_end, cutover, today))
    return sorted(transactions, key=_ledger_sort_key)


def collect_blended_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """The default checking account's ledger: what the dashboard and the
    first-negative warning read."""
    return collect_account_transactions(user_id, default_checking_account(user_id), window_start, window_end)


def first_projected_negative(
    user_id: int, window_start: date, window_end: date, today: Optional[date] = None
) -> Optional[date]:
    """The first day from today on, inside the window, when the checking
    balance (actuals, then forecast) drops below zero; None if it never does."""
    today = today or date.today()
    if window_end < today:
        return None
    opening_balance = get_setting_float(user_id, "starting_balance", 0.0)
    start_balance = checking_actual_balance_before(user_id, window_start, opening_balance)
    rows, _first, _end = build_ledger_rows(collect_blended_transactions(user_id, window_start, window_end), start_balance, window_start)
    balance = start_balance
    if balance < 0 and window_start >= today:
        return window_start
    for row in rows:
        balance = row["running_balance"]
        if row["date"] >= today and balance < 0:
            return row["date"]
    if balance < 0 and not rows and window_start <= today:
        return today
    return None


def build_ledger_rows(
    transactions: List[Dict[str, Any]],
    starting_balance: float,
    window_start: date,
) -> tuple[List[Dict[str, Any]], Optional[date], float]:
    running_balance = starting_balance
    first_negative_date: Optional[date] = window_start if running_balance < 0 else None
    ledger_rows: List[Dict[str, Any]] = []

    for tx in transactions:
        running_balance += tx["delta"]
        ledger_rows.append(
            {
                **tx,
                "running_balance": running_balance,
            }
        )
        if first_negative_date is None and running_balance < 0:
            first_negative_date = tx["date"]

    return ledger_rows, first_negative_date, running_balance


def add_month_summary_rows(
    ledger_rows: List[Dict[str, Any]],
    window_start: date,
    window_end: date,
    starting_balance: float,
) -> List[Dict[str, Any]]:
    display_rows: List[Dict[str, Any]] = []
    row_index = 0
    month_cursor = date(window_start.year, window_start.month, 1)
    end_month = date(window_end.year, window_end.month, 1)
    month_ending_balance = starting_balance

    while month_cursor <= end_month:
        month_income = 0.0
        month_expense = 0.0
        tx_count = 0
        month_rows: List[Dict[str, Any]] = []

        while row_index < len(ledger_rows):
            row_date = ledger_rows[row_index]["date"]
            if isinstance(row_date, datetime):
                row_date = row_date.date()
            row_month = date(row_date.year, row_date.month, 1)
            if row_month != month_cursor:
                break

            row = {
                **ledger_rows[row_index],
                "row_type": "transaction",
            }
            month_rows.append(row)
            month_income += float(row["income"])
            month_expense += float(row["expense"])
            tx_count += 1
            month_ending_balance = float(row["running_balance"])
            row_index += 1

        display_rows.extend(month_rows)

        month_label = month_cursor.strftime("%b %Y")
        month_net = month_income - month_expense
        display_rows.append(
            {
                "row_type": "month_summary",
                "date": day_in_month(month_cursor.year, month_cursor.month, 31),
                "month_label": month_label,
                "tx_count": tx_count,
                "income": month_income,
                "expense": month_expense,
                "month_net": month_net,
                "running_balance": month_ending_balance,
            }
        )

        month_cursor = add_months(month_cursor, 1)

    return display_rows


def build_daily_balance_series(
    window_start: date,
    window_end: date,
    starting_balance: float,
    transactions: List[Dict[str, Any]],
) -> tuple[List[str], List[float]]:
    daily_deltas: Dict[date, float] = {}
    for tx in transactions:
        tx_date = tx["date"]
        if isinstance(tx_date, datetime):
            tx_date = tx_date.date()
        daily_deltas[tx_date] = daily_deltas.get(tx_date, 0.0) + float(tx["delta"])

    labels: List[str] = []
    values: List[float] = []
    running = float(starting_balance)
    cursor = window_start
    while cursor <= window_end:
        running += daily_deltas.get(cursor, 0.0)
        labels.append(cursor.isoformat())
        values.append(round(running, 2))
        cursor += timedelta(days=1)

    return labels, values


def build_monthly_totals(
    window_start: date,
    window_end: date,
    starting_balance: float,
    transactions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    monthly_income: Dict[date, float] = {}
    monthly_expense: Dict[date, float] = {}

    for tx in transactions:
        tx_date = tx["date"]
        if isinstance(tx_date, datetime):
            tx_date = tx_date.date()
        month_key = date(tx_date.year, tx_date.month, 1)
        monthly_income[month_key] = monthly_income.get(month_key, 0.0) + float(tx["income"])
        monthly_expense[month_key] = monthly_expense.get(month_key, 0.0) + float(tx["expense"])

    rows: List[Dict[str, Any]] = []
    running = starting_balance
    month_cursor = date(window_start.year, window_start.month, 1)
    end_month = date(window_end.year, window_end.month, 1)

    while month_cursor <= end_month:
        month_income = monthly_income.get(month_cursor, 0.0)
        month_expense = monthly_expense.get(month_cursor, 0.0)
        month_net = month_income - month_expense
        ending_balance = running + month_net
        rows.append(
            {
                "month_label": month_cursor.strftime("%b %Y"),
                "income": month_income,
                "expense": month_expense,
                "net": month_net,
                "ending_balance": ending_balance,
            }
        )
        running = ending_balance
        month_cursor = add_months(month_cursor, 1)

    return rows


# ---------------------------------------------------------------------------
# CSV transaction import (real bank/card exports -> imported_transactions)
#
# Ports the sign / flow / transfer logic from the (staged, un-integrated)
# _incoming/financial_analysis/app/loaders.py, re-implemented on the stdlib
# `csv` module so metis keeps no pandas/PyYAML dependency. Amounts are stored
# SIGNED (negative = money out, positive = money in). Credit-card *payments*
# are flagged is_transfer=True so they net out of spending totals.
# ---------------------------------------------------------------------------

_IMPORT_SQUASH_RE = re.compile(r"\s+")
_CARD_PAYMENT_RE = re.compile(r"PAYMENT|THANK YOU", re.IGNORECASE)

# How a payment to a card looks on the funding account's statement varies by
# issuer, so it is configured per card account (accounts.payment_pattern) rather
# than hardcoded. These generic hints are the fallback for a card that has no
# pattern set yet — they describe what a card payment is called in general, and
# deliberately name no institution.
GENERIC_CARD_PAYMENT_HINTS = ("CARD PAYMENT", "CREDIT CARD", "CARDMEMBER", "PAYMENT TO CARD")


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    """Case-insensitive 'contains any of'. Substring rather than regex so a
    pattern typed into the accounts form can't blow up on stray metacharacters."""
    haystack = (text or "").upper()
    return any(pattern and pattern in haystack for pattern in patterns)


def card_payment_patterns(user_id: int) -> List[str]:
    """Uppercased patterns that mark a debit on a funding account as a payment to
    one of this user's credit cards.

    The generic hints are added whenever at least one card still has no pattern,
    so configuring one card never silently stops detecting payments to the others.
    Once every card is configured the hints drop away, and matching is exactly
    what the user specified."""
    rows = fetch_all(
        "SELECT payment_pattern FROM accounts "
        "WHERE user_id = ? AND account_type = 'credit_card'",
        [user_id],
    )
    configured = [str(row[0]).strip().upper() for row in rows if row[0] and str(row[0]).strip()]
    any_unconfigured = any(not row[0] or not str(row[0]).strip() for row in rows)
    if not rows or any_unconfigured:
        return configured + list(GENERIC_CARD_PAYMENT_HINTS)
    return configured


def suggest_payment_pattern(account_name: str) -> str:
    """A starting guess for a card's payment pattern: the most distinctive word in
    its name. 'Acme Rewards Card' -> 'ACME'. Meant to be edited, not trusted."""
    skip = {"CARD", "CREDIT", "THE", "MY", "BANK", "VISA", "REWARDS", "ACCOUNT"}
    words = [word for word in re.split(r"[^A-Za-z0-9]+", account_name or "") if word]
    for word in words:
        if len(word) > 2 and word.upper() not in skip:
            return word.upper()
    return words[0].upper() if words else ""


class CsvImportError(ValueError):
    """Raised when a CSV cannot be recognized or parsed. Subclasses ValueError
    so routes can surface the message with the same handling as other input
    errors."""


def _import_squash(text: Any) -> str:
    """Collapse runs of whitespace so descriptions are stable/searchable."""
    return _IMPORT_SQUASH_RE.sub(" ", str(text if text is not None else "")).strip()


def compute_import_fingerprint(row: Dict[str, Any], occurrence_index: int) -> str:
    """Stable dedup key for one transaction. The occurrence_index disambiguates
    genuinely identical same-day rows while keeping re-uploads reproducible."""
    key = "|".join(
        [
            row["account"],
            row["tx_date"].isoformat(),
            f"{float(row['amount']):.2f}",
            _import_squash(row["description"]).upper(),
            str(occurrence_index),
        ]
    )
    # SHA-1 only names a row for de-duplication. Nothing secret is hashed, and
    # switching algorithms would re-key every stored row, so a re-import would
    # add them all again.
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _import_content_key(tx_date: Any, amount: Any, description: Any) -> tuple:
    """What makes a transaction itself, whatever its export called the account:
    the day, the signed amount, and the description."""
    return (
        _as_date(tx_date).isoformat(),
        round(float(amount), 2),
        _import_squash(description).upper(),
    )


def _account_content_counts(user_id: int, first: date, last: date) -> Dict[int, Dict[tuple, int]]:
    """How many times each transaction dated within [first, last] is already
    held by each account: account id -> content key -> count."""
    rows = fetch_all(
        "SELECT account_id, tx_date, amount, description FROM imported_transactions "
        "WHERE user_id = ? AND account_id IS NOT NULL AND tx_date >= ? AND tx_date <= ?",
        [user_id, first, last],
    )
    counts: Dict[int, Dict[tuple, int]] = {}
    for account_id, tx_date, amount, description in rows:
        held = counts.setdefault(int(account_id), {})
        key = _import_content_key(tx_date, amount, description)
        held[key] = held.get(key, 0) + 1
    return counts


def _assign_import_fingerprints(rows: List[Dict[str, Any]]) -> None:
    seen: Dict[tuple, int] = {}
    for row in rows:
        base_key = (
            row["account"],
            *_import_content_key(row["tx_date"], row["amount"], row["description"]),
        )
        occurrence = seen.get(base_key, 0)
        seen[base_key] = occurrence + 1
        row["fingerprint"] = compute_import_fingerprint(row, occurrence)
        # A card payment used to be stored negative, and a row imported then still
        # carries that key; checking it too keeps a re-import from adding it twice.
        if row.get("is_transfer") and float(row["amount"]) > 0:
            row["legacy_fingerprint"] = compute_import_fingerprint(
                {**row, "amount": -float(row["amount"])}, occurrence
            )


def upsert_imported_transactions(
    user_id: int, rows: List[Dict[str, Any]], source_file: str
) -> Dict[str, int]:
    """Insert only the rows that are not already imported for this user.
    Idempotent: re-uploading overlapping exports adds nothing new.

    A row is already imported when its fingerprint is stored, or when its account
    already holds that transaction. The fingerprint hashes the export's label, so
    on its own it would re-add an account's whole history the day that label is
    worded differently; the account is what the transaction actually belongs to."""
    if not rows:
        return {"inserted": 0, "skipped": 0, "transfers": 0}

    _assign_import_fingerprints(rows)
    fingerprints = [row["fingerprint"] for row in rows]
    fingerprints += [row["legacy_fingerprint"] for row in rows if row.get("legacy_fingerprint")]

    id_to_name = {int(account["id"]): str(account["name"]) for account in load_accounts(user_id)}
    dates = [_as_date(row["tx_date"]) for row in rows]
    held = _account_content_counts(user_id, min(dates), max(dates))
    targets: List[Optional[int]] = [row.get("account_id") for row in rows]

    inserted = 0
    transfers = 0
    with get_connection() as conn:
        placeholders = ", ".join("?" for _ in fingerprints)
        existing = conn.execute(
            f"SELECT fingerprint FROM imported_transactions "
            f"WHERE user_id = ? AND fingerprint IN ({placeholders})",
            [user_id, *fingerprints],
        ).fetchall()
        already_present = {found[0] for found in existing}

        seen_in_batch: set = set()
        seen_on_account: Dict[tuple, int] = {}
        for row, account_id in zip(rows, targets):
            # Counted rather than merely looked up: two identical purchases on one
            # day are two charges, so the nth copy in the file is already held only
            # if the account holds at least n of them.
            already_held = False
            if account_id is not None:
                content = _import_content_key(row["tx_date"], row["amount"], row["description"])
                nth = seen_on_account.get((account_id, content), 0)
                seen_on_account[(account_id, content)] = nth + 1
                already_held = nth < held.get(account_id, {}).get(content, 0)
            fingerprint = row["fingerprint"]
            if already_held or fingerprint in already_present or fingerprint in seen_in_batch:
                continue
            if row.get("legacy_fingerprint") in already_present:
                continue
            seen_in_batch.add(fingerprint)
            # Store the account's own name rather than the export's label, so the
            # ledger reads in the user's terms and a later rename stays coherent.
            account_label = id_to_name.get(account_id, row["account"])
            conn.execute(
                """
                INSERT INTO imported_transactions (
                    user_id, account, account_id, tx_date, description, merchant, amount,
                    flow, is_transfer, raw_type, category_id, source_file, fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                [
                    user_id,
                    account_label,
                    account_id,
                    row["tx_date"],
                    row["description"],
                    row["merchant"],
                    float(row["amount"]),
                    row["flow"],
                    bool(row["is_transfer"]),
                    row["raw_type"],
                    source_file,
                    fingerprint,
                ],
            )
            inserted += 1
            if row["is_transfer"]:
                transfers += 1

    return {"inserted": inserted, "skipped": len(rows) - inserted, "transfers": transfers}


def load_unmapped_import_labels(user_id: int) -> List[Dict[str, Any]]:
    """Labels of imported rows that belong to no account, left over from when
    Metis placed rows by the file's wording. Each entry: {label, count}."""
    rows = fetch_all("""
            SELECT account, COUNT(*) AS n
            FROM imported_transactions
            WHERE user_id = ? AND account_id IS NULL
            GROUP BY account
            ORDER BY account
            """, [user_id])
    return [{"label": row[0], "count": int(row[1])} for row in rows]


def map_import_label_to_account(user_id: int, label: str, account_id: int) -> int:
    """Relink every unanchored imported row carrying `label` to `account_id`, and
    align its display label to that account's current name. Returns rows updated."""
    with get_connection() as conn:
        account = conn.execute(
            "SELECT name FROM accounts WHERE user_id = ? AND id = ?",
            [user_id, account_id],
        ).fetchone()
        if not account:
            raise ValueError("Account not found")
        matched = conn.execute(
            "SELECT COUNT(*) FROM imported_transactions "
            "WHERE user_id = ? AND account_id IS NULL AND account = ?",
            [user_id, label],
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE imported_transactions
            SET account_id = ?, account = ?
            WHERE user_id = ? AND account_id IS NULL AND account = ?
            """,
            [account_id, account[0], user_id, label],
        )
    return int(matched)


# ---------------------------------------------------------------------------
# Expected transactions + reconciliation
#
# "Expected transactions" = bills (scheduled money out and in) + one_time_items
# (a single expected transaction). Reconciliation links an imported (actual)
# transaction to an expected item. A confirmed link seeds an editable
# description substring rule (expected_match_rules) that auto-links matching
# actuals, both historical and on future imports. Beyond status/audit, this
# layer feeds the ledger one fact: which forecast due dates are already settled
# by an actual (see reconciled_occurrence_dates / settled_one_time_ids) so they
# are not counted twice.
# ---------------------------------------------------------------------------

EXPECTED_SOURCE_TYPES = {"bill", "one_time"}
RECONCILE_OCCURRENCE_TOLERANCE_DAYS = 10
RECONCILE_OCCURRENCE_LOOKBACK_DAYS = 400


def occurrence_history_start(bill: Dict[str, Any]) -> date:
    """Where to begin generating a bill's due dates when matching them against
    linked actuals. Shared by the reconcile view and the ledger so both walk
    the same due dates and can never disagree about what is paid."""
    first = _as_date(bill["first_due_date"])
    return max(first, date.today() - timedelta(days=RECONCILE_OCCURRENCE_LOOKBACK_DAYS))


def bill_status(bill: Dict[str, Any], today: Optional[date] = None) -> str:
    """"completed" once the end date has passed, whether or not the bill was
    paused, since it will not fall due again; otherwise "paused" or "active"."""
    today = today or date.today()
    end_date = bill.get("end_date")
    if end_date is not None and _as_date(end_date) < today:
        return "completed"
    return "active" if bill.get("active") else "paused"


def load_expected_items(user_id: int) -> List[Dict[str, Any]]:
    """Every expected transaction: bills and income, then one-time items."""
    accounts = load_accounts_map(user_id)
    today = date.today()

    def account_name(account_id: Any) -> str:
        account = accounts.get(int(account_id)) if account_id else None
        return account["name"] if account else ""

    items: List[Dict[str, Any]] = []
    for bill in load_all_bills(user_id):
        items.append(
            {
                "source_type": "bill",
                "id": bill["id"],
                "name": bill["name"],
                "kind": bill["kind"],
                "amount": float(bill["amount"]),
                "category_name": bill.get("category_name") or "",
                "category_color": safe_hex_color(bill.get("category_color")),
                "account_name": account_name(bill.get("account_id")),
                "active": bool(bill["active"]),
                "status": bill_status(bill, today),
                "cadence": summarize_frequency(bill),
                "date": bill["first_due_date"],
            }
        )
    for item in load_all_one_time_items(user_id):
        items.append(
            {
                "source_type": "one_time",
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "amount": item["amount"],
                "category_name": item["category_name"],
                "category_color": item["category_color"],
                "account_name": account_name(item.get("account_id")),
                "active": True,
                "cadence": "One-time",
                "date": item["expected_date"],
            }
        )
    return items


def load_expected_item(user_id: int, source_type: str, source_id: int) -> Optional[Dict[str, Any]]:
    if source_type == "bill":
        bill = load_bill_by_id(user_id, source_id)
        if not bill:
            return None
        return {
            "source_type": "bill",
            "id": bill["id"],
            "name": bill["name"],
            "kind": bill["kind"],
            "amount": float(bill["amount"]),
            "category_name": bill.get("category_name") or "",
            "category_color": safe_hex_color(bill.get("category_color")),
            "active": bool(bill["active"]),
            "cadence": summarize_frequency(bill),
            "raw": bill,
        }
    if source_type == "one_time":
        item = load_one_time_item_by_id(user_id, source_id)
        if not item:
            return None
        return {
            "source_type": "one_time",
            "id": item["id"],
            "name": item["name"],
            "kind": item["kind"],
            "amount": item["amount"],
            "category_name": item["category_name"],
            "category_color": item["category_color"],
            "active": True,
            "cadence": "One-time",
            "date": item["expected_date"],
            "raw": item,
        }
    return None


# ---------------------------------------------------------------------------
# Bill suggestions
#
# Scan a user's imported (actual) transactions, group them by a normalized
# merchant key, and infer which groups look like a bill or scheduled income —
# regular cadence + stable amount. Each surviving group becomes a suggestion
# the user can confirm (add a bill on the detected account) or dismiss. Dismissed/added keys are remembered so they don't reappear.
# ---------------------------------------------------------------------------

# Map a median day-gap between occurrences to a human cadence and the Metis
# frequency parameters that reproduce it. (lo, hi) is the inclusive gap window.
_SUGGEST_CADENCES = [
    # name, (lo, hi), frequency_type, interval_months
    ("Weekly", (5, 10), "weekly", None),
    ("Every 2 weeks", (11, 18), "biweekly", None),
    ("Monthly", (19, 45), "monthly", None),
    ("Every 2 months", (46, 75), "every_x_months", 2),
    ("Quarterly", (76, 135), "every_x_months", 3),
    ("Every 6 months", (136, 285), "every_x_months", 6),
    ("Yearly", (286, 430), "yearly", 12),
]


def _suggest_clean_name(text: Any) -> str:
    """Turn a raw merchant string into a friendlier default name."""
    value = _import_squash(text)
    value = re.sub(r"\s{2,}.*$", "", value)             # drop trailing address block
    value = re.sub(r"\bWWW\.[^\s]+", "", value, flags=re.IGNORECASE)  # drop URLs
    value = value.replace("*", " ").replace("_", " ")   # SQ* / TST* / FOO_BAR -> spaces
    value = re.sub(r"\b\d{3,}\b", "", value)             # drop long id numbers
    value = re.sub(r"\s+", " ", value).strip(" -.*_")
    if not value:
        value = _import_squash(text)[:40]
    # Title-case ALL-CAPS bank/ACH text; leave mixed-case merchant names alone.
    if value.isupper():
        value = value.title()
    return value[:40] or "Bill"


def _classify_suggest_cadence(median_gap: float):
    for name, (lo, hi), freq, interval in _SUGGEST_CADENCES:
        if lo <= median_gap <= hi:
            return name, freq, interval
    return None, None, None


def load_dismissed_suggestion_keys(user_id: int) -> set:
    rows = fetch_all("SELECT suggestion_key FROM dismissed_suggestions WHERE user_id = ?", [user_id])
    return {row[0] for row in rows}


def dismiss_suggestion(user_id: int, suggestion_key: str, reason: str = "dismissed") -> None:
    key = (suggestion_key or "").strip()
    if not key:
        return
    execute("""
            INSERT INTO dismissed_suggestions (user_id, suggestion_key, reason)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """, [user_id, key, reason])


def count_dismissed_suggestions(user_id: int) -> int:
    """How many suggestions the user dismissed by hand (restorable). Excludes
    'added' rows, which suppress already-confirmed items and should stay hidden."""
    row = fetch_one("SELECT COUNT(*) FROM dismissed_suggestions WHERE user_id = ? AND reason = 'dismissed'", [user_id])
    return int(row[0]) if row else 0


def reset_dismissed_suggestions(user_id: int) -> None:
    """Restore suggestions dismissed by hand. Leaves 'added' suppressions intact
    so a suggestion already turned into a bill is not made again."""
    execute("DELETE FROM dismissed_suggestions WHERE user_id = ? AND reason = 'dismissed'", [user_id])


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _pstdev(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


def _mode(values: List[Any]) -> Any:
    counts: Dict[Any, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else None


def detect_bill_suggestions(
    user_id: int,
    min_occurrences: int = 3,
    amount_cv_max: float = 0.35,
    min_confidence: float = 0.5,
    lookback_days: int = 1200,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """Infer likely bills and scheduled income from a user's imported transactions.

    A group of same-merchant transactions qualifies when it recurs at least
    `min_occurrences` times at a regular spacing (mapping to a Metis cadence)
    and its amount is reasonably stable. Transfers, dismissed/added keys, and
    groups already reconciled to a bill are excluded."""
    since = date.today() - timedelta(days=lookback_days)
    accounts = load_accounts(user_id)
    account_id_by_name = {a["name"]: int(a["id"]) for a in accounts}
    categories = {int(c["id"]): c for c in load_categories(user_id)}
    dismissed = load_dismissed_suggestion_keys(user_id)

    rows = query_all("""
                SELECT i.id, i.account, i.tx_date, i.description, i.merchant,
                       i.amount, i.category_id,
                       CASE WHEN r.source_type = 'bill' THEN 1 ELSE 0 END AS on_a_bill
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                WHERE i.user_id = ?
                  AND NOT i.is_transfer
                  AND i.tx_date >= ?
                ORDER BY i.tx_date
                """, [user_id, since])

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        tx_date = row["tx_date"]
        if isinstance(tx_date, datetime):
            tx_date = tx_date.date()
        row["tx_date"] = tx_date
        text = row.get("merchant") or row.get("description")
        key = merchant_key(text)
        if not key or key in dismissed or legacy_merchant_key(text) in dismissed:
            continue
        groups.setdefault(key, []).append(row)

    suggestions: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < min_occurrences:
            continue
        # Skip groups already tracked by a bill (majority reconciled).
        reconciled = sum(1 for r in group if r["on_a_bill"])
        if reconciled * 2 >= len(group):
            continue

        group.sort(key=lambda r: r["tx_date"])
        dates = [r["tx_date"] for r in group]
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < min_occurrences - 1:
            continue

        median_gap = _median([float(g) for g in gaps])
        cadence_label, frequency_type, interval_months = _classify_suggest_cadence(median_gap)
        if frequency_type is None:
            continue

        signed = [float(r["amount"]) for r in group]
        amounts = [abs(v) for v in signed]
        mean_abs = sum(amounts) / len(amounts)
        amount_cv = (_pstdev(amounts) / mean_abs) if mean_abs else 1.0
        if amount_cv > amount_cv_max:
            continue

        gap_cv = (_pstdev([float(g) for g in gaps]) / median_gap) if median_gap else 1.0
        confidence = round(max(0.0, min(1.0, 1.0 - 0.5 * amount_cv - 0.5 * gap_cv)), 2)
        if confidence < min_confidence:
            continue

        kind = "income" if _median(signed) > 0 else "expense"
        account_name = _mode([r["account"] for r in group])
        modal_category = _mode([r["category_id"] for r in group if r.get("category_id")])
        category = categories.get(int(modal_category)) if modal_category else None
        # The first due date sets the day a monthly bill falls on, so it is the
        # latest charge that fell on the usual day, not simply the latest charge.
        usual_day = _mode([when.day for when in dates])
        first_due = max(when for when in dates if when.day == usual_day)
        interval = int(interval_months or 1)

        suggestions.append(
            {
                "key": key,
                "token": suggestion_token(user_id, key),
                "name": _suggest_clean_name(_mode([r["merchant"] for r in group]) or key),
                "kind": kind,
                "amount": round(_median(amounts), 2),
                "account_id": account_id_by_name.get(account_name),
                "account_name": account_name,
                "category_id": int(category["id"]) if category else None,
                "category_name": category["name"] if category else "",
                "category_color": category["color"] if category else "",
                "frequency_type": frequency_type,
                "frequency_label": cadence_label,
                "interval_months": interval,
                "first_due_date": first_due.isoformat(),
                "count": len(group),
                "confidence": confidence,
                "first_date": dates[0].isoformat(),
                "last_date": dates[-1].isoformat(),
            }
        )

    suggestions.sort(key=lambda s: (s["confidence"], s["amount"]), reverse=True)
    return suggestions[:limit]


def suggestion_token(user_id: int, key: str) -> str:
    """A short name for a suggestion that is safe to put in a URL: a hash, so
    the merchant it is about is not in the address."""
    return hashlib.sha256(f"{user_id}:{key}".encode("utf-8")).hexdigest()[:16]


def find_bill_suggestion(user_id: int, token: str) -> Optional[Dict[str, Any]]:
    """The current suggestion with this token, or None."""
    if not token:
        return None
    return next((s for s in detect_bill_suggestions(user_id) if s["token"] == token), None)


def find_amount_suggestions(user_id: int, kind: str, amount: float, limit: int = 25) -> List[Dict[str, Any]]:
    """Unreconciled imported transactions whose amount ~matches an expected item
    (correct sign, not a transfer), closest amount first."""
    target = abs(float(amount))
    tolerance = round(max(target * 0.02, 0.0), 2)
    sign_condition = "i.amount < 0" if kind == "expense" else "i.amount > 0"
    rows = query_all(f"""
                SELECT i.id, i.account, i.tx_date, i.description, i.merchant, i.amount, i.flow
                FROM imported_transactions i
                WHERE i.user_id = ?
                  AND NOT i.is_transfer
                  AND {sign_condition}
                  AND ABS(ABS(i.amount) - ?) <= ?
                  AND i.id NOT IN (
                      SELECT imported_transaction_id FROM expected_reconciliations WHERE user_id = ?
                  )
                ORDER BY ABS(ABS(i.amount) - ?) ASC, i.tx_date DESC
                LIMIT ?
                """, [user_id, target, tolerance, user_id, target, limit])
    for row in rows:
        if isinstance(row["tx_date"], datetime):
            row["tx_date"] = row["tx_date"].date()
        row["is_exact"] = abs(abs(float(row["amount"])) - target) < 0.005
    return rows


def confirm_reconciliation(
    user_id: int, source_type: str, source_id: int, imported_transaction_id: int, matched_via: str = "confirm"
) -> bool:
    """Link an actual to an expected item. Idempotent: an already-linked actual
    is left untouched (returns False). Matching by hand clears an unexpected
    mark."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM expected_reconciliations WHERE user_id = ? AND imported_transaction_id = ?",
            [user_id, imported_transaction_id],
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """
            INSERT INTO expected_reconciliations (user_id, imported_transaction_id, source_type, source_id, matched_via)
            VALUES (?, ?, ?, ?, ?)
            """,
            [user_id, imported_transaction_id, source_type, source_id, matched_via],
        )
        if matched_via == "confirm":
            conn.execute("UPDATE imported_transactions SET unexpected = FALSE WHERE id = ? AND user_id = ?",
                         [imported_transaction_id, user_id])
    return True


def suggest_rule_pattern(description: str) -> str:
    """Seed an editable description-substring rule from a confirmed actual."""
    text = _import_squash(description).upper()
    if not text:
        return ""
    # The payee between "ACH" and "TYPE:". Found with one fixed-text search and
    # str.find, so a description repeating "ACH" can't make this slow. The text
    # is already squashed, so words are separated by single spaces.
    ach = re.search(r"\bACH ", text)
    if ach:
        type_at = text.find(" TYPE:", ach.end())
        if type_at > ach.end():
            return text[ach.end():type_at].strip()[:40]
    kept: List[str] = []
    for token in text.split():
        if re.fullmatch(r"[0-9#*.\-]+", token):
            break  # stop at the first id/number-ish token
        kept.append(token)
        if len(kept) >= 3:
            break
    core = " ".join(kept).strip()
    return (core or text)[:40]


def _rule_amount_display(amount_min: Any, amount_max: Any) -> Dict[str, Any]:
    """Derive the UI mode (any/exact/range) and prefill values for a rule's
    optional amount constraint."""
    lo = None if amount_min is None else float(amount_min)
    hi = None if amount_max is None else float(amount_max)
    if lo is None and hi is None:
        return {"amount_mode": "any", "amount_exact": "", "amount_min": "", "amount_max": ""}
    if lo is not None and hi is not None and abs(lo - hi) < 0.005:
        return {"amount_mode": "exact", "amount_exact": f"{lo:.2f}", "amount_min": "", "amount_max": ""}
    return {
        "amount_mode": "range",
        "amount_exact": "",
        "amount_min": "" if lo is None else f"{lo:.2f}",
        "amount_max": "" if hi is None else f"{hi:.2f}",
    }


def load_match_rules_for_item(user_id: int, source_type: str, source_id: int) -> List[Dict[str, Any]]:
    rules = query_all("SELECT id, pattern, amount_min, amount_max FROM expected_match_rules "
                "WHERE user_id = ? AND source_type = ? AND source_id = ? ORDER BY id", [user_id, source_type, source_id])
    for rule in rules:
        rule.update(_rule_amount_display(rule.get("amount_min"), rule.get("amount_max")))
    return rules


def _expected_item_kind(conn: duckdb.DuckDBPyConnection, user_id: int, source_type: str, source_id: int) -> Optional[str]:
    table = "bills" if source_type == "bill" else "one_time_items"
    row = conn.execute(f"SELECT kind FROM {table} WHERE id = ? AND user_id = ?", [source_id, user_id]).fetchone()
    return row[0] if row else None


def apply_match_rules(
    user_id: int, rule_ids: Optional[List[int]] = None, import_ids: Optional[List[int]] = None
) -> int:
    """Auto-link unreconciled actuals whose description contains a rule pattern
    (with the expected item's sign). Runs retroactively (rule create/edit) and
    on new imports (pass import_ids). Returns the number of new links."""
    linked = 0
    with get_connection() as conn:
        rule_columns = "id, source_type, source_id, pattern, amount_min, amount_max"
        if rule_ids is not None:
            if not rule_ids:
                return 0
            placeholders = ", ".join("?" for _ in rule_ids)
            rules = conn.execute(
                f"SELECT {rule_columns} FROM expected_match_rules "
                f"WHERE user_id = ? AND id IN ({placeholders})",
                [user_id, *rule_ids],
            ).fetchall()
        else:
            rules = conn.execute(
                f"SELECT {rule_columns} FROM expected_match_rules WHERE user_id = ?",
                [user_id],
            ).fetchall()

        for _rule_id, source_type, source_id, pattern, amount_min, amount_max in rules:
            kind = _expected_item_kind(conn, user_id, source_type, source_id)
            if not kind or not pattern.strip():
                continue
            sign_condition = "i.amount < 0" if kind == "expense" else "i.amount > 0"
            params: List[Any] = [user_id, f"%{pattern.upper()}%", user_id]
            # Optional amount constraint, compared against the magnitude. A small
            # epsilon absorbs float rounding so an "exact" bound matches cleanly.
            amount_filter = ""
            if amount_min is not None:
                amount_filter += " AND ABS(i.amount) >= ?"
                params.append(float(amount_min) - 0.005)
            if amount_max is not None:
                amount_filter += " AND ABS(i.amount) <= ?"
                params.append(float(amount_max) + 0.005)
            import_filter = ""
            if import_ids is not None:
                if not import_ids:
                    continue
                placeholders = ", ".join("?" for _ in import_ids)
                import_filter = f" AND i.id IN ({placeholders})"
                params.extend(import_ids)
            candidates = conn.execute(
                f"""
                SELECT i.id FROM imported_transactions i
                WHERE i.user_id = ?
                  AND NOT i.is_transfer
                  AND NOT i.unexpected
                  AND {sign_condition}
                  AND UPPER(i.description) LIKE ?
                  AND i.id NOT IN (
                      SELECT imported_transaction_id FROM expected_reconciliations WHERE user_id = ?
                  )
                  {amount_filter}
                  {import_filter}
                """,
                params,
            ).fetchall()
            for (import_id,) in candidates:
                conn.execute(
                    """
                    INSERT INTO expected_reconciliations (user_id, imported_transaction_id, source_type, source_id, matched_via)
                    VALUES (?, ?, ?, ?, 'rule')
                    """,
                    [user_id, import_id, source_type, source_id],
                )
                linked += 1
    return linked


def resync_item_rules(user_id: int, source_type: str, source_id: int) -> int:
    """Drop this item's auto (rule) links and re-apply its current rules. Manual
    ('confirm') links are preserved."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM expected_reconciliations "
            "WHERE user_id = ? AND source_type = ? AND source_id = ? AND matched_via = 'rule'",
            [user_id, source_type, source_id],
        )
        rule_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM expected_match_rules WHERE user_id = ? AND source_type = ? AND source_id = ?",
                [user_id, source_type, source_id],
            ).fetchall()
        ]
    return apply_match_rules(user_id, rule_ids=rule_ids) if rule_ids else 0


def _validate_amount_bounds(
    amount_min: Optional[float], amount_max: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    lo = None if amount_min is None else round(float(amount_min), 2)
    hi = None if amount_max is None else round(float(amount_max), 2)
    if lo is not None and lo < 0:
        raise ValueError("Amount must be zero or greater")
    if hi is not None and hi < 0:
        raise ValueError("Amount must be zero or greater")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Minimum amount cannot be greater than the maximum")
    return lo, hi


def create_match_rule(
    user_id: int,
    source_type: str,
    source_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
) -> int:
    cleaned = pattern.strip()
    if not cleaned:
        raise ValueError("Match pattern cannot be empty")
    lo, hi = _validate_amount_bounds(amount_min, amount_max)
    row = fetch_one("INSERT INTO expected_match_rules (user_id, source_type, source_id, pattern, amount_min, amount_max) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id", [user_id, source_type, source_id, cleaned, lo, hi])
    resync_item_rules(user_id, source_type, source_id)
    return int(row[0])


def update_match_rule(
    user_id: int,
    rule_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
) -> None:
    cleaned = pattern.strip()
    if not cleaned:
        raise ValueError("Match pattern cannot be empty")
    lo, hi = _validate_amount_bounds(amount_min, amount_max)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_type, source_id FROM expected_match_rules WHERE id = ? AND user_id = ?",
            [rule_id, user_id],
        ).fetchone()
        if not row:
            raise ValueError("Match rule not found")
        conn.execute(
            "UPDATE expected_match_rules SET pattern = ?, amount_min = ?, amount_max = ? WHERE id = ? AND user_id = ?",
            [cleaned, lo, hi, rule_id, user_id],
        )
    resync_item_rules(user_id, row[0], int(row[1]))


def delete_match_rule(user_id: int, rule_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_type, source_id FROM expected_match_rules WHERE id = ? AND user_id = ?",
            [rule_id, user_id],
        ).fetchone()
        conn.execute("DELETE FROM expected_match_rules WHERE id = ? AND user_id = ?", [rule_id, user_id])
    if row:
        resync_item_rules(user_id, row[0], int(row[1]))


def unlink_reconciliation(user_id: int, recon_id: int) -> None:
    execute("DELETE FROM expected_reconciliations WHERE id = ? AND user_id = ?", [recon_id, user_id])


def _load_reconciliation_rows(
    user_id: int, source_type: str, source_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Linked actuals for one source type, optionally narrowed to a single item."""
    sql = """
                SELECT r.source_id, r.id AS recon_id, r.matched_via, r.imported_transaction_id,
                       i.account, i.tx_date, i.description, i.merchant, i.amount, i.flow
                FROM expected_reconciliations r
                JOIN imported_transactions i
                  ON i.id = r.imported_transaction_id AND i.user_id = r.user_id
                WHERE r.user_id = ? AND r.source_type = ?
                """
    params: List[Any] = [user_id, source_type]
    if source_id is not None:
        sql += " AND r.source_id = ?"
        params.append(source_id)

    rows = query_all(sql + " ORDER BY i.tx_date DESC", params)
    for row in rows:
        if isinstance(row["tx_date"], datetime):
            row["tx_date"] = row["tx_date"].date()
    return rows


def load_reconciliations_for_item(user_id: int, source_type: str, source_id: int) -> List[Dict[str, Any]]:
    return _load_reconciliation_rows(user_id, source_type, source_id)


def load_reconciliations_by_item(user_id: int, source_type: str) -> Dict[int, List[Dict[str, Any]]]:
    """Linked actuals grouped by source id — the batched form of
    load_reconciliations_for_item, so a whole-ledger pass costs one query."""
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in _load_reconciliation_rows(user_id, source_type):
        grouped.setdefault(int(row["source_id"]), []).append(row)
    return grouped


def load_reconciliation_counts(user_id: int) -> Dict[tuple, int]:
    """(source_type, source_id) -> number of linked actuals, for the list page."""
    rows = fetch_all("SELECT source_type, source_id, COUNT(*) FROM expected_reconciliations "
            "WHERE user_id = ? GROUP BY source_type, source_id", [user_id])
    return {(row[0], int(row[1])): int(row[2]) for row in rows}


def bill_occurrence_status(
    bill: Dict[str, Any], window_start: date, window_end: date, linked_imports: List[Dict[str, Any]],
    overrides: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Map each due date of a bill to a linked actual within a tolerance
    window → paid / expected, for the reconcile view."""
    used: set = set()
    result: List[Dict[str, Any]] = []
    for due in bill_due_dates(bill, window_start, window_end, overrides):
        occ_date = due["date"]
        best = None
        best_days = RECONCILE_OCCURRENCE_TOLERANCE_DAYS + 1
        for linked in linked_imports:
            if linked["recon_id"] in used:
                continue
            days = abs((linked["tx_date"] - occ_date).days)
            if days <= RECONCILE_OCCURRENCE_TOLERANCE_DAYS and days < best_days:
                best = linked
                best_days = days
        if best is not None:
            used.add(best["recon_id"])
        result.append(
            {
                "date": occ_date,
                "amount": due["amount"],
                "paid": best is not None,
                "match": best,
            }
        )
    return result


def reconciled_occurrence_dates(
    user_id: int, bills: List[Dict[str, Any]], through: date, window_end: date
) -> Dict[int, set]:
    """bill id -> due dates already settled by a linked actual dated on/before
    `through` (the checking actuals cutover).

    A bill paid early straddles the cutover: the actual lands in the actuals half
    of the ledger while its due date still falls in the forecast half, so the
    ledger would count the same money twice. Matching mirrors
    bill_occurrence_status, so the due date the reconcile page calls paid is
    exactly the one dropped from the forecast."""
    linked_by_bill = load_reconciliations_by_item(user_id, "bill")
    if not linked_by_bill:
        return {}

    settled: Dict[int, set] = {}
    for bill in bills:
        bill_id = int(bill["id"])
        linked = linked_by_bill.get(bill_id)
        if not linked:
            continue
        dates = {
            occurrence["date"]
            for occurrence in bill_occurrence_status(
                bill, occurrence_history_start(bill), window_end, linked
            )
            if occurrence["paid"] and occurrence["match"]["tx_date"] <= through
        }
        if dates:
            settled[bill_id] = dates
    return settled


# ---------------------------------------------------------------------------
# Transaction splits
#
# One imported transaction, several categories — a $130 store run that is really
# groceries + kids + household. Parts are stored as positive magnitudes; the sign
# always follows the parent transaction, so a split can never flip an expense
# into income.
#
# Splitting is partial by design: name the parts you care about and whatever is
# left (the remainder) stays on the transaction's own effective category. That
# keeps the ledger honest without forcing you to account for a whole receipt in
# one sitting. Invariant: 0 <= sum(parts) <= abs(amount).
# ---------------------------------------------------------------------------


def load_imported_transaction_by_id(user_id: int, tx_id: int) -> Optional[Dict[str, Any]]:
    """One imported transaction with its effective category (a reconciled expected
    item's category wins over the import's own, matching the ledger and budget)."""
    rows = query_all(f"""
                SELECT i.id, i.tx_date, i.description, i.merchant, i.amount, i.account, i.account_id,
                       i.is_transfer, i.category_id, i.category_via, i.category_rule_id, i.transfer_rule_id,
                       i.unexpected, i.note,
                       COALESCE({category_label("ec")}, {category_label("ic")}) AS category_name,
                       COALESCE(ec.color, ic.color) AS category_color,
                       COALESCE(b.name, ot.name) AS expected_name
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN bills b
                       ON r.source_type = 'bill' AND b.id = r.source_id AND b.user_id = i.user_id
                LEFT JOIN one_time_items ot
                       ON r.source_type = 'one_time' AND ot.id = r.source_id AND ot.user_id = i.user_id
                {category_join("ec", "COALESCE(b.category_id, ot.category_id)", "i.user_id")}
                {category_join("ic", "i.category_id", "i.user_id")}
                WHERE i.id = ? AND i.user_id = ?
                """, [tx_id, user_id])
    if not rows:
        return None
    row = rows[0]
    if isinstance(row["tx_date"], datetime):
        row["tx_date"] = row["tx_date"].date()
    row["category_color"] = safe_hex_color(row.get("category_color"))
    row["magnitude"] = round(abs(float(row["amount"])), 2)
    return row


def load_transaction_splits(user_id: int, tx_id: int) -> List[Dict[str, Any]]:
    """Split parts for one transaction, oldest first."""
    rows = query_all(f"""
                SELECT ts.id, ts.category_id, ts.amount, ts.note,
                       {category_label("c")} AS category_name, c.color AS category_color
                FROM transaction_splits ts
                {category_join("c", "ts.category_id", "ts.user_id", "")}
                WHERE ts.user_id = ? AND ts.imported_transaction_id = ?
                ORDER BY ts.id
                """, [user_id, tx_id])
    for row in rows:
        row["amount"] = round(float(row["amount"]), 2)
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def split_remainder(user_id: int, tx_id: int) -> float:
    """What is left on the transaction's own category after its split parts."""
    transaction = load_imported_transaction_by_id(user_id, tx_id)
    if not transaction:
        return 0.0
    allocated = sum(part["amount"] for part in load_transaction_splits(user_id, tx_id))
    return round(float(transaction["magnitude"]) - allocated, 2)


def save_transaction_splits(
    user_id: int, tx_id: int, parts: List[Dict[str, Any]]
) -> float:
    """Replace a transaction's split parts wholesale. `parts` is a list of
    {category_id, amount, note}; blank/zero amounts are dropped so an emptied row
    in the editor simply removes that part. Returns the resulting remainder."""
    transaction = load_imported_transaction_by_id(user_id, tx_id)
    if not transaction:
        raise ValueError("Transaction not found")

    magnitude = float(transaction["magnitude"])
    cleaned: List[Dict[str, Any]] = []
    for part in parts:
        amount = round(float(part.get("amount") or 0.0), 2)
        if amount <= 0:
            continue
        category_id = int(part["category_id"])
        if not load_category_by_id(user_id, category_id):
            raise ValueError("Category not found")
        cleaned.append({"category_id": category_id, "amount": amount, "note": (part.get("note") or "").strip()})

    allocated = round(sum(part["amount"] for part in cleaned), 2)
    if allocated > magnitude + 0.005:
        raise ValueError(
            f"Split parts total {format_currency(allocated)}, more than the "
            f"transaction's {format_currency(magnitude)}"
        )

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM transaction_splits WHERE user_id = ? AND imported_transaction_id = ?",
            [user_id, tx_id],
        )
        for part in cleaned:
            conn.execute(
                """
                INSERT INTO transaction_splits (user_id, imported_transaction_id, category_id, amount, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                [user_id, tx_id, part["category_id"], part["amount"], part["note"] or None],
            )
    return round(magnitude - allocated, 2)


def clear_transaction_splits(user_id: int, tx_id: int) -> None:
    execute(
        "DELETE FROM transaction_splits WHERE user_id = ? AND imported_transaction_id = ?",
        [user_id, tx_id],
    )


def split_counts_by_transaction(user_id: int) -> Dict[int, int]:
    """imported transaction id -> number of split parts, for ledger badges."""
    rows = fetch_all(
        "SELECT imported_transaction_id, COUNT(*) FROM transaction_splits "
        "WHERE user_id = ? GROUP BY 1",
        [user_id],
    )
    return {int(row[0]): int(row[1]) for row in rows}


# ---------------------------------------------------------------------------
# Category rules
#
# One table, two kinds. A description rule matches when the description
# contains its pattern. A merchant rule (what confirming a merchant in the
# inbox makes) matches when the merchant key equals its pattern, and only on
# spending. Both honor optional amount bounds, compared with the magnitude, and
# an optional date range.
#
# A rule only ever writes rows it owns (category_via = 'rule', with its id in
# category_rule_id), so a category picked by hand is never overwritten. When
# several rules match a row, a merchant rule beats a description rule, and the
# newest wins within a kind. Rule-set categories are worked out again from
# every rule whenever one is added, changed or deleted, and after each import.
# Description rules may name transfers on purpose: card payments are transfers,
# and budget totals already leave transfers out.
# ---------------------------------------------------------------------------

RULE_KINDS = ("description", "merchant")


def load_category_rules(user_id: int, category_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Category rules with their category, merchant rules first, newest first
    within a kind. With category_id, only that category's."""
    where, params = "", [user_id]
    if category_id is not None:
        where, params = "AND cr.category_id = ?", [user_id, category_id]
    rows = query_all(f"""
                SELECT cr.id, cr.category_id, cr.pattern, cr.match_on, cr.amount_min, cr.amount_max,
                       cr.date_from, cr.date_to,
                       {category_label("c")} AS category_name, c.color AS category_color
                FROM category_rules cr
                {category_join("c", "cr.category_id", "cr.user_id", "")}
                WHERE cr.user_id = ? {where}
                ORDER BY cr.match_on = 'merchant' DESC, cr.id DESC
                """, params)
    for row in rows:
        for key in ("date_from", "date_to"):
            if row[key] is not None:
                row[key] = _as_date(row[key])
        for key in ("amount_min", "amount_max"):
            if row[key] is not None:
                row[key] = float(row[key])
    return rows


def _clean_rule(user_id: int, category_id: int, pattern: str, match_on: str,
                amount_min: Optional[float], amount_max: Optional[float],
                date_from: Optional[date], date_to: Optional[date]) -> List[Any]:
    cleaned = (pattern or "").strip()
    if not cleaned:
        raise ValueError("A match pattern is required")
    if match_on not in RULE_KINDS:
        raise ValueError("A rule matches on the description or the merchant")
    if not load_category_by_id(user_id, category_id):
        raise ValueError("Category not found")
    lo, hi = _validate_amount_bounds(amount_min, amount_max)
    if date_from and date_to and date_from > date_to:
        raise ValueError("The rule's start date can't be after its end date")
    return [category_id, cleaned, match_on, lo, hi, date_from, date_to]


def create_category_rule(
    user_id: int,
    category_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    match_on: str = "description",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> int:
    """Create a rule, then work out every rule-set category again. Returns the
    new rule's id."""
    values = _clean_rule(user_id, category_id, pattern, match_on, amount_min, amount_max, date_from, date_to)
    row = fetch_one(
        "INSERT INTO category_rules (user_id, category_id, pattern, match_on, amount_min, amount_max, date_from, "
        "date_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [user_id, *values],
    )
    resync_category_rules(user_id)
    return int(row[0])


def update_category_rule(
    user_id: int,
    rule_id: int,
    category_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    match_on: str = "description",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> None:
    """Edit a rule, then work out every rule-set category again, so rows the old
    rule claimed are released and the new one applies."""
    values = _clean_rule(user_id, category_id, pattern, match_on, amount_min, amount_max, date_from, date_to)
    execute(
        "UPDATE category_rules SET category_id = ?, pattern = ?, match_on = ?, amount_min = ?, amount_max = ?, "
        "date_from = ?, date_to = ? WHERE id = ? AND user_id = ?",
        [*values, rule_id, user_id],
    )
    resync_category_rules(user_id)


def delete_category_rule(user_id: int, rule_id: int) -> None:
    execute("DELETE FROM category_rules WHERE id = ? AND user_id = ?", [rule_id, user_id])
    resync_category_rules(user_id)


def _rule_matches(rule: Dict[str, Any], row: Dict[str, Any]) -> bool:
    magnitude = abs(float(row["amount"]))
    if rule["amount_min"] is not None and magnitude < float(rule["amount_min"]) - 0.005:
        return False
    if rule["amount_max"] is not None and magnitude > float(rule["amount_max"]) + 0.005:
        return False
    if rule["date_from"] is not None and row["tx_date"] < rule["date_from"]:
        return False
    if rule["date_to"] is not None and row["tx_date"] > rule["date_to"]:
        return False
    if rule["match_on"] == "merchant":
        return row["spending"] and row["merchant_key"] == rule["pattern"]
    return rule["pattern"].upper() in str(row["description"] or "").upper()


def rule_rows(user_id: int, where: str = "", params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Imported rows with what rule matching reads: description, merchant_key,
    amount, tx_date and whether it is spending (money out that is not a
    transfer, or a card refund), as spending_parts counts it."""
    rows = query_all(f"""
            SELECT i.id, i.description, COALESCE(NULLIF(i.merchant, ''), i.description) AS merchant, i.amount,
                   i.tx_date, i.category_id, i.category_rule_id,
                   (NOT i.is_transfer AND (i.amount < 0 OR COALESCE(acct.account_type = 'credit_card', FALSE)))
                       AS spending
            FROM imported_transactions i
            LEFT JOIN accounts acct ON acct.id = i.account_id AND acct.user_id = i.user_id
            WHERE i.user_id = ? {where}
            """, [user_id, *params])
    for row in rows:
        row["tx_date"] = _as_date(row["tx_date"])
        row["merchant_key"] = merchant_key(row["merchant"])
        row["spending"] = bool(row["spending"])
    return rows


def category_rule_candidates(user_id: int, where: str = "", params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Rows a category rule may categorize: uncategorized, or set by a rule."""
    return rule_rows(user_id, f"AND (i.category_id IS NULL OR i.category_via = 'rule') {where}", params)


def winning_rule(rules: List[Dict[str, Any]], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The rule that sets this row's category: rules come merchant first,
    newest first, as load_category_rules orders them."""
    for rule in rules:
        if _rule_matches(rule, row):
            return rule
    return None


def _in_chunks(ids: List[int], size: int = 500):
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def resync_category_rules(user_id: int) -> int:
    """Work out every rule-set category again from all rules. Hand-picked
    categories (category_via IS NULL) are never touched. Returns how many rows
    changed."""
    rules = load_category_rules(user_id)
    wanted: Dict[tuple, List[int]] = {}
    released: List[int] = []
    for row in category_rule_candidates(user_id):
        rule = winning_rule(rules, row)
        if rule is None:
            if row["category_rule_id"] is not None or row["category_id"] is not None:
                released.append(int(row["id"]))
            continue
        target = (int(rule["category_id"]), int(rule["id"]))
        current = (row["category_id"], row["category_rule_id"])
        if current != target:
            wanted.setdefault(target, []).append(int(row["id"]))
    with get_connection() as conn:
        for chunk in _in_chunks(released):
            conn.execute(
                f"UPDATE imported_transactions SET category_id = NULL, category_via = NULL, category_rule_id = NULL "
                f"WHERE user_id = ? AND id IN ({', '.join('?' for _ in chunk)})", [user_id, *chunk])
        for (category_id, rule_id), ids in wanted.items():
            for chunk in _in_chunks(ids):
                conn.execute(
                    f"UPDATE imported_transactions SET category_id = ?, category_via = 'rule', category_rule_id = ? "
                    f"WHERE user_id = ? AND id IN ({', '.join('?' for _ in chunk)})",
                    [category_id, rule_id, user_id, *chunk])
    return len(released) + sum(len(ids) for ids in wanted.values())


def settled_one_time_ids(user_id: int, through: date) -> set:
    """One-time expected items already settled by a linked actual dated on/before
    `through`. A one-time item has a single occurrence, so any link settles it."""
    rows = fetch_all("""
                SELECT DISTINCT r.source_id
                FROM expected_reconciliations r
                JOIN imported_transactions i
                  ON i.id = r.imported_transaction_id AND i.user_id = r.user_id
                WHERE r.user_id = ? AND r.source_type = 'one_time' AND i.tx_date <= ?
                """, [user_id, through])
    return {int(row[0]) for row in rows}



# ---------------------------------------------------------------------------
# One-time items settle themselves (#101)
#
# A one-time item is paid by an actual with its sign, dated near its expected
# date, on its account when it has one. Metis links the two only when it is
# certain: exactly one candidate for the same amount to the cent, which no other
# unsettled item also matches exactly. Anything less is uncertain, and the item
# is listed for the owner instead.
# ---------------------------------------------------------------------------

ONE_TIME_SETTLE_DAYS = 10
ONE_TIME_NEAR_SHARE = 0.02


def _one_time_settlement(user_id: int) -> Tuple[List[Tuple[Dict[str, Any], int]], List[Dict[str, Any]]]:
    """(certain, uncertain): each certain item with the actual that pays it, and
    the unsettled items whose match is uncertain."""
    items = _load_one_time_items(
        user_id,
        "AND o.id NOT IN (SELECT source_id FROM expected_reconciliations "
        "WHERE user_id = ? AND source_type = 'one_time')",
        [user_id],
    )
    if not items:
        return [], []
    window = timedelta(days=ONE_TIME_SETTLE_DAYS)
    actuals = query_all("""
            SELECT i.id, i.tx_date, i.amount, i.account_id
            FROM imported_transactions i
            WHERE i.user_id = ? AND NOT i.is_transfer AND NOT i.unexpected AND i.tx_date >= ? AND i.tx_date <= ?
              AND i.id NOT IN (SELECT imported_transaction_id FROM expected_reconciliations WHERE user_id = ?)
            """, [user_id, min(item["expected_date"] for item in items) - window,
                  max(item["expected_date"] for item in items) + window, user_id])

    exact: Dict[int, List[int]] = {}
    near: Dict[int, List[int]] = {}
    for item in items:
        target = round(item["amount"], 2)
        for actual in actuals:
            amount = float(actual["amount"])
            if (amount > 0) != (item["kind"] == "income") or amount == 0:
                continue
            if item.get("account_id") and actual["account_id"] != item["account_id"]:
                continue
            if abs((_as_date(actual["tx_date"]) - item["expected_date"]).days) > ONE_TIME_SETTLE_DAYS:
                continue
            gap = abs(round(abs(amount), 2) - target)
            if gap < 0.005:
                exact.setdefault(item["id"], []).append(int(actual["id"]))
            elif gap <= target * ONE_TIME_NEAR_SHARE + 0.005:
                near.setdefault(item["id"], []).append(int(actual["id"]))

    claims: Dict[int, int] = {}
    for found in exact.values():
        for tx_id in found:
            claims[tx_id] = claims.get(tx_id, 0) + 1
    certain, uncertain = [], []
    for item in items:
        found = exact.get(item["id"], [])
        if len(found) == 1 and claims[found[0]] == 1:
            certain.append((item, found[0]))
        elif found or near.get(item["id"]):
            uncertain.append(item)
    return certain, uncertain


def settle_one_time_items(user_id: int) -> Dict[str, Any]:
    """Link each one-time item to the actual that certainly pays it. Returns how
    many were linked and the ids of the items left uncertain."""
    certain, uncertain = _one_time_settlement(user_id)
    linked = sum(
        1 for item, tx_id in certain if confirm_reconciliation(user_id, "one_time", item["id"], tx_id, matched_via="auto")
    )
    return {"linked": linked, "uncertain": [item["id"] for item in uncertain]}


def uncertain_one_time_matches(user_id: int) -> List[Dict[str, Any]]:
    """Unsettled one-time items with a possible match Metis is not sure of."""
    return _one_time_settlement(user_id)[1]
