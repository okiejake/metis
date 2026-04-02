import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import duckdb
from fastapi import Request
from fastapi.responses import RedirectResponse, Response

DB_PATH = os.getenv("FINANCE_DB_PATH", "finance.duckdb")
VALID_KINDS = {"income", "expense"}
VALID_FREQUENCIES = {"biweekly", "semimonthly", "monthly", "every_x_months"}
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
            INSERT INTO settings (key, value)
            VALUES ('starting_balance', '0')
            ON CONFLICT DO NOTHING
            """
        )

        if not table_has_column(conn, "recurring_items", "category_id"):
            conn.execute("ALTER TABLE recurring_items ADD COLUMN category_id BIGINT")
        if not table_has_column(conn, "manual_transactions", "category_id"):
            conn.execute("ALTER TABLE manual_transactions ADD COLUMN category_id BIGINT")
        if not table_has_column(conn, "categories", "color"):
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT")
        if not table_has_column(conn, "categories", "user_id"):
            conn.execute("ALTER TABLE categories ADD COLUMN user_id BIGINT")
        if not table_has_column(conn, "recurring_items", "user_id"):
            conn.execute("ALTER TABLE recurring_items ADD COLUMN user_id BIGINT")
        if not table_has_column(conn, "manual_transactions", "user_id"):
            conn.execute("ALTER TABLE manual_transactions ADD COLUMN user_id BIGINT")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_user_id ON categories(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_items_user_id ON recurring_items(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_transactions_user_id ON manual_transactions(user_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_user_name_unique ON categories(user_id, name)")

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
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, slug, display_name, email
            FROM users
            ORDER BY LOWER(display_name), id
            """
        )
        return rows_to_dicts(cursor)


def load_user_by_slug(user_slug: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, slug, display_name, email
            FROM users
            WHERE slug = ?
            LIMIT 1
            """,
            [user_slug],
        ).fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "slug": row[1], "display_name": row[2], "email": row[3] or ""}


def load_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, slug, display_name, email
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            [user_id],
        ).fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "slug": row[1], "display_name": row[2], "email": row[3] or ""}


def get_default_user() -> Dict[str, Any]:
    default_user = load_user_by_slug(DEFAULT_USER_SLUG)
    if default_user:
        return default_user
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, slug, display_name, email FROM users ORDER BY id LIMIT 1"
        ).fetchone()
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
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", [storage_key]).fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def set_setting_value(user_id: int, key: str, value: str) -> None:
    storage_key = build_setting_storage_key(user_id, key)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            [storage_key, value],
        )


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
    default_start = saved_start or today
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
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, name, color
            FROM categories
            WHERE user_id = ?
            ORDER BY LOWER(name), id
            """,
            [user_id],
        )
        rows = rows_to_dicts(cursor)
    for row in rows:
        row["color"] = safe_hex_color(row.get("color"))
    return rows


def load_categories_with_usage(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT c.id, c.name, c.color,
                   (SELECT COUNT(*) FROM recurring_items r WHERE r.category_id = c.id AND r.user_id = c.user_id) AS recurring_count,
                   (SELECT COUNT(*) FROM manual_transactions m WHERE m.category_id = c.id AND m.user_id = c.user_id) AS manual_count
            FROM categories c
            WHERE c.user_id = ?
            ORDER BY LOWER(c.name), c.id
            """,
            [user_id],
        )
        rows = rows_to_dicts(cursor)
    for row in rows:
        row["color"] = safe_hex_color(row.get("color"))
    return rows


def load_category_by_id(user_id: int, category_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, color
            FROM categories
            WHERE id = ? AND user_id = ?
            """,
            [category_id, user_id],
        ).fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "name": row[1], "color": safe_hex_color(row[2])}


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


def iter_biweekly(start_anchor: date, window_start: date, window_end: date):
    if window_end < start_anchor:
        return

    if window_start <= start_anchor:
        current = start_anchor
    else:
        days_since_start = (window_start - start_anchor).days
        jumps = (days_since_start + 13) // 14
        current = start_anchor + timedelta(days=14 * jumps)

    while current <= window_end:
        yield current
        current += timedelta(days=14)


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

    if frequency == "biweekly":
        occurrences = iter_biweekly(item_start, effective_start, effective_end)
    elif frequency == "semimonthly":
        day1 = int(item.get("semimonthly_day1") or 1)
        day2 = int(item.get("semimonthly_day2") or 15)
        occurrences = iter_semimonthly(item_start, effective_start, effective_end, day1, day2)
    elif frequency == "monthly":
        day = int(item.get("day_of_month") or item_start.day)
        occurrences = iter_monthly(item_start, effective_start, effective_end, 1, day)
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
            }
        )

    return rows


