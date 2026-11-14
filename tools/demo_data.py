"""A synthetic database that fills every screen, for the visual check and for
trying Metis without real data.

    python -m tools.demo_data PATH [--password PASSWORD]

Every name, merchant and amount here is invented. Dates are relative to
today, so the default date range always has actuals and a forecast.
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEMO_PASSWORD = "synthetic-demo-password"


def _months_back(today: date, months: int) -> date:
    index = today.year * 12 + today.month - 1 - months
    return date(index // 12, index % 12 + 1, 1)


def _row(account: Dict[str, Any], when: date, description: str, amount: float, transfer: bool = False) -> Dict[str, Any]:
    return {
        "tx_date": when, "description": description, "merchant": description, "amount": amount,
        "flow": "transfer" if transfer else ("income" if amount > 0 else "expense"), "is_transfer": transfer,
        "raw_type": "", "account": account["name"], "account_id": account["id"],
    }


def build(path: str, today: Optional[date] = None, password: str = DEMO_PASSWORD) -> Dict[str, Any]:
    """Make a fresh database at path. Returns the user's id and slug."""
    os.environ["FINANCE_DB_PATH"] = path
    import services as db  # reads FINANCE_DB_PATH when imported
    import import_templates as it

    db.DB_PATH = path
    today = today or date.today()
    db.init_db()
    slug = db.create_user_record("Demo Person", "demo@example.com")
    user = db.load_user_by_slug(slug)
    user_id = int(user["id"])
    db.set_user_password(user_id, password)
    db.set_setting_float(user_id, "starting_balance", 2500.0)

    db.execute("INSERT INTO accounts (user_id, name, account_type) VALUES (?, 'Example Checking', 'checking')", [user_id])
    db.execute("INSERT INTO accounts (user_id, name, account_type, statement_day, due_day, payment_pattern) "
               "VALUES (?, 'Example Card', 'credit_card', 5, 25, 'EXAMPLE CARD PAYMENT')", [user_id])
    accounts = {a["name"]: a for a in db.load_accounts(user_id)}
    checking, card = accounts["Example Checking"], accounts["Example Card"]

    housing = db.create_category(user_id, "Housing", "#3b82f6")
    utilities = db.create_category(user_id, "Utilities", parent_id=housing)
    food = db.create_category(user_id, "Food", "#22c55e")
    groceries = db.create_category(user_id, "Groceries", parent_id=food)
    dining = db.create_category(user_id, "Dining", parent_id=food)
    transport = db.create_category(user_id, "Transport", "#f97316")
    fun = db.create_category(user_id, "Entertainment and subscriptions", "#a855f7")
    db.create_category(user_id, "Gifts", "#ef4444")

    first = _months_back(today, 3)
    rows: List[Dict[str, Any]] = []
    month = first
    while month <= today:
        def on(day: int) -> date:
            return month.replace(day=min(day, 28))
        for when, description, amount, account in (
            (on(1), "ACME PROPERTY RENT", -1200.0, checking),
            (on(3), "EXAMPLE EMPLOYER PAYROLL", 2400.0, checking),
            (on(17), "EXAMPLE EMPLOYER PAYROLL", 2400.0, checking),
            (on(8), "ACME POWER CO", -84.5, checking),
            (on(12), "ACME WATER UTILITY", -38.0, checking),
            (on(4), "ACME GROCER #12", -92.4, card),
            (on(11), "ACME GROCER #12", -118.75, card),
            (on(19), "ACME GROCER #40", -76.2, card),
            (on(9), "EXAMPLE DINER", -42.0, card),
            (on(21), "EXAMPLE NOODLE HOUSE", -27.5, card),
            (on(14), "ACME FUEL 0042", -48.0, card),
            (on(15), "EXAMPLE STREAMING SERVICE", -15.99, card),
            (on(23), "ACME HARDWARE", -64.3, card),
            (on(26), "EXAMPLE CARD PAYMENT", -650.0, checking),
            (on(26), "PAYMENT THANK YOU", 650.0, card),
            (on(27), "TRANSFER TO SAVINGS 7731", -200.0, checking),
        ):
            if when <= today:
                rows.append(_row(account, when, description, amount,
                                 transfer=description in ("EXAMPLE CARD PAYMENT", "PAYMENT THANK YOU")))
        month = _months_back(month, -1)
    rows.append(_row(card, today - timedelta(days=6), "EXAMPLE BOOKSHOP WITH A VERY LONG NAME THAT WRAPS", -31.0))
    rows.append(_row(card, today - timedelta(days=4), "ACME GIFT SHOP", -55.0))
    rows.append(_row(checking, today - timedelta(days=2), "ACME CAR WASH", -18.0))
    it.import_rows(user_id, rows, "demo.csv")

    rent = _bill(db, user_id, "Rent", 1200.0, first, housing, checking["id"])
    _bill(db, user_id, "Paycheck", 2400.0, first.replace(day=3), None, checking["id"], kind="income",
          frequency="semimonthly")
    power = _bill(db, user_id, "Power", 85.0, first.replace(day=8), utilities, checking["id"])
    _bill(db, user_id, "Water", 40.0, first.replace(day=12), utilities, checking["id"])
    _bill(db, user_id, "Streaming", 15.99, first.replace(day=15), fun, card["id"])
    _bill(db, user_id, "Groceries", 100.0, first.replace(day=4), groceries, card["id"], frequency="weekly")
    db.execute("INSERT INTO one_time_items (name, kind, amount, expected_date, category_id, account_id, user_id) "
               "VALUES ('Car service', 'expense', 180.0, ?, ?, ?, ?)",
               [today + timedelta(days=12), transport, checking["id"], user_id])
    db.execute("INSERT INTO one_time_items (name, kind, amount, expected_date, category_id, account_id, user_id) "
               "VALUES ('Birthday gift', 'expense', 60.0, ?, NULL, ?, ?)",
               [today + timedelta(days=20), card["id"], user_id])

    db.create_match_rule(user_id, "bill", rent, "ACME PROPERTY")
    db.create_match_rule(user_id, "bill", power, "ACME POWER")
    db.create_category_rule(user_id, dining, "EXAMPLE DINER", match_on="merchant")
    db.create_category_rule(user_id, transport, "ACME FUEL")
    db.create_category_rule(user_id, groceries, "ACME GROCER")
    return {"user_id": user_id, "slug": slug, "password": password}


def _bill(db, user_id: int, name: str, amount: float, first_due: date, category_id: Optional[int],
          account_id: Optional[int], kind: str = "expense", frequency: str = "monthly") -> int:
    row = db.fetch_one(
        "INSERT INTO bills (name, kind, amount, first_due_date, frequency_type, interval_months, semimonthly_day1, "
        "semimonthly_day2, category_id, account_id, user_id, active) VALUES (?, ?, ?, ?, ?, 1, 3, 17, ?, ?, ?, TRUE) "
        "RETURNING id",
        [name, kind, amount, first_due, frequency, category_id, account_id, user_id],
    )
    return int(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--password", default=DEMO_PASSWORD)
    args = parser.parse_args()
    if os.path.exists(args.path):
        sys.exit(f"{args.path} already exists")
    made = build(args.path, password=args.password)
    print(f"made {args.path}: sign in as {made['slug']}")


if __name__ == "__main__":
    main()
