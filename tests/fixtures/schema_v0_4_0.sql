-- The v0.4.0 schema, with synthetic rows. Migration tests start from this
-- file, run init_db(), and check the new shape and that nothing was lost.
-- Every name, merchant and amount here is invented.

CREATE SEQUENCE accounts_id_seq START 1;
CREATE SEQUENCE amount_overrides_id_seq START 1;
CREATE SEQUENCE card_statements_id_seq START 1;
CREATE SEQUENCE categories_id_seq START 1;
CREATE SEQUENCE category_rules_id_seq START 1;
CREATE SEQUENCE expected_match_rules_id_seq START 1;
CREATE SEQUENCE expected_reconciliations_id_seq START 1;
CREATE SEQUENCE import_templates_id_seq START 1;
CREATE SEQUENCE import_uploads_id_seq START 1;
CREATE SEQUENCE imported_transactions_id_seq START 1;
CREATE SEQUENCE manual_transactions_id_seq START 1;
CREATE SEQUENCE merchant_decisions_id_seq START 1;
CREATE SEQUENCE recurring_items_id_seq START 1;
CREATE SEQUENCE review_actions_id_seq START 1;
CREATE SEQUENCE saved_ranges_id_seq START 1;
CREATE SEQUENCE sessions_id_seq START 1;
CREATE SEQUENCE transaction_splits_id_seq START 1;
CREATE SEQUENCE users_id_seq START 1;

