"""Bringing a database older than v0.4.0 to the v0.4.0 shape.

These are the steps earlier versions ran at every start, unchanged. They are
kept so an old database can still be upgraded: they run first, and then the
named steps in migrations.py take the v0.4.0 shape to the current one.

The old table names (recurring_items, manual_transactions, review_actions,
merchant_decisions) live on here and in migrations.py for that purpose only.
Nothing here runs on a database that is already past v0.4.0.
"""

import duckdb


def bring_to_v0_4_0(conn: duckdb.DuckDBPyConnection) -> None:
    import services as db  # services imports this module

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
    conn.execute("CREATE SEQUENCE IF NOT EXISTS import_uploads_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS category_rules_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS transaction_splits_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS sessions_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS card_statements_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS merchant_decisions_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS review_actions_id_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS saved_ranges_id_seq START 1")
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
        [db.DEFAULT_USER_SLUG, db.DEFAULT_USER_DISPLAY_NAME],
    )
    default_user_row = conn.execute(
        "SELECT id FROM users WHERE slug = ? LIMIT 1",
        [db.DEFAULT_USER_SLUG],
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
        CREATE TABLE IF NOT EXISTS sessions (
            id BIGINT PRIMARY KEY DEFAULT nextval('sessions_id_seq'),
            user_id BIGINT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_ranges (
            id BIGINT PRIMARY KEY DEFAULT nextval('saved_ranges_id_seq'),
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            rule TEXT NOT NULL,
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
        """
    )
    # One-time data steps already taken on this database.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS applied_migrations "
        "(name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT NOW())"
    )
    # A file uploaded while its template is being mapped. Deleted when the
    # template is saved, or a day later.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_uploads (
            id BIGINT PRIMARY KEY DEFAULT nextval('import_uploads_id_seq'),
            user_id BIGINT NOT NULL,
            filename TEXT NOT NULL,
            content BLOB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    # One row per statement a card has closed (see sync_card_statements). Only
    # the dates and the issuer's printed balance live here; every other
    # figure is summed from imported rows so a re-import is never stale.
    conn.execute(
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
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_account_links (
            user_id BIGINT NOT NULL,
            label TEXT NOT NULL,
            display_label TEXT,
            account_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, label)
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
    # Review's per-merchant decisions (see review.py). kind is 'category' or
    # 'transfer'; a merchant can hold one of each (a card part categorized,
    # a checking part a transfer). Rows a category decision changed carry
    # its id in imported_transactions.decision_id, and rows a transfer
    # decision marked carry it in transfer_decision_id, so Undo finds every
    # one of them.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_decisions (
            id BIGINT PRIMARY KEY DEFAULT nextval('merchant_decisions_id_seq'),
            user_id BIGINT NOT NULL,
            merchant_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            category_id BIGINT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, merchant_key, kind)
        )
        """
    )
    # Every Review action, with what Undo needs to reverse it (JSON).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_actions (
            id BIGINT PRIMARY KEY DEFAULT nextval('review_actions_id_seq'),
            user_id BIGINT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
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

    if not db.table_has_column(conn, "recurring_items", "category_id"):
        conn.execute("ALTER TABLE recurring_items ADD COLUMN category_id BIGINT")
    if not db.table_has_column(conn, "manual_transactions", "category_id"):
        conn.execute("ALTER TABLE manual_transactions ADD COLUMN category_id BIGINT")
    if not db.table_has_column(conn, "expected_match_rules", "amount_min"):
        conn.execute("ALTER TABLE expected_match_rules ADD COLUMN amount_min DOUBLE")
    if not db.table_has_column(conn, "expected_match_rules", "amount_max"):
        conn.execute("ALTER TABLE expected_match_rules ADD COLUMN amount_max DOUBLE")
    if not db.table_has_column(conn, "categories", "color"):
        conn.execute("ALTER TABLE categories ADD COLUMN color TEXT")
    if not db.table_has_column(conn, "categories", "user_id"):
        conn.execute("ALTER TABLE categories ADD COLUMN user_id BIGINT")
    # Optional explicit monthly budget for a category. NULL = derive the budget
    # from the category's expected items, as before.
    if not db.table_has_column(conn, "categories", "budget_amount"):
        conn.execute("ALTER TABLE categories ADD COLUMN budget_amount DOUBLE")
    if not db.table_has_column(conn, "recurring_items", "user_id"):
        conn.execute("ALTER TABLE recurring_items ADD COLUMN user_id BIGINT")
    if not db.table_has_column(conn, "manual_transactions", "user_id"):
        conn.execute("ALTER TABLE manual_transactions ADD COLUMN user_id BIGINT")
    if not db.table_has_column(conn, "recurring_items", "account_id"):
        conn.execute("ALTER TABLE recurring_items ADD COLUMN account_id BIGINT")
    if not db.table_has_column(conn, "manual_transactions", "account_id"):
        conn.execute("ALTER TABLE manual_transactions ADD COLUMN account_id BIGINT")
    if not db.table_has_column(conn, "imported_transactions", "account_id"):
        conn.execute("ALTER TABLE imported_transactions ADD COLUMN account_id BIGINT")
    # How a payment to this card appears on the funding account's statement.
    # Per-account so no issuer name has to live in the code.
    if not db.table_has_column(conn, "accounts", "payment_pattern"):
        conn.execute("ALTER TABLE accounts ADD COLUMN payment_pattern TEXT")
    # How an imported row got its category: 'rule' (set by a category rule, and
    # therefore safe to recompute) vs 'manual' (you chose it — rules never clobber it).
    if not db.table_has_column(conn, "imported_transactions", "category_via"):
        conn.execute("ALTER TABLE imported_transactions ADD COLUMN category_via TEXT")
    if not db.table_has_column(conn, "users", "email"):
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    # Import labels are matched case-insensitively but shown back as written.
    if not db.table_has_column(conn, "import_account_links", "display_label"):
        conn.execute("ALTER TABLE import_account_links ADD COLUMN display_label TEXT")
    if not db.table_has_column(conn, "imported_transactions", "decision_id"):
        conn.execute("ALTER TABLE imported_transactions ADD COLUMN decision_id BIGINT")
    if not db.table_has_column(conn, "imported_transactions", "transfer_decision_id"):
        conn.execute("ALTER TABLE imported_transactions ADD COLUMN transfer_decision_id BIGINT")
    # Credentials. NULL password_hash means "not set yet", which puts the app
    # into first-run setup rather than letting anyone in.
    if not db.table_has_column(conn, "users", "password_hash"):
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if not db.table_has_column(conn, "users", "failed_attempts"):
        conn.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER DEFAULT 0")
    if not db.table_has_column(conn, "users", "locked_until"):
        conn.execute("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP")
    # Templates stand alone, shared by any number of accounts; an account
    # points at its default one. DuckDB will not relax a column while an index
    # depends on the table, so that index is dropped and recreated below.
    for column in ("account_id", "amount_field"):
        if not db.column_is_nullable(conn, "import_templates", column):
            conn.execute("DROP INDEX IF EXISTS idx_import_templates_user")
            conn.execute(f"ALTER TABLE import_templates ALTER COLUMN {column} DROP NOT NULL")
    for column in ("date_fallback_field", "amount_in_field", "amount_out_field"):
        if not db.table_has_column(conn, "import_templates", column):
            conn.execute(f"ALTER TABLE import_templates ADD COLUMN {column} TEXT")
    if not db.table_has_column(conn, "accounts", "default_template_id"):
        conn.execute("ALTER TABLE accounts ADD COLUMN default_template_id BIGINT")
    db.migrate_categories_to_user_scoped_uniqueness(conn, default_user_id)
    db.migrate_card_payment_signs(conn)
    db.migrate_card_payment_fingerprints(conn)
    conn.execute(
        "UPDATE categories SET color = ? WHERE color IS NULL OR TRIM(color) = ''",
        [db.DEFAULT_CATEGORY_COLOR],
    )
    conn.execute("UPDATE categories SET user_id = ? WHERE user_id IS NULL", [default_user_id])
    conn.execute("UPDATE recurring_items SET user_id = ? WHERE user_id IS NULL", [default_user_id])
    conn.execute("UPDATE manual_transactions SET user_id = ? WHERE user_id IS NULL", [default_user_id])
    conn.execute("UPDATE users SET email = '' WHERE email IS NULL")
    # Anchor imported rows to accounts by id. Backfill exact name matches;
    # the rest are placed by hand from Accounts (map_import_label_to_account).
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
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
