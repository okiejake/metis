# Accounts and imports Implementation Plan

**Goal:** Every import goes into an account the user made, through a template the
user mapped and checked. No built-in CSV recognition.

**Spec:** `docs/superpowers/specs/2026-11-13-account-imports-design.md`

**Tech:** FastAPI, Jinja2, DuckDB, pytest. TDD for every behaviour.

## Global constraints

- Synthetic test data only ("Example Checking", "ACME …").
- Every template URL goes through `{{ base_path() }}` (guard test).
- Existing imported rows keep their fingerprints; dedup by account still holds.

## Tasks

1. **Templates and the parser** (`imports.py`, new; `services.py` schema)
   - Schema: `import_templates.account_id` nullable; new columns
     `date_fallback_field`, `amount_in_field`, `amount_out_field`;
     `accounts.default_template_id`; table `import_uploads`
     (id, user_id, filename, content, created_at).
   - `template_columns(t) -> list[str]`, `missing_columns(header, t)`,
     `detect_date_format(values) -> str|None`, `derive_merchant(description)`,
     `parse_rows(text, template, account, transfer_patterns) -> rows`
     (raises `CsvImportError` naming missing columns).
   - `save_template(user_id, fields) -> id`, `load_templates(user_id)` (with
     `created` as `YYYY-MM-DD` and `used_by` account names), `load_template`,
     `delete_template` (clears defaults), `set_default_template`.
   - Remove: built-in parsers and detection, `ensure_seed_accounts`, label
     resolution and inference in `upsert_imported_transactions` (rows carry
     `account_id`).
   - Tests: `test_import_templates.py`; rewrite tests that used built-ins.
2. **Import into an account**: `POST /accounts/{id}/import` (template_id, file);
   runs decisions, rules and match rules as today; message names the account.
3. **Template creation**: `GET /templates/new` (upload, optional `account_id`),
   `POST /templates/upload` (stores `import_uploads`), `GET /templates/map/{upload}`
   (sample + form + preview), `POST /templates/preview` (fragment),
   `POST /templates` (save; with an account: set default and import the file),
   `POST /templates/{id}/delete`.
4. **Add account flow**: `GET /accounts/new`, `POST /accounts/new` — name, type,
   cycle days, method cards, template cards / Create new.
5. **Accounts page**: Import button + shared pop-up part, Templates section,
   rows-with-no-account panel; remove the Import page and its nav link.
6. **Stale accounts in Review**: `stale_accounts(user_id, today)`; Imports list
   first; Dismiss keyed to the last transaction date; counted in the badge.
7. **Existing users**: startup migration creating the three legacy templates for
   users with imported rows, and setting unambiguous defaults.
8. **Preview build**: `image.yml` tags `:preview` on a manual run from a branch,
   so the NAS QA can run a branch before it reaches main.