CREATE TABLE accounts (
    id BIGINT DEFAULT(nextval('accounts_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    "name" VARCHAR NOT NULL,
    account_type VARCHAR NOT NULL,
    statement_day INTEGER,
    due_day INTEGER,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    payment_pattern VARCHAR,
    default_template_id BIGINT
);
CREATE TABLE amount_overrides (
    id BIGINT DEFAULT(nextval('amount_overrides_id_seq')) PRIMARY KEY,
    recurring_item_id BIGINT NOT NULL,
    effective_date DATE NOT NULL,
    amount DOUBLE NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE applied_migrations (
    "name" VARCHAR PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE card_statements (
    id BIGINT DEFAULT(nextval('card_statements_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    account_id BIGINT NOT NULL,
    close_date DATE NOT NULL,
    due_date DATE NOT NULL,
    issuer_balance DOUBLE,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    UNIQUE(user_id, account_id, close_date)
);
CREATE TABLE categories (
    id BIGINT DEFAULT(nextval('categories_id_seq')) PRIMARY KEY,
    "name" VARCHAR NOT NULL,
    color VARCHAR DEFAULT('#64748b') NOT NULL,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    budget_amount DOUBLE
);
CREATE TABLE category_rules (
    id BIGINT DEFAULT(nextval('category_rules_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    pattern VARCHAR NOT NULL,
    amount_min DOUBLE,
    amount_max DOUBLE,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE dismissed_suggestions (
    user_id BIGINT,
    suggestion_key VARCHAR,
    reason VARCHAR DEFAULT('dismissed') NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    PRIMARY KEY(user_id, suggestion_key)
);
CREATE TABLE expected_match_rules (
    id BIGINT DEFAULT(nextval('expected_match_rules_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    source_type VARCHAR NOT NULL,
    source_id BIGINT NOT NULL,
    pattern VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    amount_min DOUBLE,
    amount_max DOUBLE
);
CREATE TABLE expected_reconciliations (
    id BIGINT DEFAULT(nextval('expected_reconciliations_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    imported_transaction_id BIGINT NOT NULL,
    source_type VARCHAR NOT NULL,
    source_id BIGINT NOT NULL,
    matched_via VARCHAR DEFAULT('confirm') NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE import_account_links (
    user_id BIGINT,
    "label" VARCHAR,
    display_label VARCHAR,
    account_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    PRIMARY KEY(user_id, "label")
);
CREATE TABLE import_templates (
    id BIGINT DEFAULT(nextval('import_templates_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    "name" VARCHAR NOT NULL,
    account_id BIGINT,
    date_field VARCHAR NOT NULL,
    date_fallback_field VARCHAR,
    date_format VARCHAR,
    amount_field VARCHAR,
    amount_in_field VARCHAR,
    amount_out_field VARCHAR,
    amount_sign VARCHAR DEFAULT('standard') NOT NULL,
    type_field VARCHAR,
    credit_value VARCHAR,
    description_field VARCHAR NOT NULL,
    merchant_field VARCHAR,
    signature VARCHAR DEFAULT('[]') NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE import_uploads (
    id BIGINT DEFAULT(nextval('import_uploads_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    filename VARCHAR NOT NULL,
    "content" BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE imported_transactions (
    id BIGINT DEFAULT(nextval('imported_transactions_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    account VARCHAR NOT NULL,
    tx_date DATE NOT NULL,
    description VARCHAR NOT NULL,
    merchant VARCHAR DEFAULT('') NOT NULL,
    amount DOUBLE NOT NULL,
    flow VARCHAR NOT NULL,
    is_transfer BOOLEAN DEFAULT(false) NOT NULL,
    raw_type VARCHAR DEFAULT('') NOT NULL,
    category_id BIGINT,
    source_file VARCHAR DEFAULT('') NOT NULL,
    fingerprint VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    account_id BIGINT,
    category_via VARCHAR,
    decision_id BIGINT,
    transfer_decision_id BIGINT
);
CREATE TABLE manual_transactions (
    id BIGINT DEFAULT(nextval('manual_transactions_id_seq')) PRIMARY KEY,
    "name" VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    amount DOUBLE NOT NULL,
    tx_date DATE NOT NULL,
    category_id BIGINT,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    account_id BIGINT
);
CREATE TABLE merchant_decisions (
    id BIGINT DEFAULT(nextval('merchant_decisions_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    merchant_key VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    category_id BIGINT,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    UNIQUE(user_id, merchant_key, kind)
);
CREATE TABLE recurring_items (
    id BIGINT DEFAULT(nextval('recurring_items_id_seq')) PRIMARY KEY,
    "name" VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    amount DOUBLE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    frequency_type VARCHAR NOT NULL,
    interval_months INTEGER,
    semimonthly_day1 INTEGER,
    semimonthly_day2 INTEGER,
    day_of_month INTEGER,
    category_id BIGINT,
    user_id BIGINT NOT NULL,
    active BOOLEAN DEFAULT(true) NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    account_id BIGINT
);
CREATE TABLE review_actions (
    id BIGINT DEFAULT(nextval('review_actions_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    kind VARCHAR NOT NULL,
    payload VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE saved_ranges (
    id BIGINT DEFAULT(nextval('saved_ranges_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    "name" VARCHAR NOT NULL,
    "rule" VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE sessions (
    id BIGINT DEFAULT(nextval('sessions_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    token_hash VARCHAR NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE settings (
    "key" VARCHAR PRIMARY KEY,
    "value" VARCHAR NOT NULL
);
CREATE TABLE transaction_splits (
    id BIGINT DEFAULT(nextval('transaction_splits_id_seq')) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    imported_transaction_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    amount DOUBLE NOT NULL,
    note VARCHAR,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL
);
CREATE TABLE users (
    id BIGINT DEFAULT(nextval('users_id_seq')) PRIMARY KEY,
    slug VARCHAR NOT NULL UNIQUE,
    display_name VARCHAR NOT NULL,
    email VARCHAR DEFAULT('') NOT NULL,
    created_at TIMESTAMP DEFAULT(now()) NOT NULL,
    password_hash VARCHAR,
    failed_attempts INTEGER DEFAULT(0),
    locked_until TIMESTAMP
);

CREATE UNIQUE INDEX idx_accounts_user_name_unique ON accounts(user_id, "name");
CREATE INDEX idx_categories_user_id ON categories(user_id);
CREATE UNIQUE INDEX idx_categories_user_name_unique ON categories(user_id, "name");
CREATE INDEX idx_category_rules_category ON category_rules(user_id, category_id);
CREATE INDEX idx_category_rules_user ON category_rules(user_id);
CREATE INDEX idx_import_templates_user ON import_templates(user_id);
CREATE UNIQUE INDEX idx_imported_fingerprint ON imported_transactions(user_id, fingerprint);
CREATE INDEX idx_imported_user_account ON imported_transactions(user_id, account_id);
CREATE INDEX idx_imported_user_date ON imported_transactions(user_id, tx_date);
CREATE INDEX idx_manual_transactions_account ON manual_transactions(user_id, account_id);
CREATE INDEX idx_manual_transactions_user_id ON manual_transactions(user_id);
CREATE INDEX idx_match_rules_source ON expected_match_rules(user_id, source_type, source_id);
CREATE UNIQUE INDEX idx_reconciliation_import_unique ON expected_reconciliations(user_id, imported_transaction_id);
CREATE INDEX idx_reconciliation_source ON expected_reconciliations(user_id, source_type, source_id);
CREATE INDEX idx_recurring_items_account ON recurring_items(user_id, account_id);
CREATE INDEX idx_recurring_items_user_id ON recurring_items(user_id);
CREATE INDEX idx_sessions_token ON sessions(token_hash);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_transaction_splits_tx ON transaction_splits(user_id, imported_transaction_id);

-- Users: 1 is the default profile, 2 is a second person in the household.
INSERT INTO users (slug, display_name, email) VALUES
    ('personal', 'Personal', ''),
    ('second', 'Second User', 'second@example.com');

-- Accounts: 1 checking and 2 card for user 1, 3 checking for user 2.
INSERT INTO accounts (user_id, "name", account_type, statement_day, due_day, payment_pattern, default_template_id) VALUES
    (1, 'Example Checking', 'checking', NULL, NULL, '', 1),
    (1, 'Example Card', 'credit_card', 5, 25, 'EXAMPLE CARD', NULL),
    (2, 'Other Checking', 'checking', NULL, NULL, '', NULL);

-- Categories: 1 housing, 2 groceries (hand-set budget), 3 dining, 4 utilities; 5 is user 2's.
INSERT INTO categories ("name", color, user_id, budget_amount) VALUES
    ('Housing', '#3b82f6', 1, NULL),
    ('Groceries', '#22c55e', 1, 400.0),
    ('Dining', '#f97316', 1, NULL),
    ('Utilities', '#a855f7', 1, NULL),
    ('Groceries', '#22c55e', 2, NULL);

-- Recurring items. The comment on each says what the v0.4.0 rule generates.
INSERT INTO recurring_items ("name", kind, amount, start_date, end_date, frequency_type, interval_months,
                             semimonthly_day1, semimonthly_day2, day_of_month, category_id, user_id, active, account_id) VALUES
    -- 1: day matches the start date
    ('Rent', 'expense', 1200.0, DATE '2026-01-01', NULL, 'monthly', 1, 1, 15, 1, 1, 1, TRUE, 1),
    -- 2: every 14 days from the start
    ('Paycheck', 'income', 2000.0, DATE '2026-01-09', NULL, 'biweekly', 1, 1, 15, NULL, NULL, 1, TRUE, 1),
    -- 3: day 20, start on the 5th: first on 2026-01-20
    ('Power', 'expense', 90.0, DATE '2026-01-05', NULL, 'monthly', 1, 1, 15, 20, 4, 1, TRUE, 1),
    -- 4: day 20, start on the 25th: first on 2026-02-20
    ('Water', 'expense', 40.0, DATE '2026-01-25', NULL, 'monthly', 1, 1, 15, 20, 4, 1, TRUE, 1),
    -- 5: every 3 months on day 31 from February: 02-28, 05-31, 08-31, 11-30
    ('Insurance', 'expense', 300.0, DATE '2026-02-10', NULL, 'every_x_months', 3, 1, 15, 31, NULL, 1, TRUE, 1),
    -- 6: yearly on March 20, start on March 25: first on 2027-03-20
    ('Membership', 'expense', 120.0, DATE '2026-03-25', NULL, 'yearly', 1, 1, 15, 20, NULL, 1, TRUE, 1),
    -- 7: day 31 from January 31, clamped in short months
    ('Streaming', 'expense', 15.0, DATE '2026-01-31', NULL, 'monthly', 1, 1, 15, 31, NULL, 1, TRUE, 2),
    -- 8: the 1st and the 15th
    ('Side income', 'income', 500.0, DATE '2026-01-01', NULL, 'semimonthly', 1, 1, 15, 1, NULL, 1, TRUE, 1),
    -- 9: ended, so completed
    ('Old gym', 'expense', 30.0, DATE '2025-01-10', DATE '2025-12-10', 'monthly', 1, 1, 15, 10, NULL, 1, TRUE, 1),
    -- 10: paused
    ('Paused sub', 'expense', 12.0, DATE '2026-01-12', NULL, 'weekly', 1, 1, 15, 12, NULL, 1, FALSE, 2),
    -- 11: day 31 from February 10, on the card: 02-28, 03-31, 04-30
    ('Storage', 'expense', 55.0, DATE '2026-02-10', NULL, 'monthly', 1, 1, 15, 31, 1, 1, TRUE, 2),
    -- 12: no day set, so the start date's
    ('Lawn care', 'expense', 70.0, DATE '2026-04-18', NULL, 'monthly', 1, 1, 15, NULL, 1, 1, TRUE, 1),
    -- 13: user 2's
    ('Other rent', 'expense', 800.0, DATE '2026-01-03', NULL, 'monthly', 1, 1, 15, 3, NULL, 2, TRUE, 3);

INSERT INTO amount_overrides (recurring_item_id, effective_date, amount) VALUES
    (1, DATE '2026-06-01', 1250.0),
    (13, DATE '2026-03-03', 825.0);

-- One-time expected items: 1 and 2 for user 1, 3 for user 2.
INSERT INTO manual_transactions ("name", kind, amount, tx_date, category_id, user_id, account_id) VALUES
    ('Car repair', 'expense', 400.0, DATE '2026-03-14', NULL, 1, 1),
    ('Bonus', 'income', 1000.0, DATE '2026-04-30', NULL, 1, 1),
    ('Gift', 'expense', 50.0, DATE '2026-02-02', 5, 2, 3);

INSERT INTO category_rules (user_id, category_id, pattern, amount_min, amount_max) VALUES
    (1, 2, 'ACME MARKET', NULL, NULL),
    (1, 4, 'POWER', 50.0, 150.0);

-- Inbox decisions: 1 and 3 set a category, 2 marks a merchant's debits as transfers.
INSERT INTO merchant_decisions (user_id, merchant_key, kind, category_id) VALUES
    (1, 'ACME DINER', 'category', 3),
    (1, 'TRANSFER TO SAVINGS', 'transfer', NULL),
    (2, 'ACME CAFE', 'category', 5);

INSERT INTO imported_transactions (user_id, account, tx_date, description, merchant, amount, flow, is_transfer,
                                   raw_type, category_id, source_file, fingerprint, account_id, category_via,
                                   decision_id, transfer_decision_id) VALUES
    (1, 'Example Checking', DATE '2026-01-01', 'ACH ACME PROPERTY TYPE: RENT', 'ACME PROPERTY', -1200.0, 'expense', FALSE, '', NULL, 'january.csv', 'fp-01', 1, NULL, NULL, NULL),
    (1, 'Example Checking', DATE '2026-01-09', 'PAYROLL EXAMPLE EMPLOYER', 'PAYROLL EXAMPLE EMPLOYER', 2000.0, 'income', FALSE, '', NULL, 'january.csv', 'fp-02', 1, NULL, NULL, NULL),
    (1, 'Example Checking', DATE '2026-01-20', 'ACME POWER CO', 'ACME POWER CO', -90.0, 'expense', FALSE, '', 4, 'january.csv', 'fp-03', 1, 'rule', NULL, NULL),
    (1, 'Example Card', DATE '2026-01-14', 'ACME DINER #12', 'ACME DINER #12', -45.0, 'expense', FALSE, '', 3, 'card-january.csv', 'fp-04', 2, 'merchant', 1, NULL),
    (1, 'Example Card', DATE '2026-01-15', 'ACME MARKET', 'ACME MARKET', -80.0, 'expense', FALSE, '', 2, 'card-january.csv', 'fp-05', 2, 'rule', NULL, NULL),
    (1, 'Example Card', DATE '2026-01-16', 'ACME MARKET', 'ACME MARKET', -60.0, 'expense', FALSE, '', 3, 'card-january.csv', 'fp-06', 2, NULL, NULL, NULL),
    (1, 'Example Checking', DATE '2026-01-18', 'TRANSFER TO SAVINGS EXAMPLE', 'TRANSFER TO SAVINGS', -500.0, 'expense', TRUE, '', NULL, 'january.csv', 'fp-07', 1, NULL, NULL, 2),
    (1, 'Example Checking', DATE '2026-01-25', 'EXAMPLE CARD PAYMENT', 'EXAMPLE CARD PAYMENT', -300.0, 'transfer', TRUE, '', NULL, 'january.csv', 'fp-08', 1, NULL, NULL, NULL),
    (1, 'Example Card', DATE '2026-01-25', 'PAYMENT THANK YOU', 'PAYMENT THANK YOU', 300.0, 'transfer', TRUE, '', NULL, 'card-january.csv', 'fp-09', 2, NULL, NULL, NULL),
    (1, 'Example Checking', DATE '2026-03-14', 'ACME AUTO REPAIR', 'ACME AUTO REPAIR', -400.0, 'expense', FALSE, '', NULL, 'march.csv', 'fp-10', 1, NULL, NULL, NULL),
    (1, 'Example Card', DATE '2026-01-22', 'ACME HARDWARE', 'ACME HARDWARE', -40.0, 'expense', FALSE, '', NULL, 'card-january.csv', 'fp-11', 2, NULL, NULL, NULL),
    (1, 'Example Card', DATE '2026-01-23', 'ACME DINER #12', 'ACME DINER #12', -25.0, 'expense', FALSE, '', 3, 'card-january.csv', 'fp-12', 2, 'merchant', 1, NULL),
    (2, 'Other Checking', DATE '2026-01-03', 'ACH OTHER LANDLORD TYPE: RENT', 'OTHER LANDLORD', -800.0, 'expense', FALSE, '', NULL, 'other.csv', 'fp-13', 3, NULL, NULL, NULL),
    (2, 'Other Checking', DATE '2026-01-10', 'ACME CAFE', 'ACME CAFE', -12.0, 'expense', FALSE, '', 5, 'other.csv', 'fp-14', 3, 'merchant', 3, NULL);

INSERT INTO expected_match_rules (user_id, source_type, source_id, pattern, amount_min, amount_max) VALUES
    (1, 'recurring', 1, 'ACME PROPERTY', NULL, NULL),
    (1, 'one_time', 1, 'ACME AUTO', 400.0, 400.0),
    (2, 'recurring', 13, 'OTHER LANDLORD', NULL, NULL);

INSERT INTO expected_reconciliations (user_id, imported_transaction_id, source_type, source_id, matched_via) VALUES
    (1, 1, 'recurring', 1, 'rule'),
    (1, 2, 'recurring', 2, 'confirm'),
    (1, 3, 'recurring', 3, 'confirm'),
    (1, 10, 'one_time', 1, 'confirm'),
    (2, 13, 'recurring', 13, 'rule');

INSERT INTO transaction_splits (user_id, imported_transaction_id, category_id, amount, note) VALUES
    (1, 12, 2, 10.0, 'shared');

-- One recorded action of each kind, with what Undo needs.
INSERT INTO review_actions (user_id, kind, payload) VALUES
    (1, 'decision', '{"decision_id": 1, "kind": "category", "created_category_id": null}'),
    (1, 'decision', '{"decision_id": 2, "kind": "transfer"}'),
    (1, 'charge', '{"tx_id": 6, "category_id": null, "category_via": null, "is_transfer": false, "decision_id": null, "transfer_decision_id": null, "created_category_id": null}'),
    (1, 'dismiss', '{"key": "transfer:ACME TRANSFERS"}'),
    (1, 'budget', '{"category_id": 2, "before": null}'),
    (1, 'recurring', '{"item_id": 3, "suggestion_key": "ACME POWER CO"}'),
    (2, 'decision', '{"decision_id": 3, "kind": "category", "created_category_id": 5}');

INSERT INTO dismissed_suggestions (user_id, suggestion_key, reason) VALUES
    (1, 'ACME GYM', 'dismissed'),
    (1, 'ACME POWER CO', 'added'),
    (1, 'transfer:ACME TRANSFERS', 'dismissed'),
    (1, 'budget:3', 'dismissed'),
    (1, 'stale:2:2026-01-25', 'dismissed');

INSERT INTO card_statements (user_id, account_id, close_date, due_date, issuer_balance) VALUES
    (1, 2, DATE '2026-01-05', DATE '2026-01-25', NULL);

INSERT INTO import_templates (user_id, "name", date_field, amount_field, description_field) VALUES
    (1, 'Example checking export', 'Date', 'Amount', 'Description');

INSERT INTO import_account_links (user_id, "label", display_label, account_id) VALUES
    (1, 'checking', 'Checking', 1);

INSERT INTO saved_ranges (user_id, "name", "rule") VALUES
    (1, 'This year', '{"grain": "year", "mode": "relative", "start": 0, "end": 0}');

INSERT INTO settings ("key", "value") VALUES
    ('starting_balance', '0'),
    ('user:1:starting_balance', '1500'),
    ('user:1:forecast_window', '{"grain": "day", "mode": "absolute", "start": "2026-01-01", "end": "2026-06-30"}'),
    ('user:2:starting_balance', '900');

INSERT INTO sessions (user_id, token_hash, expires_at) VALUES
    (1, 'synthetic-token-hash', TIMESTAMP '2099-01-01 00:00:00');

INSERT INTO applied_migrations ("name") VALUES ('legacy_templates');
