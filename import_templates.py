"""Import templates: how one CSV layout maps to Metis's transaction fields.

The user makes every template by mapping a sample of their own file. Metis never
guesses a format, an account or an account type from a file's shape. A template
stands on its own; any number of accounts can use it, and an account may name
one as its default.

Amounts come in one of three layouts:

- signed: one column, negative = money out (or the reverse, with "flip signs");
- split:  a money-in column and a money-out column;
- typed:  one amount column, and a type column whose named value means money in.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import services as db
from services import CsvImportError, _CARD_PAYMENT_RE, _import_squash, _matches_any

AMOUNT_LAYOUTS = ("signed", "split", "typed")
AMOUNT_SIGNS = ("standard", "inverted")

# Tried in this order when reading a sample, so an ambiguous day/month reads
# month first.
DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y",
    "%Y/%m/%d", "%m-%d-%Y", "%d.%m.%Y", "%b %d, %Y", "%d %b %Y",
)
DATE_FORMAT_EXAMPLE = date(2026, 11, 13)

TEXT_FIELDS = (
    "name", "date_field", "date_fallback_field", "date_format", "description_field",
    "merchant_field", "type_field", "amount_field", "amount_in_field", "amount_out_field",
    "credit_value",
)
COLUMNS = (
    "id, name, created_at, date_field, date_fallback_field, date_format, description_field, "
    "merchant_field, type_field, amount_field, amount_sign, amount_in_field, amount_out_field, credit_value"
)
UPLOAD_TTL = timedelta(days=1)
SAMPLE_ROWS = 5


# --- Reading a file ---------------------------------------------------------

def read_csv(raw: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    """The column names and rows of an uploaded CSV."""
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text or not text.strip():
        raise CsvImportError("The file is empty.")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise CsvImportError("The file is not a CSV.")
    header = [str(name or "").strip() for name in reader.fieldnames]
    reader.fieldnames = header
    rows = [{key: (value or "") for key, value in row.items() if key} for row in reader]
    return header, rows


def date_format_choices() -> List[Tuple[str, str]]:
    """(format, how 2026-11-13 looks in it), for the date format picker."""
    return [(fmt, DATE_FORMAT_EXAMPLE.strftime(fmt)) for fmt in DATE_FORMATS]


def _parses(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value.strip(), fmt)
        return True
    except ValueError:
        return False


def detect_date_format(values: Sequence[str]) -> Optional[str]:
    """The first known format every non-blank sample value reads in."""
    sample = [value for value in values if str(value).strip()]
    if not sample:
        return None
    for fmt in DATE_FORMATS:
        if all(_parses(value, fmt) for value in sample):
            return fmt
    return None


SUGGESTIONS = {
    "date_field": "date",
    "description_field": "description",
    "merchant_field": "merchant",
    "type_field": "type",
    "amount_field": "amount",
}


def suggest_mapping(header: Sequence[str]) -> Dict[str, str]:
    """A starting mapping: for each field, the first column whose name contains
    the field's name. The owner checks it against the preview before saving."""
    found = {}
    for field, word in SUGGESTIONS.items():
        for column in header:
            if word in column.lower():
                found[field] = column
                break
    return found


# --- Building and checking a template ---------------------------------------

def layout_of(template: Mapping[str, Any]) -> str:
    if template.get("amount_in_field") or template.get("amount_out_field"):
        return "split"
    if template.get("credit_value"):
        return "typed"
    return "signed"


def build_template(fields: Mapping[str, Any]) -> Dict[str, Any]:
    """A complete, checked template from form fields. Raises ValueError naming
    what is missing. Only the chosen amount layout's columns are kept."""
    t: Dict[str, Any] = {key: (str(fields.get(key) or "").strip() or None) for key in TEXT_FIELDS}
    layout = str(fields.get("amount_layout") or "signed")
    sign = str(fields.get("amount_sign") or "standard")
    if layout not in AMOUNT_LAYOUTS:
        raise ValueError("Choose how the file shows amounts")
    if sign not in AMOUNT_SIGNS:
        raise ValueError("Choose how the file signs amounts")
    if not t["name"]:
        raise ValueError("Give the template a name")
    if not t["date_field"]:
        raise ValueError("Choose the date column")
    if not t["description_field"]:
        raise ValueError("Choose the description column")
    if t["date_format"] and t["date_format"] not in DATE_FORMATS:
        raise ValueError("Choose a date format from the list")

    if layout == "split":
        if not (t["amount_in_field"] or t["amount_out_field"]):
            raise ValueError("Choose the money in or money out column")
        t["amount_field"] = t["credit_value"] = None
        sign = "standard"
    else:
        if not t["amount_field"]:
            raise ValueError("Choose the amount column")
        t["amount_in_field"] = t["amount_out_field"] = None
    if layout == "typed":
        if not t["type_field"]:
            raise ValueError("Choose the type column")
        if not t["credit_value"]:
            raise ValueError("Type the value that means money in")
        sign = "standard"
    elif layout == "signed":
        t["credit_value"] = None
    t["amount_sign"] = sign
    t["amount_layout"] = layout
    return t


