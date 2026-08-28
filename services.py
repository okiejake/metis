import calendar
import csv
import hashlib
import io
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote_plus

import duckdb
from fastapi import Request
from fastapi.responses import RedirectResponse, Response

DB_PATH = os.getenv("FINANCE_DB_PATH", "finance.duckdb")
VALID_KINDS = {"income", "expense"}
VALID_FREQUENCIES = {"weekly", "biweekly", "semimonthly", "monthly", "yearly", "every_x_months"}
DEFAULT_CATEGORY_COLOR = "#64748b"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FORECAST_WINDOW_SETTINGS_KEY = "forecast_window"
FORECAST_WINDOW_FALLBACK_START_KEY = "ledger_window_start"
FORECAST_WINDOW_FALLBACK_END_KEY = "ledger_window_end"
FORECAST_WINDOW_DEFAULT_DAYS = 180
DEFAULT_USER_SLUG = "personal"
DEFAULT_USER_DISPLAY_NAME = "Personal"
USER_SLUG_COOKIE = "metis_user"
USER_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365 * 5
USER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")



def format_currency(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"




def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH)


def table_has_column(conn: duckdb.DuckDBPyConnection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(row[1] == column_name for row in rows)


def categories_has_global_name_unique(conn: duckdb.DuckDBPyConnection) -> bool:
    rows = conn.execute(
        """
        SELECT constraint_column_names
        FROM duckdb_constraints()
        WHERE table_name = 'categories' AND constraint_type = 'UNIQUE'
        """
    ).fetchall()
    return any(list(row[0]) == ["name"] for row in rows)


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


def normalize_user_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not cleaned:
        return ""
    if not EMAIL_RE.match(cleaned):
        raise ValueError("Please enter a valid email address")
    return cleaned


def slugify_user_name(display_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", display_name.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("User name must include letters or numbers")
    return normalize_user_slug(cleaned[:32])


def resolve_safe_redirect_target(target: str) -> str:
    cleaned = target.strip()
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
        conn.execute("DELETE FROM recurring_items WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM manual_transactions WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM imported_transactions WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM expected_reconciliations WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM expected_match_rules WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM category_rules WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM transaction_splits WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM accounts WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM categories WHERE user_id = ?", [user_id])
        conn.execute("DELETE FROM settings WHERE key LIKE ?", [f"user:{user_id}:%"])
        conn.execute("DELETE FROM users WHERE id = ?", [user_id])


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS users_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS categories_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS recurring_items_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS manual_transactions_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS amount_overrides_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS imported_transactions_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS expected_reconciliations_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS expected_match_rules_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS accounts_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS import_templates_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS category_rules_id_seq START 1")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS transaction_splits_id_seq START 1")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY DEFAULT nextval('users_id_seq'),
                slug TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (slug, display_name)
            VALUES (?, ?)
            ON CONFLICT DO NOTHING
            """,
            [DEFAULT_USER_SLUG, DEFAULT_USER_DISPLAY_NAME],
        )
        default_user_row = conn.execute(
            "SELECT id FROM users WHERE slug = ? LIMIT 1",
            [DEFAULT_USER_SLUG],
        ).fetchone()
        if not default_user_row:
            default_user_row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        default_user_id = int(default_user_row[0])

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
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
            CREATE TABLE IF NOT EXISTS recurring_items (
                id BIGINT PRIMARY KEY DEFAULT nextval('recurring_items_id_seq'),
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount DOUBLE NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE,
                frequency_type TEXT NOT NULL,
                interval_months INTEGER,
                semimonthly_day1 INTEGER,
                semimonthly_day2 INTEGER,
                day_of_month INTEGER,
                category_id BIGINT,
                user_id BIGINT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_transactions (
                id BIGINT PRIMARY KEY DEFAULT nextval('manual_transactions_id_seq'),
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount DOUBLE NOT NULL,
                tx_date DATE NOT NULL,
                category_id BIGINT,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS amount_overrides (
                id BIGINT PRIMARY KEY DEFAULT nextval('amount_overrides_id_seq'),
                recurring_item_id BIGINT NOT NULL,
                effective_date DATE NOT NULL,
                amount DOUBLE NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_transactions (
                id BIGINT PRIMARY KEY DEFAULT nextval('imported_transactions_id_seq'),
                user_id BIGINT NOT NULL,
                account TEXT NOT NULL,
                tx_date DATE NOT NULL,
                description TEXT NOT NULL,
                merchant TEXT NOT NULL DEFAULT '',
                amount DOUBLE NOT NULL,
                flow TEXT NOT NULL,
                is_transfer BOOLEAN NOT NULL DEFAULT FALSE,
                raw_type TEXT NOT NULL DEFAULT '',
                category_id BIGINT,
                source_file TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
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
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expected_match_rules (
                id BIGINT PRIMARY KEY DEFAULT nextval('expected_match_rules_id_seq'),
                user_id BIGINT NOT NULL,
                source_type TEXT NOT NULL,
                source_id BIGINT NOT NULL,
                pattern TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
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
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS category_rules (
                id BIGINT PRIMARY KEY DEFAULT nextval('category_rules_id_seq'),
                user_id BIGINT NOT NULL,
                category_id BIGINT NOT NULL,
                pattern TEXT NOT NULL,
                amount_min DOUBLE,
                amount_max DOUBLE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id BIGINT PRIMARY KEY DEFAULT nextval('accounts_id_seq'),
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                statement_day INTEGER,
                due_day INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS import_templates (
                id BIGINT PRIMARY KEY DEFAULT nextval('import_templates_id_seq'),
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                account_id BIGINT NOT NULL,
                date_field TEXT NOT NULL,
                date_format TEXT,
                amount_field TEXT NOT NULL,
                amount_sign TEXT NOT NULL DEFAULT 'standard',
                type_field TEXT,
                credit_value TEXT,
                description_field TEXT NOT NULL,
                merchant_field TEXT,
                signature TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dismissed_suggestions (
                user_id BIGINT NOT NULL,
                suggestion_key TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'dismissed',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, suggestion_key)
            )
            """
        )

        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('starting_balance', '0')
            ON CONFLICT DO NOTHING
            """
        )

        if not table_has_column(conn, "recurring_items", "category_id"):
            conn.execute("ALTER TABLE recurring_items ADD COLUMN category_id BIGINT")
        if not table_has_column(conn, "manual_transactions", "category_id"):
            conn.execute("ALTER TABLE manual_transactions ADD COLUMN category_id BIGINT")
        if not table_has_column(conn, "expected_match_rules", "amount_min"):
            conn.execute("ALTER TABLE expected_match_rules ADD COLUMN amount_min DOUBLE")
        if not table_has_column(conn, "expected_match_rules", "amount_max"):
            conn.execute("ALTER TABLE expected_match_rules ADD COLUMN amount_max DOUBLE")
        if not table_has_column(conn, "categories", "color"):
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT")
        if not table_has_column(conn, "categories", "user_id"):
            conn.execute("ALTER TABLE categories ADD COLUMN user_id BIGINT")
        # Optional explicit monthly budget for a category. NULL = derive the budget
        # from the category's expected items, as before.
        if not table_has_column(conn, "categories", "budget_amount"):
            conn.execute("ALTER TABLE categories ADD COLUMN budget_amount DOUBLE")
        if not table_has_column(conn, "recurring_items", "user_id"):
            conn.execute("ALTER TABLE recurring_items ADD COLUMN user_id BIGINT")
        if not table_has_column(conn, "manual_transactions", "user_id"):
            conn.execute("ALTER TABLE manual_transactions ADD COLUMN user_id BIGINT")
        if not table_has_column(conn, "recurring_items", "account_id"):
            conn.execute("ALTER TABLE recurring_items ADD COLUMN account_id BIGINT")
        if not table_has_column(conn, "manual_transactions", "account_id"):
            conn.execute("ALTER TABLE manual_transactions ADD COLUMN account_id BIGINT")
        if not table_has_column(conn, "imported_transactions", "account_id"):
            conn.execute("ALTER TABLE imported_transactions ADD COLUMN account_id BIGINT")
        # How a payment to this card appears on the funding account's statement.
        # Per-account so no issuer name has to live in the code.
        if not table_has_column(conn, "accounts", "payment_pattern"):
            conn.execute("ALTER TABLE accounts ADD COLUMN payment_pattern TEXT")
        # How an imported row got its category: 'rule' (set by a category rule, and
        # therefore safe to recompute) vs 'manual' (you chose it — rules never clobber it).
        if not table_has_column(conn, "imported_transactions", "category_via"):
            conn.execute("ALTER TABLE imported_transactions ADD COLUMN category_via TEXT")
        if not table_has_column(conn, "users", "email"):
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        migrate_categories_to_user_scoped_uniqueness(conn, default_user_id)
        conn.execute(
            "UPDATE categories SET color = ? WHERE color IS NULL OR TRIM(color) = ''",
            [DEFAULT_CATEGORY_COLOR],
        )
        conn.execute("UPDATE categories SET user_id = ? WHERE user_id IS NULL", [default_user_id])
        conn.execute("UPDATE recurring_items SET user_id = ? WHERE user_id IS NULL", [default_user_id])
        conn.execute("UPDATE manual_transactions SET user_id = ? WHERE user_id IS NULL", [default_user_id])
        conn.execute("UPDATE users SET email = '' WHERE email IS NULL")
        # Anchor imported rows to accounts by id. Backfill exact name matches;
        # rows whose account was renamed stay NULL and are relinked via the
        # Import-page mapping UI (load_unmapped_import_labels / map_import_label_to_account).
        conn.execute(
            """
            UPDATE imported_transactions SET account_id = (
                SELECT a.id FROM accounts a
                WHERE a.user_id = imported_transactions.user_id
                  AND LOWER(a.name) = LOWER(imported_transactions.account)
            )
            WHERE account_id IS NULL
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_user_id ON categories(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_items_user_id ON recurring_items(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_transactions_user_id ON manual_transactions(user_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_user_name_unique ON categories(user_id, name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_user_date ON imported_transactions(user_id, tx_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_user_account ON imported_transactions(user_id, account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_import_templates_user ON import_templates(user_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_fingerprint ON imported_transactions(user_id, fingerprint)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_import_unique ON expected_reconciliations(user_id, imported_transaction_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reconciliation_source ON expected_reconciliations(user_id, source_type, source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_rules_source ON expected_match_rules(user_id, source_type, source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category_rules_user ON category_rules(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category_rules_category ON category_rules(user_id, category_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transaction_splits_tx ON transaction_splits(user_id, imported_transaction_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_name_unique ON accounts(user_id, name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_items_account ON recurring_items(user_id, account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_transactions_account ON manual_transactions(user_id, account_id)")

        legacy_settings = conn.execute(
            "SELECT key, value FROM settings WHERE key NOT LIKE 'user:%:%'"
        ).fetchall()
        for key, value in legacy_settings:
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT DO NOTHING
                """,
                [f"user:{default_user_id}:{key}", value],
            )


def load_all_users() -> List[Dict[str, Any]]:
    return query_all("""
            SELECT id, slug, display_name, email
            FROM users
            ORDER BY LOWER(display_name), id
            """)


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
    cached = getattr(request.state, "current_user", None)
    if cached:
        return cached

    query_slug = request.query_params.get("user", "")
    cookie_slug = request.cookies.get(USER_SLUG_COOKIE, "")
    requested_slug = query_slug or cookie_slug or DEFAULT_USER_SLUG
    try:
        normalized_slug = normalize_user_slug(requested_slug)
    except ValueError:
        normalized_slug = DEFAULT_USER_SLUG

    user = load_user_by_slug(normalized_slug)
    if not user:
        user = get_default_user()

    request.state.current_user = user
    request.state.current_user_slug = user["slug"]
    return user


def attach_user_cookie(response: Response, user_slug: str) -> None:
    response.set_cookie(
        USER_SLUG_COOKIE,
        user_slug,
        max_age=USER_COOKIE_MAX_AGE_SECONDS,
        samesite="lax",
    )


def template_context(request: Request, message: str, err: int, **kwargs: Any) -> Dict[str, Any]:
    context = {
        "request": request,
        "message": message,
        "is_error": bool(err),
        "current_user": get_current_user(request),
        "users": load_all_users(),
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


def redirect_with_message(path: str, message: str, is_error: bool = False) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    url = f"{path}{separator}msg={quote_plus(message)}&err={1 if is_error else 0}"
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


def load_saved_forecast_window(user_id: int) -> tuple[Optional[date], Optional[date]]:
    object_value = get_setting_value(user_id, FORECAST_WINDOW_SETTINGS_KEY)
    if object_value:
        try:
            parsed = json.loads(object_value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            saved_start = parse_optional_iso_date(parsed.get("start"))
            saved_end = parse_optional_iso_date(parsed.get("end"))
            if saved_start or saved_end:
                return saved_start, saved_end

    # Backward compatibility for prior per-ledger keys.
    fallback_start = parse_optional_iso_date(get_setting_value(user_id, FORECAST_WINDOW_FALLBACK_START_KEY))
    fallback_end = parse_optional_iso_date(get_setting_value(user_id, FORECAST_WINDOW_FALLBACK_END_KEY))
    return fallback_start, fallback_end


def save_forecast_window(user_id: int, window_start: date, window_end: date) -> None:
    set_setting_value(
        user_id,
        FORECAST_WINDOW_SETTINGS_KEY,
        json.dumps({"start": window_start.isoformat(), "end": window_end.isoformat()}),
    )


def resolve_forecast_window(user_id: int, start: str = "", end: str = "") -> tuple[date, date, bool]:
    today = date.today()
    saved_start, saved_end = load_saved_forecast_window(user_id)
    # Blend-friendly default: recent past → near future so actuals + forecast both show.
    default_start = saved_start or (today - timedelta(days=90))
    default_end = saved_end or (default_start + timedelta(days=FORECAST_WINDOW_DEFAULT_DAYS))

    invalid_filter = False
    try:
        window_start = parse_iso_date(start) if start else default_start
        window_end = parse_iso_date(end) if end else default_end
    except ValueError:
        window_start = default_start
        window_end = default_end
        invalid_filter = True

    if window_end < window_start:
        window_start, window_end = window_end, window_start

    save_forecast_window(user_id, window_start, window_end)
    return window_start, window_end, invalid_filter


def load_categories(user_id: int) -> List[Dict[str, Any]]:
    rows = query_all("""
            SELECT id, name, color
            FROM categories
            WHERE user_id = ?
            ORDER BY LOWER(name), id
            """, [user_id])
    for row in rows:
        row["color"] = safe_hex_color(row.get("color"))
    return rows


def load_categories_with_usage(user_id: int) -> List[Dict[str, Any]]:
    rows = query_all("""
            SELECT c.id, c.name, c.color,
                   (SELECT COUNT(*) FROM recurring_items r WHERE r.category_id = c.id AND r.user_id = c.user_id) AS recurring_count,
                   (SELECT COUNT(*) FROM manual_transactions m WHERE m.category_id = c.id AND m.user_id = c.user_id) AS manual_count
            FROM categories c
            WHERE c.user_id = ?
            ORDER BY LOWER(c.name), c.id
            """, [user_id])
    for row in rows:
        row["color"] = safe_hex_color(row.get("color"))
    return rows


def load_category_by_id(user_id: int, category_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_one("""
            SELECT id, name, color, budget_amount
            FROM categories
            WHERE id = ? AND user_id = ?
            """, [category_id, user_id])
    if not row:
        return None
    return {
        "id": int(row[0]),
        "name": row[1],
        "color": safe_hex_color(row[2]),
        "budget_amount": float(row[3]) if row[3] is not None else None,
    }


def set_category_budget(user_id: int, category_id: int, amount: Optional[float]) -> None:
    """Set (or clear, with None) a category's explicit monthly budget. When set it
    replaces the budget derived from that category's expected items."""
    if amount is not None and amount < 0:
        raise ValueError("A budget cannot be negative")
    execute(
        "UPDATE categories SET budget_amount = ? WHERE id = ? AND user_id = ?",
        [amount, category_id, user_id],
    )


def load_category_budgets(user_id: int) -> Dict[str, float]:
    """lower(name) -> explicit monthly budget, for categories that have one."""
    rows = fetch_all(
        "SELECT name, budget_amount FROM categories WHERE user_id = ? AND budget_amount IS NOT NULL",
        [user_id],
    )
    return {str(name).strip().lower(): float(amount) for name, amount in rows}


def window_month_fraction(window_start: date, window_end: date) -> float:
    """How many months the window covers, prorated by days so a full calendar
    month counts 1.0 and half of March counts 0.5. Explicit budgets are stored
    monthly, so this is what scales them to whatever window you're looking at."""
    if window_end < window_start:
        return 0.0
    total = 0.0
    cursor = date(window_start.year, window_start.month, 1)
    end_month = date(window_end.year, window_end.month, 1)
    while cursor <= end_month:
        days_in_month = (add_months(cursor, 1) - cursor).days
        month_last_day = add_months(cursor, 1) - timedelta(days=1)
        overlap_start = max(window_start, cursor)
        overlap_end = min(window_end, month_last_day)
        if overlap_end >= overlap_start:
            total += ((overlap_end - overlap_start).days + 1) / days_in_month
        cursor = add_months(cursor, 1)
    return round(total, 4)


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


def generate_recurring_transactions(
    item: Dict[str, Any],
    window_start: date,
    window_end: date,
    overrides: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    item_start = item["start_date"]
    item_end = item["end_date"]

    if isinstance(item_start, datetime):
        item_start = item_start.date()
    if isinstance(item_end, datetime):
        item_end = item_end.date()

    effective_start = max(window_start, item_start)
    effective_end = min(window_end, item_end) if item_end else window_end
    if effective_start > effective_end:
        return []

    kind = item["kind"]
    base_amount = float(item["amount"])
    frequency = item["frequency_type"]

    if frequency == "weekly":
        occurrences = iter_every_n_days(item_start, effective_start, effective_end, 7)
    elif frequency == "biweekly":
        occurrences = iter_every_n_days(item_start, effective_start, effective_end, 14)
    elif frequency == "semimonthly":
        day1 = int(item.get("semimonthly_day1") or 1)
        day2 = int(item.get("semimonthly_day2") or 15)
        occurrences = iter_semimonthly(item_start, effective_start, effective_end, day1, day2)
    elif frequency == "monthly":
        day = int(item.get("day_of_month") or item_start.day)
        occurrences = iter_monthly(item_start, effective_start, effective_end, 1, day)
    elif frequency == "yearly":
        day = int(item.get("day_of_month") or item_start.day)
        occurrences = iter_monthly(item_start, effective_start, effective_end, 12, day)
    else:
        interval_months = int(item.get("interval_months") or 1)
        day = int(item.get("day_of_month") or item_start.day)
        occurrences = iter_monthly(item_start, effective_start, effective_end, interval_months, day)

    rows: List[Dict[str, Any]] = []
    for occurrence_date in occurrences:
        amount = _resolve_amount(base_amount, overrides or [], occurrence_date)
        delta = amount if kind == "income" else -amount
        rows.append(
            {
                "date": occurrence_date,
                "description": item["name"],
                "source": "recurring",
                "income": amount if kind == "income" else 0.0,
                "expense": amount if kind == "expense" else 0.0,
                "delta": delta,
                "kind": kind,
                "category_name": item.get("category_name") or "",
                "category_color": item.get("category_color") or "",
                "account_id": item.get("account_id"),
            }
        )

    return rows


def load_all_recurring(user_id: int) -> List[Dict[str, Any]]:
    rows = query_all("""
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.created_at, r.category_id, r.account_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.user_id = ?
            ORDER BY r.active DESC, r.kind, r.name, r.id
            """, [user_id])
    for row in rows:
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_active_recurring(user_id: int) -> List[Dict[str, Any]]:
    rows = query_all("""
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.created_at, r.category_id, r.account_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.active = TRUE AND r.user_id = ?
            ORDER BY r.kind, r.name, r.id
            """, [user_id])
    for row in rows:
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_manual_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    rows = query_all("""
            SELECT m.id, m.name, m.kind, m.amount, m.tx_date, m.category_id, m.account_id,
                   c.name AS category_name, c.color AS category_color
            FROM manual_transactions m
            LEFT JOIN categories c ON c.id = m.category_id AND c.user_id = m.user_id
            WHERE m.user_id = ? AND m.tx_date >= ? AND m.tx_date <= ?
            ORDER BY m.tx_date, m.id
            """, [user_id, window_start, window_end])

    transactions: List[Dict[str, Any]] = []
    for row in rows:
        kind = row["kind"]
        amount = float(row["amount"])
        tx_date = row["tx_date"]
        if isinstance(tx_date, datetime):
            tx_date = tx_date.date()

        transactions.append(
            {
                "date": tx_date,
                "description": row["name"],
                "source": "manual",
                "income": amount if kind == "income" else 0.0,
                "expense": amount if kind == "expense" else 0.0,
                "delta": amount if kind == "income" else -amount,
                "kind": kind,
                "id": row["id"],
                "category_name": row.get("category_name") or "",
                "category_color": safe_hex_color(row.get("category_color")),
                "account_id": row.get("account_id"),
            }
        )
    return transactions


def load_manual_transaction_by_id(user_id: int, tx_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_one("""
            SELECT m.id, m.name, m.kind, m.amount, m.tx_date, m.category_id, c.name AS category_name, c.color AS category_color
            FROM manual_transactions m
            LEFT JOIN categories c ON c.id = m.category_id AND c.user_id = m.user_id
            WHERE m.id = ? AND m.user_id = ?
            """, [tx_id, user_id])

    if not row:
        return None

    tx_date = row[4]
    if isinstance(tx_date, datetime):
        tx_date = tx_date.date()

    return {
        "id": row[0],
        "name": row[1],
        "kind": row[2],
        "amount": float(row[3]),
        "tx_date": tx_date,
        "category_id": row[5],
        "category_name": row[6] or "",
        "category_color": safe_hex_color(row[7]),
    }


def load_recurring_item_by_id(user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    rows = query_all("""
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.category_id, r.account_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.id = ? AND r.user_id = ?
            """, [item_id, user_id])
    if not rows:
        return None
    rows[0]["category_color"] = safe_hex_color(rows[0].get("category_color"))
    return rows[0]


def load_amount_overrides(recurring_item_id: int) -> List[Dict[str, Any]]:
    return query_all("SELECT id, recurring_item_id, effective_date, amount FROM amount_overrides "
            "WHERE recurring_item_id = ? ORDER BY effective_date ASC", [recurring_item_id])


def load_amount_overrides_for_items(item_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not item_ids:
        return {}
    placeholders = ", ".join("?" for _ in item_ids)
    rows = query_all(f"SELECT id, recurring_item_id, effective_date, amount FROM amount_overrides "
            f"WHERE recurring_item_id IN ({placeholders}) ORDER BY effective_date ASC", item_ids)
    result: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        rid = int(row["recurring_item_id"])
        result.setdefault(rid, []).append(row)
    return result


def save_amount_override(recurring_item_id: int, effective_date: date, amount: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM amount_overrides WHERE recurring_item_id = ? AND effective_date = ?",
            [recurring_item_id, effective_date],
        )
        conn.execute(
            "INSERT INTO amount_overrides (recurring_item_id, effective_date, amount) VALUES (?, ?, ?)",
            [recurring_item_id, effective_date, amount],
        )


def delete_overrides_for_item(recurring_item_id: int) -> None:
    execute("DELETE FROM amount_overrides WHERE recurring_item_id = ?", [recurring_item_id])


def summarize_frequency(item: Dict[str, Any]) -> str:
    frequency = item["frequency_type"]
    start = item["start_date"]
    day = item.get("day_of_month") or start.day
    if frequency == "weekly":
        return "Every week"
    if frequency == "biweekly":
        return "Every 2 weeks"
    if frequency == "semimonthly":
        return f"Twice monthly ({item.get('semimonthly_day1') or 1}, {item.get('semimonthly_day2') or 15})"
    if frequency == "monthly":
        return f"Monthly (day {day})"
    if frequency == "yearly":
        return f"Yearly ({start.strftime('%b')} {day})"
    interval = item.get("interval_months") or 1
    return f"Every {interval} month(s) (day {day})"


# Enough lookback that a charge whose deferred card due-date lands inside the
# window is still generated (one statement cycle + due offset ≈ up to ~62 days).
CYCLE_LOOKBACK_DAYS = 75

# How close (in days) a checking payment and a card's own payment row must be to
# be treated as the same payment, and how long after a statement's due date a
# recorded card payment still counts as settling it.
PAYMENT_MATCH_WINDOW_DAYS = 7
CARD_PAYMENT_GRACE_DAYS = 7


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
    due_day = account.get("due_day")

    close = resolve_cycle_day(statement_day, charge_date.year, charge_date.month)
    if close < charge_date:
        nm = _next_month_first(charge_date)
        close = resolve_cycle_day(statement_day, nm.year, nm.month)

    due = resolve_cycle_day(due_day, close.year, close.month)
    if due <= close:
        nm = _next_month_first(close)
        due = resolve_cycle_day(due_day, nm.year, nm.month)
    return close, due


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


def load_accounts(user_id: int) -> List[Dict[str, Any]]:
    return query_all("SELECT id, name, account_type, statement_day, due_day, payment_pattern FROM accounts "
                "WHERE user_id = ? ORDER BY account_type, LOWER(name), id", [user_id])


def load_account_by_id(user_id: int, account_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_one("SELECT id, name, account_type, statement_day, due_day, payment_pattern FROM accounts WHERE id = ? AND user_id = ?", [account_id, user_id])
    if not row:
        return None
    return {
        "id": int(row[0]),
        "name": row[1],
        "account_type": row[2],
        "statement_day": row[3],
        "due_day": row[4],
        "payment_pattern": row[5] or "",
    }


def load_accounts_map(user_id: int) -> Dict[int, Dict[str, Any]]:
    return {int(account["id"]): account for account in load_accounts(user_id)}


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


def ensure_seed_accounts(user_id: int) -> None:
    """First-time convenience: if the user has no accounts but has imported data,
    create one account per distinct imported account name (Checking→checking,
    others→credit_card) so they can just fill in cycle days."""
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM accounts WHERE user_id = ?", [user_id]).fetchone()[0]:
            return
        names = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT account FROM imported_transactions WHERE user_id = ? ORDER BY account",
                [user_id],
            ).fetchall()
        ]
        for name in names:
            account_type = "checking" if "checking" in name.lower() else "credit_card"
            conn.execute(
                "INSERT INTO accounts (user_id, name, account_type) VALUES (?, ?, ?)",
                [user_id, name, account_type],
            )


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


def checking_account_names(user_id: int) -> List[str]:
    """Names of the user's checking-type accounts, used as a name fallback for
    imported rows not yet anchored by account_id. Falls back to seeded 'Checking'."""
    names = [a["name"] for a in load_accounts(user_id) if a.get("account_type") == "checking"]
    return names or ["Checking"]


def checking_account_ids(user_id: int) -> List[int]:
    """Ids of the user's checking-type accounts — the stable, rename-proof key
    for selecting checking rows out of imported_transactions."""
    return [int(a["id"]) for a in load_accounts(user_id) if a.get("account_type") == "checking"]


def _checking_scope_sql(user_id: int, prefix: str = "") -> tuple[str, List[Any]]:
    """SQL predicate + params selecting a user's checking imported rows by
    account_id, with a name fallback for rows not yet anchored (account_id NULL).
    `prefix` is the column qualifier for the imported_transactions row (e.g. "i.")."""
    ids = checking_account_ids(user_id)
    names = checking_account_names(user_id)
    clauses: List[str] = []
    params: List[Any] = []
    if ids:
        clauses.append(f"{prefix}account_id IN ({', '.join('?' for _ in ids)})")
        params.extend(ids)
    clauses.append(
        f"({prefix}account_id IS NULL AND {prefix}account IN ({', '.join('?' for _ in names)}))"
    )
    params.extend(names)
    return "(" + " OR ".join(clauses) + ")", params


def _card_scope_sql(card: Dict[str, Any], prefix: str = "") -> tuple[str, List[Any]]:
    """SQL predicate + params selecting one card's imported rows by account_id,
    with a name fallback for rows not yet anchored (account_id NULL)."""
    clause = (
        f"({prefix}account_id = ? OR ({prefix}account_id IS NULL AND {prefix}account = ?))"
    )
    return clause, [int(card["id"]), card["name"]]


def latest_checking_actual_date(user_id: int) -> Optional[date]:
    clause, params = _checking_scope_sql(user_id)
    row = fetch_one(f"SELECT MAX(tx_date) FROM imported_transactions WHERE user_id = ? AND {clause}", [user_id, *params])
    value = row[0] if row else None
    if isinstance(value, datetime):
        value = value.date()
    return value


def first_checking_actual_date(user_id: int) -> Optional[date]:
    clause, params = _checking_scope_sql(user_id)
    row = fetch_one(f"SELECT MIN(tx_date) FROM imported_transactions WHERE user_id = ? AND {clause}", [user_id, *params])
    value = row[0] if row else None
    if isinstance(value, datetime):
        value = value.date()
    return value


def load_actual_checking_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """Real checking-account transactions (imported) mapped to the ledger row
    schema. Amounts are signed (negative = money out). Card payments count."""
    scope_clause, scope_params = _checking_scope_sql(user_id, "i.")
    rows = query_all(f"""
                SELECT i.id, i.tx_date, i.description, i.merchant, i.amount,
                       COALESCE(ri.name, mt.name) AS expected_name,
                       ec.name AS expected_category_name, ec.color AS expected_category_color,
                       ic.name AS import_category_name, ic.color AS import_category_color
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN recurring_items ri
                       ON r.source_type = 'recurring' AND ri.id = r.source_id AND ri.user_id = i.user_id
                LEFT JOIN manual_transactions mt
                       ON r.source_type = 'one_time' AND mt.id = r.source_id AND mt.user_id = i.user_id
                LEFT JOIN categories ec
                       ON ec.id = COALESCE(ri.category_id, mt.category_id) AND ec.user_id = i.user_id
                LEFT JOIN categories ic
                       ON ic.id = i.category_id AND ic.user_id = i.user_id
                WHERE i.user_id = ? AND {scope_clause}
                  AND i.tx_date >= ? AND i.tx_date <= ?
                ORDER BY i.tx_date, i.id
                """, [user_id, *scope_params, window_start, window_end])

    transactions: List[Dict[str, Any]] = []
    for row in rows:
        tx_date = row["tx_date"]
        if isinstance(tx_date, datetime):
            tx_date = tx_date.date()
        amount = float(row["amount"])
        raw_name = row.get("merchant") or row.get("description") or ""
        expected_name = row.get("expected_name")
        linked = bool(expected_name)
        if linked:
            display = expected_name
            category_name = row.get("expected_category_name") or row.get("import_category_name") or ""
            category_color = row.get("expected_category_color") or row.get("import_category_color")
        else:
            display = raw_name
            category_name = row.get("import_category_name") or ""
            category_color = row.get("import_category_color")
        transactions.append(
            {
                "date": tx_date,
                "description": display,
                "raw_name": raw_name,
                "linked": linked,
                "source": "actual",
                "income": amount if amount > 0 else 0.0,
                "expense": -amount if amount < 0 else 0.0,
                "delta": amount,
                "kind": "income" if amount > 0 else "expense",
                "id": row["id"],
                "category_name": category_name,
                "category_color": safe_hex_color(category_color),
            }
        )
    return transactions


def checking_actual_balance_before(user_id: int, cutoff_date: date, opening_balance: float) -> float:
    """opening_balance + sum of checking actual deltas strictly before cutoff_date.
    Gives the correct running-balance seed for any window start."""
    clause, params = _checking_scope_sql(user_id)
    row = fetch_one(f"""
            SELECT COALESCE(SUM(amount), 0) FROM imported_transactions
            WHERE user_id = ? AND {clause} AND tx_date < ?
            """, [user_id, *params, cutoff_date])
    return float(opening_balance) + float(row[0] if row else 0.0)


def card_actual_cutover(user_id: int, card: Dict[str, Any]) -> Optional[date]:
    """Latest imported transaction date for a specific card account (by account_id,
    with a name fallback for rows not yet anchored)."""
    clause, params = _card_scope_sql(card)
    row = fetch_one(f"SELECT MAX(tx_date) FROM imported_transactions WHERE user_id = ? AND {clause}", [user_id, *params])
    value = row[0] if row else None
    if isinstance(value, datetime):
        value = value.date()
    return value


def _sum_actual_card_charges(user_id: int, card: Dict[str, Any], after: date, close: date) -> float:
    """Net signed sum of a card's real (non-transfer) charges in (after, close]."""
    clause, params = _card_scope_sql(card)
    row = fetch_one(f"""
            SELECT COALESCE(SUM(amount), 0) FROM imported_transactions
            WHERE user_id = ? AND {clause} AND NOT is_transfer
              AND tx_date > ? AND tx_date <= ?
            """, [user_id, *params, after, close])
    return float(row[0] if row else 0.0)


def _card_payments_between(user_id: int, card: Dict[str, Any], after: date, through: date) -> float:
    """Total magnitude of a card's recorded payments in (after, through]."""
    clause, params = _card_scope_sql(card)
    row = fetch_one(f"SELECT COALESCE(SUM(ABS(amount)), 0) FROM imported_transactions "
            f"WHERE user_id = ? AND {clause} AND is_transfer AND tx_date > ? AND tx_date <= ?", [user_id, *params, after, through])
    return round(float(row[0] if row else 0.0), 2)


def _card_expected_charges(
    user_id: int, card_id: int, window_start: date, window_end: date
) -> List[Dict[str, Any]]:
    """Expected (recurring + one-time) charges assigned to a card as {date, delta}."""
    charges: List[Dict[str, Any]] = []
    active_items = load_active_recurring(user_id)
    item_ids = [int(item["id"]) for item in active_items]
    overrides_by_id = load_amount_overrides_for_items(item_ids)
    for item in active_items:
        if item.get("account_id") != card_id:
            continue
        for row in generate_recurring_transactions(
            item, window_start, window_end, overrides=overrides_by_id.get(int(item["id"]), [])
        ):
            charges.append(
                {
                    "date": row["date"],
                    "delta": float(row["delta"]),
                    "description": row.get("description") or "",
                    "kind": "recurring",
                    "category_name": item.get("category_name") or "",
                    "category_color": safe_hex_color(item.get("category_color")),
                }
            )
    for row in load_manual_transactions(user_id, window_start, window_end):
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


def _list_actual_card_charges(
    user_id: int, card: Dict[str, Any], after: date, close: date
) -> List[Dict[str, Any]]:
    """A card's real (non-transfer) charges in (after, close] as itemized rows
    {date, description, delta, kind:'actual'} — the imported half of a payment."""
    clause, params = _card_scope_sql(card, "i.")
    rows = query_all(f"SELECT i.tx_date, i.description, i.merchant, i.amount, "
                f"c.name AS category_name, c.color AS category_color "
                f"FROM imported_transactions i "
                f"LEFT JOIN categories c ON c.id = i.category_id AND c.user_id = i.user_id "
                f"WHERE i.user_id = ? AND {clause} AND NOT i.is_transfer "
                f"AND i.tx_date > ? AND i.tx_date <= ? ORDER BY i.tx_date, i.id", [user_id, *params, after, close])
    items: List[Dict[str, Any]] = []
    for row in rows:
        tx_date = row["tx_date"]
        items.append(
            {
                "date": tx_date.date() if isinstance(tx_date, datetime) else tx_date,
                "description": row.get("merchant") or row.get("description") or "",
                "delta": float(row["amount"]),
                "kind": "actual",
                "category_name": row.get("category_name") or "",
                "category_color": safe_hex_color(row.get("category_color")),
            }
        )
    return items


def _iter_card_cycles(card: Dict[str, Any], window_start: date, window_end: date):
    """Yield (prev_close, close, due) per monthly cycle whose due could fall in
    [window_start, window_end]. Handles 'last day of month' via resolve_cycle_day."""
    statement_day = card.get("statement_day")
    due_day = card.get("due_day")
    month = add_months(date(window_start.year, window_start.month, 1), -2)
    stop = add_months(date(window_end.year, window_end.month, 1), 2)
    while month <= stop:
        close = resolve_cycle_day(statement_day, month.year, month.month)
        prev_m = add_months(month, -1)
        prev_close = resolve_cycle_day(statement_day, prev_m.year, prev_m.month)
        due = resolve_cycle_day(due_day, close.year, close.month)
        if due <= close:
            nm = _next_month_first(close)
            due = resolve_cycle_day(due_day, nm.year, nm.month)
        yield prev_close, close, due
        month = add_months(month, 1)


def forecast_card_payments(
    user_id: int, window_start: date, window_end: date, cutover: Optional[date]
) -> List[Dict[str, Any]]:
    """One projected '{card} statement' per cycle whose due date is in the window and
    after the checking cutover. Amount = the card's actual charges in the cycle +
    expected card charges — the latter counted only after that card's own import
    cutover, so a charge that is both imported and expected is not double-counted."""
    accounts = load_accounts_map(user_id)
    payments: List[Dict[str, Any]] = []

    for card in accounts.values():
        if card.get("account_type") != "credit_card":
            continue
        if card.get("statement_day") is None or card.get("due_day") is None:
            continue  # cycle not configured

        card_cutover = card_actual_cutover(user_id, card)
        expected = _card_expected_charges(
            user_id,
            int(card["id"]),
            window_start - timedelta(days=CYCLE_LOOKBACK_DAYS),
            window_end,
        )

        for prev_close, close, due in _iter_card_cycles(card, window_start, window_end):
            if not (window_start <= due <= window_end):
                continue
            if cutover is not None and due <= cutover:
                continue  # already paid within the checking actuals

            actual_items = _list_actual_card_charges(user_id, card, prev_close, close)
            actual = sum(item["delta"] for item in actual_items)
            expected_items = [
                {
                    "date": charge["date"],
                    "description": charge.get("description") or "",
                    "delta": charge["delta"],
                    "kind": charge.get("kind") or "recurring",
                    "category_name": charge.get("category_name") or "",
                    "category_color": safe_hex_color(charge.get("category_color")),
                }
                for charge in expected
                if prev_close < charge["date"] <= close
                and (card_cutover is None or charge["date"] > card_cutover)
            ]
            expected_sum = sum(item["delta"] for item in expected_items)
            delta = round(actual + expected_sum, 2)

            # Payments the card itself recorded against this closed statement — an
            # early or late payment the checking cutover would not catch — reduce
            # what is left to pay. Their mere presence settles nothing: a card paid
            # in instalments is partly paid for most of its cycle, and treating the
            # first instalment as settlement would hide the rest of the balance.
            applied_items: List[Dict[str, Any]] = []
            if delta < 0:
                applied = _card_payments_between(
                    user_id, card, close, due + timedelta(days=CARD_PAYMENT_GRACE_DAYS)
                )
                # Never credit back more than was owed: an overpayment settles this
                # statement, it does not turn the projection into income.
                applied = min(applied, -delta)
                if applied > 0.005:
                    applied_items.append(
                        {
                            "date": close,
                            "description": "Payments already applied",
                            "delta": round(applied, 2),
                            "kind": "payment",
                            "category_name": "",
                            "category_color": safe_hex_color(None),
                        }
                    )
                    delta = round(delta + applied, 2)

            if abs(delta) < 0.005:
                continue

            card_charges = sorted(
                actual_items + expected_items, key=lambda item: item["date"]
            )
            # Appended rather than sorted in: it is an aggregate of everything paid
            # after the statement closed, so it belongs at the end of the breakdown.
            card_charges.extend(applied_items)
            payments.append(
                {
                    "date": due,
                    "description": f"{card['name']} statement",
                    "source": "card",
                    "income": delta if delta > 0 else 0.0,
                    "expense": -delta if delta < 0 else 0.0,
                    "delta": delta,
                    "kind": "income" if delta > 0 else "expense",
                    "category_name": "",
                    "category_color": "",
                    "account_id": int(card["id"]),
                    "card_charges": card_charges,
                }
            )
    return payments


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


def card_balance_summary(user_id: int, card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Amount due on a card's most recently closed statement: the statement's net
    charges (account_id-anchored) minus payments recorded since it closed."""
    if card.get("statement_day") is None or card.get("due_day") is None:
        return None
    today = date.today()
    last_cycle = None
    for prev_close, close, due in _iter_card_cycles(card, add_months(today, -3), today):
        if close <= today and (last_cycle is None or close > last_cycle[1]):
            last_cycle = (prev_close, close, due)
    if last_cycle is None:
        return None
    prev_close, close, due = last_cycle

    # Purchases are negative, so owed = -(net charges); refunds reduce it.
    statement_owed = round(-_sum_actual_card_charges(user_id, card, prev_close, close), 2)
    payments_applied = _card_payments_between(user_id, card, close, today)
    amount_due = round(statement_owed - payments_applied, 2)
    return {
        "statement_close": close,
        "due_date": due,
        "statement_owed": statement_owed,
        "payments_applied": payments_applied,
        "amount_due": max(amount_due, 0.0),
        "paid": statement_owed > 0.005 and amount_due <= 0.005,
    }


BUDGET_WINDOW_SETTINGS_KEY = "budget_window"


def _month_bounds(anchor: date) -> tuple[date, date]:
    start = date(anchor.year, anchor.month, 1)
    end = add_months(start, 1) - timedelta(days=1)
    return start, end


def resolve_budget_window(user_id: int, start: str = "", end: str = "") -> tuple[date, date, bool]:
    """Budget's own saved window (default = current month). Kept under a separate
    settings key so it never clobbers the shared Dashboard/Ledger window."""
    today = date.today()
    saved_start = saved_end = None
    stored = get_setting_value(user_id, BUDGET_WINDOW_SETTINGS_KEY)
    if stored:
        try:
            parsed = json.loads(stored)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            saved_start = parse_optional_iso_date(parsed.get("start"))
            saved_end = parse_optional_iso_date(parsed.get("end"))

    month_start, month_end = _month_bounds(today)
    default_start = saved_start or month_start
    default_end = saved_end or month_end

    invalid = False
    try:
        window_start = parse_iso_date(start) if start else default_start
        window_end = parse_iso_date(end) if end else default_end
    except ValueError:
        window_start, window_end, invalid = default_start, default_end, True

    if window_end < window_start:
        window_start, window_end = window_end, window_start

    set_setting_value(
        user_id,
        BUDGET_WINDOW_SETTINGS_KEY,
        json.dumps({"start": window_start.isoformat(), "end": window_end.isoformat()}),
    )
    return window_start, window_end, invalid


def _budget_actuals_by_category(
    user_id: int, window_start: date, window_end: date
) -> Dict[str, Dict[str, Any]]:
    """Actual imported spend/income in the window grouped by *effective* category
    (COALESCE reconciled item's category, import category). Transfers excluded so
    card payments never count as spending. Split transactions contribute each part
    to its own category and only the remainder to their effective category.
    Returns keyed by lower(name) ('' = none)."""
    rows = fetch_all("""
            WITH tx AS (
                SELECT i.id, i.amount,
                       COALESCE(ec.name, ic.name) AS cat_name,
                       COALESCE(ec.color, ic.color) AS cat_color
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN recurring_items ri
                       ON r.source_type = 'recurring' AND ri.id = r.source_id AND ri.user_id = i.user_id
                LEFT JOIN manual_transactions mt
                       ON r.source_type = 'one_time' AND mt.id = r.source_id AND mt.user_id = i.user_id
                LEFT JOIN categories ec
                       ON ec.id = COALESCE(ri.category_id, mt.category_id) AND ec.user_id = i.user_id
                LEFT JOIN categories ic
                       ON ic.id = i.category_id AND ic.user_id = i.user_id
                WHERE i.user_id = ? AND NOT i.is_transfer
                  AND i.tx_date >= ? AND i.tx_date <= ?
            ),
            allocated AS (
                SELECT imported_transaction_id AS tx_id, COALESCE(SUM(amount), 0) AS part_total
                FROM transaction_splits WHERE user_id = ? GROUP BY 1
            ),
            parts AS (
                -- What is left after splitting, on the transaction's own category.
                SELECT tx.cat_name, tx.cat_color,
                       SIGN(tx.amount) * (ABS(tx.amount) - COALESCE(a.part_total, 0)) AS total
                FROM tx LEFT JOIN allocated a ON a.tx_id = tx.id
                UNION ALL
                -- Each split part, on the category it was assigned to.
                SELECT sc.name, sc.color, SIGN(tx.amount) * ts.amount
                FROM tx
                JOIN transaction_splits ts
                       ON ts.imported_transaction_id = tx.id AND ts.user_id = ?
                JOIN categories sc ON sc.id = ts.category_id AND sc.user_id = ?
            )
            SELECT cat_name, cat_color, COALESCE(SUM(total), 0) AS total
            FROM parts
            GROUP BY 1, 2
            """, [user_id, window_start, window_end, user_id, user_id, user_id])

    result: Dict[str, Dict[str, Any]] = {}
    for cat_name, cat_color, total in rows:
        key = (cat_name or "").strip().lower()
        bucket = result.setdefault(
            key, {"name": cat_name or "Uncategorized", "color": safe_hex_color(cat_color), "expense": 0.0, "income": 0.0}
        )
        total = float(total)
        if total < 0:
            bucket["expense"] += -total
        else:
            bucket["income"] += total
    return result


def _budget_actuals_by_item(
    user_id: int, window_start: date, window_end: date
) -> Dict[tuple, float]:
    """Actual imported expense in the window attributed to a specific budgeted
    line item via its reconciliation. Keyed by (source_type, source_id) ->
    expense as a positive number. Only reconciled, non-transfer imports count;
    unreconciled category spend belongs to the category, not to any one item.
    Split parts are subtracted — money moved to another category is no longer
    this line item's spend."""
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
    result: Dict[tuple, float] = {}
    for source_type, source_id, total in rows:
        total = float(total)
        if total < 0:
            result[(source_type, int(source_id))] = round(-total, 2)
    return result


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


def build_budget_summary(user_id: int, window_start: date, window_end: date) -> Dict[str, Any]:
    """Budget vs actual vs difference by category for the window. Budgeted =
    recurring occurrences + one-time items in the window; actual = imported spend
    grouped by effective category. Expense-focused, with income shown separately."""
    # --- Budgeted, grouped by category ---
    budget_cats: Dict[str, Dict[str, Any]] = {}
    income_budgeted = 0.0

    def _cat_bucket(name: Optional[str], color: Optional[str]) -> Dict[str, Any]:
        key = (name or "").strip().lower()
        return budget_cats.setdefault(
            key,
            {
                "name": name or "Uncategorized",
                "color": safe_hex_color(color),
                "budgeted": 0.0,
                "line_items": [],
            },
        )

    active_items = load_active_recurring(user_id)
    item_ids = [int(item["id"]) for item in active_items]
    overrides_by_id = load_amount_overrides_for_items(item_ids)
    for item in active_items:
        occurrences = generate_recurring_transactions(
            item, window_start, window_end, overrides=overrides_by_id.get(int(item["id"]), [])
        )
        total = sum(abs(float(row["delta"])) for row in occurrences)
        if total <= 0:
            continue
        if item["kind"] == "income":
            income_budgeted += total
            continue
        bucket = _cat_bucket(item.get("category_name"), item.get("category_color"))
        bucket["budgeted"] += total
        bucket["line_items"].append(
            {"id": int(item["id"]), "name": item["name"], "budgeted": round(total, 2), "source": "recurring"}
        )

    for row in load_manual_transactions(user_id, window_start, window_end):
        amount = abs(float(row["delta"]))
        if amount <= 0:
            continue
        if row["kind"] == "income":
            income_budgeted += amount
            continue
        bucket = _cat_bucket(row.get("category_name"), row.get("category_color"))
        bucket["budgeted"] += amount
        bucket["line_items"].append(
            {"id": int(row["id"]), "name": row["description"], "budgeted": round(amount, 2), "source": "one_time"}
        )

    # --- Actuals, grouped by effective category and by reconciled line item ---
    actuals = _budget_actuals_by_category(user_id, window_start, window_end)
    item_actuals = _budget_actuals_by_item(user_id, window_start, window_end)
    actual_income = sum(bucket["income"] for bucket in actuals.values())

    # --- Explicit per-category budgets, prorated to the window ---
    # A category with its own monthly budget uses it instead of the sum of its
    # expected items; the items still show underneath as detail.
    monthly_budgets = load_category_budgets(user_id)
    months = window_month_fraction(window_start, window_end)
    category_meta = {c["name"].strip().lower(): c for c in load_categories(user_id)}

    # --- Merge expense categories from both sides ---
    keys = set(budget_cats) | {k for k, v in actuals.items() if v["expense"] > 0.005} | set(monthly_budgets)
    categories: List[Dict[str, Any]] = []
    for key in keys:
        explicit_monthly = monthly_budgets.get(key)
        if explicit_monthly is not None:
            budgeted = round(explicit_monthly * months, 2)
        else:
            budgeted = round(budget_cats.get(key, {}).get("budgeted", 0.0), 2)
        actual = round(actuals.get(key, {}).get("expense", 0.0), 2)
        meta = category_meta.get(key, {})
        name = (
            budget_cats.get(key, {}).get("name")
            or actuals.get(key, {}).get("name")
            or meta.get("name")
            or "Uncategorized"
        )
        color = budget_cats.get(key, {}).get("color") or actuals.get(key, {}).get("color") or meta.get("color")
        line_items = []
        for li in sorted(
            budget_cats.get(key, {}).get("line_items", []), key=lambda i: -i["budgeted"]
        ):
            li_actual = item_actuals.get((li["source"], li["id"]), 0.0)
            line_items.append(
                {
                    **li,
                    "actual": li_actual,
                    "difference": round(li["budgeted"] - li_actual, 2),
                }
            )
        # No target means nothing to exceed — without this guard every unbudgeted
        # category reads as "over" by the whole of its spend.
        has_target = budgeted > 0.005
        over = round(max(0.0, actual - budgeted), 2) if has_target else 0.0

        categories.append(
            {
                "id": meta.get("id"),
                "name": name,
                "color": safe_hex_color(color),
                "budgeted": budgeted,
                "actual": actual,
                "difference": round(budgeted - actual, 2),
                "over": over,
                "has_target": has_target,
                "explicit_monthly": explicit_monthly,
                "line_items": line_items,
            }
        )

    _apply_bar_geometry(categories)
    categories.sort(key=lambda c: (-max(c["budgeted"], c["actual"]), c["name"].lower()))

    total_budgeted = round(sum(c["budgeted"] for c in categories), 2)
    total_actual = round(sum(c["actual"] for c in categories), 2)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "months": months,
        "categories": categories,
        "totals": {
            "budgeted": total_budgeted,
            "actual": total_actual,
            "difference": round(total_budgeted - total_actual, 2),
        },
        "income": {
            "budgeted": round(income_budgeted, 2),
            "actual": round(actual_income, 2),
            "difference": round(income_budgeted - actual_income, 2),
        },
    }


def collect_blended_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """Actual checking transactions up to the latest CSV date, then the expected
    forecast after it — one coherent, non-double-counted cash timeline.

    The cutover date itself belongs to both halves. An import taken that day is
    only as complete as the bank was at the time, so a bill due that day may
    simply not have posted yet. Treating the day as pure actuals would silently
    drop it from the ledger, so expected occurrences dated on the cutover that no
    actual has settled are kept and flagged `due_not_posted`. Earlier dates get no
    such benefit of the doubt: the same import covered them in full."""
    cutover = latest_checking_actual_date(user_id)

    transactions: List[Dict[str, Any]] = []
    if cutover is not None:
        actual_end = min(window_end, cutover)
        if window_start <= actual_end:
            transactions.extend(load_actual_checking_transactions(user_id, window_start, actual_end))
        # Begin the forecast *on* the cutover. Occurrences that day which an actual
        # already settled are dropped by `paid_through` below, so only genuinely
        # unposted ones survive.
        forecast_start = max(window_start, cutover)
        # Card payments keep the old boundary — forecast_card_payments settles a
        # whole cycle at once and already skips any due on or before the cutover.
        card_forecast_start = max(window_start, cutover + timedelta(days=1))
    else:
        forecast_start = window_start
        card_forecast_start = window_start

    if forecast_start <= window_end:
        expected = collect_window_transactions(
            user_id, forecast_start, window_end, paid_through=cutover
        )
        if cutover is not None:
            for tx in expected:
                if tx["date"] == cutover:
                    tx["due_not_posted"] = True
        transactions.extend(expected)
    if card_forecast_start <= window_end:
        transactions.extend(
            forecast_card_payments(user_id, card_forecast_start, window_end, cutover)
        )

    transactions.sort(
        key=lambda tx: (
            tx["date"],
            0 if tx["kind"] == "income" else 1,
            tx["description"].lower(),
            tx.get("id", 0),
        )
    )
    return transactions


def collect_window_transactions(
    user_id: int,
    window_start: date,
    window_end: date,
    paid_through: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Expected (recurring + one-off) rows for a window. `paid_through` is the
    actuals cutover: occurrences already settled by an actual on/before it are
    dropped, so an item paid ahead of its due date is not counted twice."""
    accounts = load_accounts_map(user_id)
    gen_start = window_start

    raw: List[Dict[str, Any]] = []
    active_items = load_active_recurring(user_id)
    item_ids = [int(item["id"]) for item in active_items]
    overrides_by_id = load_amount_overrides_for_items(item_ids)
    settled_dates = (
        reconciled_occurrence_dates(user_id, active_items, paid_through, window_end)
        if paid_through is not None
        else {}
    )
    for item in active_items:
        item_overrides = overrides_by_id.get(int(item["id"]), [])
        rows = generate_recurring_transactions(item, gen_start, window_end, overrides=item_overrides)
        settled = settled_dates.get(int(item["id"]))
        if settled:
            rows = [row for row in rows if row["date"] not in settled]
        raw.extend(rows)

    manual_rows = load_manual_transactions(user_id, gen_start, window_end)
    if paid_through is not None:
        settled_one_time = settled_one_time_ids(user_id, paid_through)
        if settled_one_time:
            manual_rows = [row for row in manual_rows if int(row["id"]) not in settled_one_time]
    raw.extend(manual_rows)

    transactions = _apply_cash_flow_deferral(raw, accounts, window_start, window_end)

    transactions.sort(
        key=lambda tx: (
            tx["date"],
            0 if tx["kind"] == "income" else 1,
            tx["description"].lower(),
            tx.get("id", 0),
        )
    )
    return transactions


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
                "insert_default_date": row_date,
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
                "insert_default_date": month_cursor,
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


def _visa_merchant(description: str) -> str:
    """Visa descriptions look like 'LAKE 66        ARCADIA      OK' — the payee
    is the leading chunk before the big whitespace gap."""
    parts = re.split(r"\s{2,}", description.strip())
    return parts[0].strip() if parts else description.strip()


def _checking_merchant(description: str) -> str:
    """Pull a readable payee out of a checking-account ACH description."""
    desc = _import_squash(description)
    match = re.search(r"\bACH\s+(.+?)\s+TYPE:", desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"\bACH\s+(.+?)(?:\s+ID:|\s+CO:|$)", desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return " ".join(desc.split()[:4])


def _parse_import_date(value: str) -> date:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise CsvImportError("row is missing a date")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError as exc:
        raise CsvImportError(f"unrecognized date '{cleaned}'") from exc


def _parse_import_amount(value: str) -> float:
    cleaned = str(value or "").strip().replace(",", "").replace("$", "")
    if cleaned in ("", "-"):
        return 0.0
    try:
        return float(cleaned)
    except ValueError as exc:
        raise CsvImportError(f"unrecognized amount '{value}'") from exc


def _parse_import_date_with(value: str, date_format: Optional[str]) -> date:
    """Parse a date using a template's explicit strptime format when given,
    falling back to the built-in multi-format parser."""
    if date_format:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise CsvImportError("row is missing a date")
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            pass  # fall through to the tolerant parser
    return _parse_import_date(value)


def _parse_template_row(
    row: Dict[str, str], template: Dict[str, Any], transfer_patterns: Sequence[str] = ()
) -> Dict[str, Any]:
    """Normalize a CSV row using a user-defined template. Output mirrors the
    built-in parsers' keys and additionally carries the target account_id."""
    description = _import_squash(row.get(template["description_field"], ""))
    merchant_field = template.get("merchant_field")
    merchant = _import_squash(row.get(merchant_field, "")) if merchant_field else description

    tx_date = _parse_import_date_with(row.get(template["date_field"], ""), template.get("date_format"))

    raw = _parse_import_amount(row.get(template["amount_field"], "0"))
    if (template.get("amount_sign") or "standard") == "inverted":
        raw = -raw
    magnitude = abs(raw)

    type_field = template.get("type_field")
    raw_type = str(row.get(type_field, "")).strip() if type_field else ""
    if type_field:
        is_credit = raw_type == str(template.get("credit_value") or "").strip()
    else:
        is_credit = raw > 0  # sign-based: positive = money in

    target_is_card = template.get("account_type") == "credit_card"
    if is_credit:
        is_payment = target_is_card and bool(_CARD_PAYMENT_RE.search(description))
        if is_payment:
            amount, flow, is_transfer = -magnitude, "transfer", True
        else:
            amount, flow, is_transfer = magnitude, ("refund" if target_is_card else "income"), False
    else:
        pays_card = (not target_is_card) and _matches_any(description, transfer_patterns or ())
        amount, flow, is_transfer = -magnitude, ("transfer" if pays_card else "expense"), pays_card

    return {
        "account": template["account_name"],
        "account_id": template["account_id"],
        "tx_date": tx_date,
        "description": description,
        "merchant": merchant,
        "amount": amount,
        "flow": flow,
        "is_transfer": is_transfer,
        "raw_type": raw_type,
    }


def _parse_checking_row(row: Dict[str, str], transfer_patterns: Sequence[str] = ()) -> Dict[str, Any]:
    description = _import_squash(row.get("Description", ""))
    raw_type = str(row.get("Credit or Debit", "")).strip()
    is_credit = raw_type == "Credit"
    magnitude = abs(_parse_import_amount(row.get("Amount", "0")))
    # Credit -> money in (+), Debit -> money out (-).
    amount = magnitude if is_credit else -magnitude
    # A debit matching one of the user's card payment patterns is a transfer.
    pays_card = (not is_credit) and _matches_any(description, transfer_patterns or ())
    if is_credit:
        flow = "income"
    elif pays_card:
        flow = "transfer"
    else:
        flow = "expense"
    return {
        "account": "Checking",
        "tx_date": _parse_import_date(row.get("Processed Date", "")),
        "description": description,
        "merchant": _checking_merchant(description),
        "amount": amount,
        "flow": flow,
        "is_transfer": pays_card,
        "raw_type": raw_type,
    }


def _parse_visa_row(row: Dict[str, str], transfer_patterns: Sequence[str] = ()) -> Dict[str, Any]:
    description = _import_squash(row.get("Description", ""))
    raw_type = str(row.get("Credit or Debit", "")).strip()
    is_credit = raw_type == "Credit"
    is_payment = is_credit and bool(_CARD_PAYMENT_RE.search(description))
    is_refund = is_credit and not is_payment
    magnitude = abs(_parse_import_amount(row.get("Amount", "0")))
    # Debit on a card = purchase (money out, -). Refund = money back (+).
    # Payment = transfer; store negative (debt paid down) but flag it.
    amount = magnitude if is_refund else -magnitude
    if is_payment:
        flow = "transfer"
    elif is_refund:
        flow = "refund"
    else:
        flow = "expense"
    return {
        "account": "Visa",
        "tx_date": _parse_import_date(row.get("Processed Date", "")),
        "description": description,
        "merchant": _visa_merchant(description),
        "amount": amount,
        "flow": flow,
        "is_transfer": is_payment,
        "raw_type": raw_type,
    }


def _parse_apple_row(row: Dict[str, str], transfer_patterns: Sequence[str] = ()) -> Dict[str, Any]:
    description = _import_squash(row.get("Description", ""))
    merchant = _import_squash(row.get("Merchant", ""))
    raw_type = str(row.get("Type", "")).strip()
    usd = _parse_import_amount(row.get("Amount (USD)", "0"))  # purchases +, credits -
    amount = -usd  # purchase (+usd) -> expense (-)
    is_transfer = raw_type == "Payment"
    if raw_type == "Payment":
        flow = "transfer"
    elif raw_type == "Interest":
        flow = "fee"
    elif raw_type == "Credit":
        flow = "refund"
    else:
        flow = "expense"
    # "Other"/"Debit" rows are tiny adjustments; treat as transfer so they drop
    # out of spending totals.
    if raw_type in ("Other", "Debit"):
        is_transfer = True
        flow = "transfer"
    return {
        "account": "Apple Card",
        "tx_date": _parse_import_date(row.get("Transaction Date", "")),
        "description": description,
        "merchant": merchant,
        "amount": amount,
        "flow": flow,
        "is_transfer": is_transfer,
        "raw_type": raw_type,
    }


_ROW_PARSERS = {
    "checking": _parse_checking_row,
    "visa": _parse_visa_row,
    "apple": _parse_apple_row,
}


def detect_csv_format(header: List[str], first_row: Optional[Dict[str, str]]) -> Optional[str]:
    """Identify which export a CSV is. Checking and Visa share an identical
    6-column header, so they are disambiguated by the Account Name value."""
    fields = {str(name).strip() for name in (header or []) if name}
    if "Amount (USD)" in fields and "Transaction Date" in fields:
        return "apple"
    if {"Processed Date", "Credit or Debit", "Amount", "Account Name"}.issubset(fields):
        account_name = str((first_row or {}).get("Account Name", "")).upper()
        if "CHECKING" in account_name:
            return "checking"
        if "VISA" in account_name:
            return "visa"
    return None


def _template_signature_matches(header_fields: set, template: Dict[str, Any]) -> bool:
    signature = template.get("signature") or []
    return bool(signature) and all(col in header_fields for col in signature)


def parse_import_csv(
    filename: str,
    text: str,
    templates: Optional[List[Dict[str, Any]]] = None,
    forced_template: Optional[Dict[str, Any]] = None,
    transfer_patterns: Optional[Sequence[str]] = None,
) -> tuple[str, Optional[int], List[Dict[str, Any]]]:
    """Parse a bank/card CSV export into normalized rows.

    Tries the 3 built-in parsers first, then any user templates by signature;
    `forced_template` skips detection (manual picker). Returns
    (account_label, account_id, rows). account_id is None for built-in formats
    (resolved by name at upsert). Raises CsvImportError on an unrecognized format.
    """
    patterns = tuple(transfer_patterns or GENERIC_CARD_PAYMENT_HINTS)
    if text.startswith("﻿"):  # strip UTF-8 BOM so header cells match
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CsvImportError("The file is empty or not a valid CSV.")
    raw_rows = list(reader)
    header = list(reader.fieldnames)

    template = forced_template
    fmt: Optional[str] = None
    if template is None:
        fmt = detect_csv_format(header, raw_rows[0] if raw_rows else None)
        if fmt is None and templates:
            fields = {str(name).strip() for name in header if name}
            for candidate in templates:
                if _template_signature_matches(fields, candidate):
                    template = candidate
                    break
    if fmt is None and template is None:
        raise CsvImportError(
            "Unrecognized CSV format. Expected a Checking, Visa, or Apple Card "
            "export, or a matching custom template."
        )

    parsed: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        if not any(str(value).strip() for value in raw.values()):
            continue  # skip fully blank lines
        try:
            if template is not None:
                parsed.append(_parse_template_row(raw, template, patterns))
            else:
                parsed.append(_ROW_PARSERS[fmt](raw, patterns))
        except CsvImportError as exc:
            raise CsvImportError(f"row {index + 2}: {exc}") from exc

    if template is not None:
        return template["account_name"], int(template["account_id"]), parsed
    account_label = parsed[0]["account"] if parsed else fmt.title()
    return account_label, None, parsed


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
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _assign_import_fingerprints(rows: List[Dict[str, Any]]) -> None:
    seen: Dict[tuple, int] = {}
    for row in rows:
        base_key = (
            row["account"],
            row["tx_date"].isoformat(),
            round(float(row["amount"]), 2),
            _import_squash(row["description"]).upper(),
        )
        occurrence = seen.get(base_key, 0)
        seen[base_key] = occurrence + 1
        row["fingerprint"] = compute_import_fingerprint(row, occurrence)


def upsert_imported_transactions(
    user_id: int, rows: List[Dict[str, Any]], source_file: str
) -> Dict[str, int]:
    """Insert only rows whose fingerprint is not already present for this user.
    Idempotent: re-uploading overlapping exports adds nothing new."""
    if not rows:
        return {"inserted": 0, "skipped": 0, "transfers": 0}

    _assign_import_fingerprints(rows)
    fingerprints = [row["fingerprint"] for row in rows]

    # Anchor each row to an account by id at insert time. Prefer an account_id the
    # parser already resolved (custom templates); otherwise match on account name.
    name_to_id = {
        str(a["name"]).lower(): int(a["id"]) for a in load_accounts(user_id)
    }

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
        for row in rows:
            fingerprint = row["fingerprint"]
            if fingerprint in already_present or fingerprint in seen_in_batch:
                continue
            seen_in_batch.add(fingerprint)
            account_id = row.get("account_id")
            if account_id is None:
                account_id = name_to_id.get(str(row["account"]).lower())
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
                    row["account"],
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


def load_imported_transactions(
    user_id: int, account_filter: str = "", limit: int = 500
) -> List[Dict[str, Any]]:
    query = """
        SELECT i.id, i.account, i.tx_date, i.description, i.merchant, i.amount,
               i.flow, i.is_transfer, i.raw_type, i.category_id, i.source_file,
               c.name AS category_name, c.color AS category_color
        FROM imported_transactions i
        LEFT JOIN categories c ON c.id = i.category_id AND c.user_id = i.user_id
        WHERE i.user_id = ?
    """
    params: List[Any] = [user_id]
    if account_filter:
        query += " AND i.account = ?"
        params.append(account_filter)
    query += " ORDER BY i.tx_date DESC, i.id DESC LIMIT ?"
    params.append(limit)

    rows = query_all(query, params)

    for row in rows:
        tx_date = row["tx_date"]
        if isinstance(tx_date, datetime):
            row["tx_date"] = tx_date.date()
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_imported_accounts(user_id: int) -> List[str]:
    rows = fetch_all("SELECT DISTINCT account FROM imported_transactions WHERE user_id = ? ORDER BY account", [user_id])
    return [row[0] for row in rows]


def load_unmapped_import_labels(user_id: int) -> List[Dict[str, Any]]:
    """Distinct imported-account labels whose rows are not yet anchored to an
    account (account_id IS NULL) — i.e. the labels of already-renamed accounts
    that the exact-name backfill could not match. Each entry: {label, count}."""
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


IMPORT_TEMPLATE_SIGN_MODES = {"standard", "inverted"}


def _row_to_import_template(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signature = json.loads(row.get("signature") or "[]")
    except (json.JSONDecodeError, TypeError):
        signature = []
    if not isinstance(signature, list):
        signature = []
    row["signature"] = [str(col) for col in signature]
    return row


def load_import_templates(user_id: int) -> List[Dict[str, Any]]:
    """User-defined CSV templates joined to their target account (name + type)."""
    rows = query_all("""
                SELECT t.id, t.name, t.account_id, t.date_field, t.date_format,
                       t.amount_field, t.amount_sign, t.type_field, t.credit_value,
                       t.description_field, t.merchant_field, t.signature,
                       a.name AS account_name, a.account_type
                FROM import_templates t
                JOIN accounts a ON a.id = t.account_id AND a.user_id = t.user_id
                WHERE t.user_id = ?
                ORDER BY LOWER(t.name), t.id
                """, [user_id])
    return [_row_to_import_template(row) for row in rows]


def load_import_template(user_id: int, template_id: int) -> Optional[Dict[str, Any]]:
    for template in load_import_templates(user_id):
        if int(template["id"]) == int(template_id):
            return template
    return None


def save_import_template(
    user_id: int,
    name: str,
    account_id: int,
    date_field: str,
    amount_field: str,
    description_field: str,
    signature: List[str],
    amount_sign: str = "standard",
    date_format: Optional[str] = None,
    type_field: Optional[str] = None,
    credit_value: Optional[str] = None,
    merchant_field: Optional[str] = None,
    template_id: Optional[int] = None,
) -> None:
    """Create or update a custom import template. Raises ValueError on bad input."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Template name is required")
    for label, value in (("date column", date_field), ("amount column", amount_field), ("description column", description_field)):
        if not (value or "").strip():
            raise ValueError(f"The {label} is required")
    if amount_sign not in IMPORT_TEMPLATE_SIGN_MODES:
        raise ValueError("Amount sign must be standard or inverted")
    sig = [str(c).strip() for c in signature if str(c).strip()]
    if not sig:
        raise ValueError("List at least one header column that identifies this format")

    def _opt(value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        return value or None

    fields = [
        name, int(account_id), date_field.strip(), _opt(date_format), amount_field.strip(),
        amount_sign, _opt(type_field), _opt(credit_value), description_field.strip(),
        _opt(merchant_field), json.dumps(sig),
    ]
    with get_connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND user_id = ?", [int(account_id), user_id]
        ).fetchone():
            raise ValueError("Target account not found")
        if template_id:
            conn.execute(
                """
                UPDATE import_templates
                SET name = ?, account_id = ?, date_field = ?, date_format = ?, amount_field = ?,
                    amount_sign = ?, type_field = ?, credit_value = ?, description_field = ?,
                    merchant_field = ?, signature = ?
                WHERE id = ? AND user_id = ?
                """,
                [*fields, int(template_id), user_id],
            )
        else:
            conn.execute(
                """
                INSERT INTO import_templates (
                    name, account_id, date_field, date_format, amount_field, amount_sign,
                    type_field, credit_value, description_field, merchant_field, signature, user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [*fields, user_id],
            )


def delete_import_template(user_id: int, template_id: int) -> None:
    execute("DELETE FROM import_templates WHERE id = ? AND user_id = ?", [int(template_id), user_id])


def summarize_imported(user_id: int) -> Dict[str, Any]:
    """Per-account counts + totals for the import page header. Spending and
    income both exclude transfers so card payments never double-count."""
    with get_connection() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) FROM imported_transactions WHERE user_id = ?",
            [user_id],
        ).fetchone()
        accounts = rows_to_dicts(
            conn.execute(
                """
                SELECT account,
                       COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN NOT is_transfer AND amount < 0 THEN -amount ELSE 0 END), 0) AS spending,
                       COALESCE(SUM(CASE WHEN NOT is_transfer AND amount > 0 THEN amount ELSE 0 END), 0) AS income,
                       COALESCE(SUM(CASE WHEN is_transfer THEN 1 ELSE 0 END), 0) AS transfers,
                       MIN(tx_date) AS first_date,
                       MAX(tx_date) AS last_date
                FROM imported_transactions
                WHERE user_id = ?
                GROUP BY account
                ORDER BY account
                """,
                [user_id],
            )
        )

    for account in accounts:
        for key in ("first_date", "last_date"):
            if isinstance(account.get(key), datetime):
                account[key] = account[key].date()

    return {
        "total_count": int(total_row[0]) if total_row else 0,
        "accounts": accounts,
        "total_spending": sum(float(account["spending"]) for account in accounts),
        "total_income": sum(float(account["income"]) for account in accounts),
    }


# ---------------------------------------------------------------------------
# Expected transactions + reconciliation
#
# "Expected transactions" = recurring_items (recurring) + manual_transactions
# (one-time). Reconciliation links an imported (actual) transaction to an
# expected item. A confirmed link seeds an editable description substring rule
# (expected_match_rules) that auto-links matching actuals, both historical and
# on future imports. Beyond status/audit, this layer feeds the ledger one fact:
# which forecast occurrences are already settled by an actual (see
# reconciled_occurrence_dates / settled_one_time_ids) so they are not counted twice.
# ---------------------------------------------------------------------------

EXPECTED_SOURCE_TYPES = {"recurring", "one_time"}
RECONCILE_OCCURRENCE_TOLERANCE_DAYS = 10
RECONCILE_OCCURRENCE_LOOKBACK_DAYS = 400


def occurrence_history_start(item: Dict[str, Any]) -> date:
    """Where to begin generating a recurring item's occurrences when matching them
    against linked actuals. Shared by the reconcile view and the ledger so both
    walk the same occurrences and can never disagree about what is paid."""
    item_start = item["start_date"]
    if isinstance(item_start, datetime):
        item_start = item_start.date()
    return max(item_start, date.today() - timedelta(days=RECONCILE_OCCURRENCE_LOOKBACK_DAYS))


def load_all_manual_transactions(user_id: int) -> List[Dict[str, Any]]:
    """Raw one-off (manual) transactions for a user, newest first."""
    rows = query_all("""
                SELECT m.id, m.name, m.kind, m.amount, m.tx_date, m.category_id, m.account_id,
                       c.name AS category_name, c.color AS category_color
                FROM manual_transactions m
                LEFT JOIN categories c ON c.id = m.category_id AND c.user_id = m.user_id
                WHERE m.user_id = ?
                ORDER BY m.tx_date DESC, m.id DESC
                """, [user_id])
    for row in rows:
        if isinstance(row["tx_date"], datetime):
            row["tx_date"] = row["tx_date"].date()
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_expected_items(user_id: int) -> List[Dict[str, Any]]:
    """Unified expected-transaction list: recurring items + one-time items."""
    accounts = load_accounts_map(user_id)

    def account_name(account_id: Any) -> str:
        account = accounts.get(int(account_id)) if account_id else None
        return account["name"] if account else ""

    items: List[Dict[str, Any]] = []
    for item in load_all_recurring(user_id):
        items.append(
            {
                "source_type": "recurring",
                "id": int(item["id"]),
                "name": item["name"],
                "kind": item["kind"],
                "amount": float(item["amount"]),
                "category_name": item.get("category_name") or "",
                "category_color": safe_hex_color(item.get("category_color")),
                "account_name": account_name(item.get("account_id")),
                "active": bool(item["active"]),
                "cadence": summarize_frequency(item),
                "date": item["start_date"].date() if isinstance(item.get("start_date"), datetime) else item.get("start_date"),
            }
        )
    for row in load_all_manual_transactions(user_id):
        items.append(
            {
                "source_type": "one_time",
                "id": int(row["id"]),
                "name": row["name"],
                "kind": row["kind"],
                "amount": float(row["amount"]),
                "category_name": row.get("category_name") or "",
                "category_color": safe_hex_color(row.get("category_color")),
                "account_name": account_name(row.get("account_id")),
                "active": True,
                "cadence": "One-time",
                "date": row["tx_date"],
            }
        )
    return items


def load_expected_item(user_id: int, source_type: str, source_id: int) -> Optional[Dict[str, Any]]:
    if source_type == "recurring":
        item = load_recurring_item_by_id(user_id, source_id)
        if not item:
            return None
        return {
            "source_type": "recurring",
            "id": int(item["id"]),
            "name": item["name"],
            "kind": item["kind"],
            "amount": float(item["amount"]),
            "category_name": item.get("category_name") or "",
            "category_color": safe_hex_color(item.get("category_color")),
            "active": bool(item["active"]),
            "cadence": summarize_frequency(item),
            "raw": item,
        }
    if source_type == "one_time":
        row = load_manual_transaction_by_id(user_id, source_id)
        if not row:
            return None
        return {
            "source_type": "one_time",
            "id": int(row["id"]),
            "name": row["name"],
            "kind": row["kind"],
            "amount": float(row["amount"]),
            "category_name": row.get("category_name") or "",
            "category_color": safe_hex_color(row.get("category_color")),
            "active": True,
            "cadence": "One-time",
            "date": row["tx_date"],
            "raw": row,
        }
    return None


# ---------------------------------------------------------------------------
# Recurring-transaction suggestions
#
# Scan a user's imported (actual) transactions, group them by a normalized
# merchant key, and infer which groups look like real recurring items —
# regular cadence + stable amount. Each surviving group becomes a suggestion
# the user can confirm (create a recurring item on the detected account) or
# dismiss. Dismissed/added keys are remembered so they don't reappear.
# ---------------------------------------------------------------------------

_SUGGEST_STATE_RE = re.compile(r"\b[A-Z]{2}\b")

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


def _suggest_norm_key(text: Any) -> str:
    """Collapse a merchant/description to a stable grouping key (drop store #s,
    trailing city/state, ' * ' / '_' suffixes, and long digit runs)."""
    value = str(text or "").upper()
    value = re.sub(r"#\s*\d+", "", value)          # store numbers
    value = re.sub(r"\b\d{3,}\b", "", value)        # long digit runs / phone tails
    value = re.sub(r"[*_].*$", "", value)           # SQ * / TST* / FOO_BAR suffixes
    value = _SUGGEST_STATE_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or str(text or "").upper().strip()


def _suggest_clean_name(text: Any) -> str:
    """Turn a raw merchant string into a friendlier default recurring name."""
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
    return value[:40] or "Recurring item"


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
    """How many suggestions the user manually dismissed (restorable). Excludes
    'added' rows, which suppress already-confirmed items and should stay hidden."""
    row = fetch_one("SELECT COUNT(*) FROM dismissed_suggestions WHERE user_id = ? AND reason = 'dismissed'", [user_id])
    return int(row[0]) if row else 0


def reset_dismissed_suggestions(user_id: int) -> None:
    """Restore manually-dismissed suggestions. Leaves 'added' suppressions intact
    so items already turned into recurring rules are not re-suggested."""
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


def detect_recurring_suggestions(
    user_id: int,
    min_occurrences: int = 3,
    amount_cv_max: float = 0.35,
    min_confidence: float = 0.5,
    lookback_days: int = 1200,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """Infer likely recurring items from a user's imported transactions.

    A group of same-merchant transactions qualifies when it recurs at least
    `min_occurrences` times at a regular spacing (mapping to a Metis cadence)
    and its amount is reasonably stable. Transfers, dismissed/added keys, and
    groups already reconciled to a recurring item are excluded."""
    since = date.today() - timedelta(days=lookback_days)
    accounts = load_accounts(user_id)
    account_id_by_name = {a["name"]: int(a["id"]) for a in accounts}
    categories = {int(c["id"]): c for c in load_categories(user_id)}
    dismissed = load_dismissed_suggestion_keys(user_id)

    rows = query_all("""
                SELECT i.id, i.account, i.tx_date, i.description, i.merchant,
                       i.amount, i.category_id,
                       CASE WHEN r.source_type = 'recurring' THEN 1 ELSE 0 END AS recon_recurring
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
        key = _suggest_norm_key(row.get("merchant") or row.get("description"))
        if not key or key in dismissed:
            continue
        groups.setdefault(key, []).append(row)

    suggestions: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < min_occurrences:
            continue
        # Skip groups already tracked by a recurring item (majority reconciled).
        reconciled = sum(1 for r in group if r["recon_recurring"])
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
        day_of_month = int(_mode([r["tx_date"].day for r in group]) or dates[-1].day)
        interval = int(interval_months or 1)

        suggestions.append(
            {
                "key": key,
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
                "day_of_month": day_of_month,
                "semimonthly_day1": 1,
                "semimonthly_day2": 15,
                "start_date": dates[-1].isoformat(),
                "count": len(group),
                "confidence": confidence,
                "first_date": dates[0].isoformat(),
                "last_date": dates[-1].isoformat(),
            }
        )

    suggestions.sort(key=lambda s: (s["confidence"], s["amount"]), reverse=True)
    return suggestions[:limit]


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
    is left untouched (returns False)."""
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
    return True


def suggest_rule_pattern(description: str) -> str:
    """Seed an editable description-substring rule from a confirmed actual."""
    text = _import_squash(description).upper()
    if not text:
        return ""
    ach = re.search(r"\bACH\s+(.+?)\s+TYPE:", text)
    if ach:
        return ach.group(1).strip()[:40]
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
    table = "recurring_items" if source_type == "recurring" else "manual_transactions"
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


def recurring_occurrence_status(
    item: Dict[str, Any], window_start: date, window_end: date, linked_imports: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Map each generated occurrence of a recurring item to a linked actual
    within a tolerance window → paid / expected, for the reconcile view."""
    occurrences = generate_recurring_transactions(item, window_start, window_end)
    used: set = set()
    result: List[Dict[str, Any]] = []
    for occurrence in occurrences:
        occ_date = occurrence["date"]
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
                "amount": occurrence["income"] if occurrence["kind"] == "income" else occurrence["expense"],
                "paid": best is not None,
                "match": best,
            }
        )
    return result


def reconciled_occurrence_dates(
    user_id: int, items: List[Dict[str, Any]], through: date, window_end: date
) -> Dict[int, set]:
    """recurring item id -> occurrence dates already settled by a linked actual
    dated on/before `through` (the checking actuals cutover).

    A bill paid early straddles the cutover: the actual lands in the actuals half
    of the ledger while its scheduled occurrence still falls in the forecast half,
    so the ledger would count the same money twice. Matching mirrors
    recurring_occurrence_status, so the occurrence the reconcile page calls paid is
    exactly the one dropped from the forecast."""
    linked_by_item = load_reconciliations_by_item(user_id, "recurring")
    if not linked_by_item:
        return {}

    settled: Dict[int, set] = {}
    for item in items:
        item_id = int(item["id"])
        linked = linked_by_item.get(item_id)
        if not linked:
            continue
        dates = {
            occurrence["date"]
            for occurrence in recurring_occurrence_status(
                item, occurrence_history_start(item), window_end, linked
            )
            if occurrence["paid"] and occurrence["match"]["tx_date"] <= through
        }
        if dates:
            settled[item_id] = dates
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
    rows = query_all("""
                SELECT i.id, i.tx_date, i.description, i.merchant, i.amount, i.account,
                       i.is_transfer, i.category_id, i.category_via,
                       COALESCE(ec.name, ic.name) AS category_name,
                       COALESCE(ec.color, ic.color) AS category_color,
                       COALESCE(ri.name, mt.name) AS expected_name
                FROM imported_transactions i
                LEFT JOIN expected_reconciliations r
                       ON r.imported_transaction_id = i.id AND r.user_id = i.user_id
                LEFT JOIN recurring_items ri
                       ON r.source_type = 'recurring' AND ri.id = r.source_id AND ri.user_id = i.user_id
                LEFT JOIN manual_transactions mt
                       ON r.source_type = 'one_time' AND mt.id = r.source_id AND mt.user_id = i.user_id
                LEFT JOIN categories ec
                       ON ec.id = COALESCE(ri.category_id, mt.category_id) AND ec.user_id = i.user_id
                LEFT JOIN categories ic
                       ON ic.id = i.category_id AND ic.user_id = i.user_id
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
    rows = query_all("""
                SELECT ts.id, ts.category_id, ts.amount, ts.note,
                       c.name AS category_name, c.color AS category_color
                FROM transaction_splits ts
                JOIN categories c ON c.id = ts.category_id AND c.user_id = ts.user_id
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
# Name-match rules that categorize imported transactions directly, with no
# budget or recurring item behind them — for the things that never follow a
# schedule (card payments, ATM withdrawals, one-off spend you still want named).
#
# A rule only ever writes rows it owns (category_via = 'rule'), so a category you
# picked by hand is never overwritten. Transfers are eligible on purpose: card
# payments are transfers, and the budget aggregation already excludes transfers,
# so naming one is presentational and can't distort budget totals.
# ---------------------------------------------------------------------------


def load_category_rules(user_id: int) -> List[Dict[str, Any]]:
    """All category rules with their category, newest first."""
    return query_all("""
                SELECT cr.id, cr.category_id, cr.pattern, cr.amount_min, cr.amount_max,
                       c.name AS category_name, c.color AS category_color
                FROM category_rules cr
                JOIN categories c ON c.id = cr.category_id AND c.user_id = cr.user_id
                WHERE cr.user_id = ?
                ORDER BY cr.created_at DESC, cr.id DESC
                """, [user_id])


def create_category_rule(
    user_id: int,
    category_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
) -> int:
    """Create a rule and apply it immediately. Returns the new rule id."""
    cleaned = (pattern or "").strip()
    if not cleaned:
        raise ValueError("A match pattern is required")
    if not load_category_by_id(user_id, category_id):
        raise ValueError("Category not found")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO category_rules (user_id, category_id, pattern, amount_min, amount_max)
            VALUES (?, ?, ?, ?, ?)
            """,
            [user_id, category_id, cleaned, amount_min, amount_max],
        )
        rule_id = int(conn.execute(
            "SELECT MAX(id) FROM category_rules WHERE user_id = ?", [user_id]
        ).fetchone()[0])
    apply_category_rules(user_id, rule_ids=[rule_id])
    return rule_id


def update_category_rule(
    user_id: int,
    rule_id: int,
    category_id: int,
    pattern: str,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
) -> None:
    """Edit a rule, then rebuild every rule-owned category so rows the old
    pattern claimed are released and the new one is applied."""
    cleaned = (pattern or "").strip()
    if not cleaned:
        raise ValueError("A match pattern is required")
    if not load_category_by_id(user_id, category_id):
        raise ValueError("Category not found")

    execute(
        """
        UPDATE category_rules SET category_id = ?, pattern = ?, amount_min = ?, amount_max = ?
        WHERE id = ? AND user_id = ?
        """,
        [category_id, cleaned, amount_min, amount_max, rule_id, user_id],
    )
    resync_category_rules(user_id)


def delete_category_rule(user_id: int, rule_id: int) -> None:
    execute("DELETE FROM category_rules WHERE id = ? AND user_id = ?", [rule_id, user_id])
    resync_category_rules(user_id)


def apply_category_rules(
    user_id: int, rule_ids: Optional[List[int]] = None, import_ids: Optional[List[int]] = None
) -> int:
    """Categorize imported rows whose description contains a rule pattern. Runs
    retroactively (rule create/edit) and on new imports (pass import_ids). Rows
    you categorized by hand are left alone. Returns the number of rows changed."""
    categorized = 0
    with get_connection() as conn:
        rule_columns = "id, category_id, pattern, amount_min, amount_max"
        if rule_ids is not None:
            if not rule_ids:
                return 0
            placeholders = ", ".join("?" for _ in rule_ids)
            rules = conn.execute(
                f"SELECT {rule_columns} FROM category_rules WHERE user_id = ? AND id IN ({placeholders})",
                [user_id, *rule_ids],
            ).fetchall()
        else:
            rules = conn.execute(
                f"SELECT {rule_columns} FROM category_rules WHERE user_id = ? ORDER BY id",
                [user_id],
            ).fetchall()

        for _rule_id, category_id, pattern, amount_min, amount_max in rules:
            if not pattern.strip():
                continue
            where_params: List[Any] = [user_id, f"%{pattern.upper()}%"]
            # Optional amount constraint, compared against the magnitude. A small
            # epsilon absorbs float rounding so an "exact" bound matches cleanly.
            amount_filter = ""
            if amount_min is not None:
                amount_filter += " AND ABS(amount) >= ?"
                where_params.append(float(amount_min) - 0.005)
            if amount_max is not None:
                amount_filter += " AND ABS(amount) <= ?"
                where_params.append(float(amount_max) + 0.005)
            import_filter = ""
            if import_ids is not None:
                if not import_ids:
                    continue
                placeholders = ", ".join("?" for _ in import_ids)
                import_filter = f" AND id IN ({placeholders})"
                where_params.extend(import_ids)

            where_sql = f"""
                WHERE user_id = ?
                  AND UPPER(description) LIKE ?
                  AND (category_id IS NULL OR category_via = 'rule')
                  {amount_filter}
                  {import_filter}
                """
            # Count only rows this rule actually moves, so re-running is honest.
            changed = conn.execute(
                f"SELECT COUNT(*) FROM imported_transactions {where_sql} "
                f"AND category_id IS DISTINCT FROM ?",
                [*where_params, int(category_id)],
            ).fetchone()[0]
            conn.execute(
                f"UPDATE imported_transactions SET category_id = ?, category_via = 'rule' {where_sql}",
                [int(category_id), *where_params],
            )
            categorized += int(changed)
    return categorized


def resync_category_rules(user_id: int) -> int:
    """Release every rule-owned category, then re-apply all rules from scratch.
    Hand-picked categories (category_via IS NULL) survive untouched."""
    execute(
        "UPDATE imported_transactions SET category_id = NULL, category_via = NULL "
        "WHERE user_id = ? AND category_via = 'rule'",
        [user_id],
    )
    return apply_category_rules(user_id)


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