def load_all_recurring(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.created_at, r.category_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.user_id = ?
            ORDER BY r.active DESC, r.kind, r.name, r.id
            """,
            [user_id],
        )
        rows = rows_to_dicts(cursor)
    for row in rows:
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_active_recurring(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.created_at, r.category_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.active = TRUE AND r.user_id = ?
            ORDER BY r.kind, r.name, r.id
            """,
            [user_id],
        )
        rows = rows_to_dicts(cursor)
    for row in rows:
        row["category_color"] = safe_hex_color(row.get("category_color"))
    return rows


def load_manual_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT m.id, m.name, m.kind, m.amount, m.tx_date, m.category_id, c.name AS category_name, c.color AS category_color
            FROM manual_transactions m
            LEFT JOIN categories c ON c.id = m.category_id AND c.user_id = m.user_id
            WHERE m.user_id = ? AND m.tx_date >= ? AND m.tx_date <= ?
            ORDER BY m.tx_date, m.id
            """,
            [user_id, window_start, window_end],
        )
        rows = rows_to_dicts(cursor)

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
            }
        )
    return transactions


def load_manual_transaction_by_id(user_id: int, tx_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT m.id, m.name, m.kind, m.amount, m.tx_date, m.category_id, c.name AS category_name, c.color AS category_color
            FROM manual_transactions m
            LEFT JOIN categories c ON c.id = m.category_id AND c.user_id = m.user_id
            WHERE m.id = ? AND m.user_id = ?
            """,
            [tx_id, user_id],
        ).fetchone()

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
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT r.id, r.name, r.kind, r.amount, r.start_date, r.end_date, r.frequency_type,
                   r.interval_months, r.semimonthly_day1, r.semimonthly_day2, r.day_of_month,
                   r.active, r.category_id, c.name AS category_name, c.color AS category_color
            FROM recurring_items r
            LEFT JOIN categories c ON c.id = r.category_id AND c.user_id = r.user_id
            WHERE r.id = ? AND r.user_id = ?
            """,
            [item_id, user_id],
        )
        rows = rows_to_dicts(cursor)
    if not rows:
        return None
    rows[0]["category_color"] = safe_hex_color(rows[0].get("category_color"))
    return rows[0]


def load_amount_overrides(recurring_item_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, recurring_item_id, effective_date, amount FROM amount_overrides "
            "WHERE recurring_item_id = ? ORDER BY effective_date ASC",
            [recurring_item_id],
        )
        return rows_to_dicts(cursor)


def load_amount_overrides_for_items(item_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not item_ids:
        return {}
    placeholders = ", ".join("?" for _ in item_ids)
    with get_connection() as conn:
        cursor = conn.execute(
            f"SELECT id, recurring_item_id, effective_date, amount FROM amount_overrides "
            f"WHERE recurring_item_id IN ({placeholders}) ORDER BY effective_date ASC",
            item_ids,
        )
        rows = rows_to_dicts(cursor)
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
    with get_connection() as conn:
        conn.execute("DELETE FROM amount_overrides WHERE recurring_item_id = ?", [recurring_item_id])


def summarize_frequency(item: Dict[str, Any]) -> str:
    frequency = item["frequency_type"]
    if frequency == "biweekly":
        return "Every 2 weeks"
    if frequency == "semimonthly":
        return f"Twice monthly ({item.get('semimonthly_day1') or 1}, {item.get('semimonthly_day2') or 15})"
    if frequency == "monthly":
        return f"Monthly (day {item.get('day_of_month') or item['start_date'].day})"
    interval = item.get("interval_months") or 1
    return f"Every {interval} month(s) (day {item.get('day_of_month') or item['start_date'].day})"


def collect_window_transactions(user_id: int, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    transactions: List[Dict[str, Any]] = []
    active_items = load_active_recurring(user_id)
    item_ids = [int(item["id"]) for item in active_items]
    overrides_by_id = load_amount_overrides_for_items(item_ids)
    for item in active_items:
        item_overrides = overrides_by_id.get(int(item["id"]), [])
        transactions.extend(generate_recurring_transactions(item, window_start, window_end, overrides=item_overrides))
    transactions.extend(load_manual_transactions(user_id, window_start, window_end))

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