def template_columns(template: Mapping[str, Any]) -> List[str]:
    """Every column the template reads, in form order."""
    keys = ("date_field", "date_fallback_field", "description_field", "merchant_field", "type_field",
            "amount_field", "amount_in_field", "amount_out_field")
    return [template[key] for key in keys if template.get(key)]


def missing_columns(header: Sequence[str], template: Mapping[str, Any]) -> List[str]:
    present = set(header)
    return [column for column in template_columns(template) if column not in present]


# --- Parsing rows -------------------------------------------------------------

_ACH_PAYEE = (
    re.compile(r"\bACH\s+(.+?)\s+TYPE:", re.IGNORECASE),
    re.compile(r"\bACH\s+(.+?)(?:\s+ID:|\s+CO:|$)", re.IGNORECASE),
)


def derive_merchant(description: str) -> str:
    """The payee named in a bank transfer description, else the description."""
    for pattern in _ACH_PAYEE:
        match = pattern.search(description)
        if match:
            return match.group(1).strip()
    return description


def parse_amount(value: Any) -> float:
    cleaned = re.sub(r"[\s$,]|USD", "", str(value or ""))
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if cleaned in ("", "-"):
        return 0.0
    try:
        amount = float(cleaned)
    except ValueError as exc:
        raise CsvImportError(f"“{value}” is not an amount") from exc
    return -abs(amount) if negative else amount


def parse_date(value: str, date_format: Optional[str]) -> date:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise CsvImportError("no date")
    for fmt in ([date_format] if date_format else DATE_FORMATS):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise CsvImportError(f"“{cleaned}” is not a date in the chosen format")


def _direction(row: Mapping[str, str], t: Mapping[str, Any], raw_type: str) -> Tuple[bool, float]:
    """(is money in, size) for one row."""
    layout = layout_of(t)
    if layout == "split":
        inflow = abs(parse_amount(row.get(t["amount_in_field"]))) if t.get("amount_in_field") else 0.0
        outflow = abs(parse_amount(row.get(t["amount_out_field"]))) if t.get("amount_out_field") else 0.0
        return (True, inflow) if inflow > 0 else (False, outflow)
    raw = parse_amount(row.get(t["amount_field"]))
    if layout == "typed":
        return raw_type.lower() == str(t["credit_value"]).strip().lower(), abs(raw)
    if t.get("amount_sign") == "inverted":
        raw = -raw
    return raw > 0, abs(raw)


def parse_row(row: Mapping[str, str], t: Mapping[str, Any], account: Mapping[str, Any],
              transfer_patterns: Sequence[str] = ()) -> Dict[str, Any]:
    description = _import_squash(row.get(t["description_field"]))
    merchant_field = t.get("merchant_field")
    merchant = _import_squash(row.get(merchant_field)) if merchant_field else ""
    date_text = str(row.get(t["date_field"]) or "").strip()
    if not date_text and t.get("date_fallback_field"):
        date_text = str(row.get(t["date_fallback_field"]) or "").strip()
    raw_type = str(row.get(t["type_field"]) or "").strip() if t.get("type_field") else ""
    is_credit, size = _direction(row, t, raw_type)

    on_card = account.get("account_type") == "credit_card"
    if is_credit:
        # Money in is positive whatever it is called. A payment to a card is only
        # singled out so it is not counted as a refund.
        is_payment = on_card and bool(_CARD_PAYMENT_RE.search(description) or _CARD_PAYMENT_RE.search(raw_type))
        amount, is_transfer = size, is_payment
        flow = "transfer" if is_payment else ("refund" if on_card else "income")
    else:
        pays_card = not on_card and _matches_any(description, transfer_patterns)
        amount, is_transfer = -size, pays_card
        flow = "transfer" if pays_card else "expense"

    return {
        "account": account["name"],
        "account_id": int(account["id"]),
        "tx_date": parse_date(date_text, t.get("date_format")),
        "description": description,
        "merchant": merchant or derive_merchant(description),
        "amount": amount,
        "flow": flow,
        "is_transfer": is_transfer,
        "raw_type": raw_type,
    }


def parse_rows(header: Sequence[str], rows: Sequence[Mapping[str, str]], t: Mapping[str, Any],
               account: Mapping[str, Any], transfer_patterns: Sequence[str] = ()) -> List[Dict[str, Any]]:
    missing = missing_columns(header, t)
    if missing:
        raise CsvImportError("This file has no column called " + ", ".join(f"“{c}”" for c in missing) + ".")
    parsed = []
    for index, row in enumerate(rows):
        if not any(str(value).strip() for value in row.values()):
            continue
        try:
            parsed.append(parse_row(row, t, account, transfer_patterns))
        except CsvImportError as exc:
            raise CsvImportError(f"Row {index + 2}: {exc}.") from exc
    return parsed


# --- Saved templates ------------------------------------------------------------

def _from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    created = row.pop("created_at")
    row["created"] = (created.date() if isinstance(created, datetime) else created).isoformat()
    row["amount_layout"] = layout_of(row)
    return row


def load_templates(user_id: int) -> List[Dict[str, Any]]:
    """The user's templates by name, each with its created date and the accounts
    using it as their default."""
    templates = [_from_row(row) for row in db.query_all(
        f"SELECT {COLUMNS} FROM import_templates WHERE user_id = ? ORDER BY LOWER(name), id", [user_id])]
    used: Dict[int, List[str]] = {}
    for template_id, name in db.fetch_all(
            "SELECT default_template_id, name FROM accounts WHERE user_id = ? AND default_template_id IS NOT NULL "
            "ORDER BY LOWER(name)", [user_id]):
        used.setdefault(int(template_id), []).append(name)
    for template in templates:
        template["used_by"] = used.get(int(template["id"]), [])
    return templates


def load_template(user_id: int, template_id: Any) -> Optional[Dict[str, Any]]:
    try:
        template_id = int(template_id)
    except (TypeError, ValueError):
        return None
    rows = db.query_all(f"SELECT {COLUMNS} FROM import_templates WHERE user_id = ? AND id = ?", [user_id, template_id])
    return _from_row(rows[0]) if rows else None


def save_template(user_id: int, t: Mapping[str, Any]) -> int:
    """Save a template made by build_template. Names are unique per user."""
    if db.fetch_one("SELECT 1 FROM import_templates WHERE user_id = ? AND LOWER(name) = LOWER(?)",
                    [user_id, t["name"]]):
        raise ValueError(f"A template called “{t['name']}” already exists")
    keys = [key for key in TEXT_FIELDS] + ["amount_sign"]
    row = db.fetch_one(
        f"INSERT INTO import_templates (user_id, {', '.join(keys)}) "
        f"VALUES (?, {', '.join('?' for _ in keys)}) RETURNING id",
        [user_id, *[t.get(key) for key in keys]],
    )
    return int(row[0])


def delete_template(user_id: int, template_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("UPDATE accounts SET default_template_id = NULL WHERE user_id = ? AND default_template_id = ?",
                     [user_id, template_id])
        conn.execute("DELETE FROM import_templates WHERE user_id = ? AND id = ?", [user_id, template_id])


def set_default_template(user_id: int, account_id: int, template_id: Optional[int]) -> None:
    if not db.load_account_by_id(user_id, account_id):
        raise ValueError("Account not found")
    if template_id is not None and not load_template(user_id, template_id):
        raise ValueError("Template not found")
    db.execute("UPDATE accounts SET default_template_id = ? WHERE user_id = ? AND id = ?",
               [template_id, user_id, account_id])


# --- Importing ------------------------------------------------------------------

def import_into_account(user_id: int, account: Mapping[str, Any], t: Mapping[str, Any],
                        raw: bytes, filename: str) -> Dict[str, int]:
    """Read a file with a template into one account, then settle the new rows the
    way every import does: match rules, Review's merchant decisions, then
    category rules. Raises CsvImportError when the file does not fit."""
    from review import apply_review_decisions  # review imports services; keep this module light

    header, rows = read_csv(raw)
    parsed = parse_rows(header, rows, t, account, db.card_payment_patterns(user_id))
    result = db.upsert_imported_transactions(user_id, parsed, filename)
    result["matched"] = result["categorized"] = 0
    if result["inserted"]:
        result["matched"] = db.apply_match_rules(user_id)
        # Decisions first, so they win over rules for their rows.
        result["categorized"] = apply_review_decisions(user_id) + db.apply_category_rules(user_id)
    return result


def import_message(account_name: str, result: Mapping[str, int]) -> str:
    message = f"{account_name}: {result['inserted']} new, {result['skipped']} already imported"
    if result.get("categorized"):
        message += f", {result['categorized']} categorized"
    return message


# --- Files waiting for their template ---------------------------------------------

def save_upload(user_id: int, filename: str, raw: bytes) -> int:
    """Hold an uploaded file while its template is mapped. Old ones are dropped."""
    db.execute("DELETE FROM import_uploads WHERE created_at < ?", [datetime.now() - UPLOAD_TTL])
    row = db.fetch_one("INSERT INTO import_uploads (user_id, filename, content) VALUES (?, ?, ?) RETURNING id",
                       [user_id, filename, raw])
    return int(row[0])


def load_upload(user_id: int, upload_id: Any) -> Optional[Tuple[str, bytes]]:
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return None
    row = db.fetch_one("SELECT filename, content FROM import_uploads WHERE user_id = ? AND id = ?",
                       [user_id, upload_id])
    return (row[0], bytes(row[1])) if row else None


def delete_upload(user_id: int, upload_id: int) -> None:
    db.execute("DELETE FROM import_uploads WHERE user_id = ? AND id = ?", [user_id, upload_id])
