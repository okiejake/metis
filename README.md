# Metis Finance Tracker

Local Python finance tracker with recurring income/expenses and a running-balance forecast ledger.

## Features

- Multi-user support with isolated data per user (shared app, separate ledgers/categories/settings)
- Add recurring items:
  - Bi-weekly
  - Semi-monthly (two days per month)
  - Monthly (day of month)
  - Every X months
- Manage categories (name + color) and assign them to recurring or one-off transactions
- Edit recurring items and one-off transactions
- Add one-off manual transactions
- Set a starting balance
- Forecast ledger with running balance and first-negative-date alert
- Dashboard page with monthly running totals and running-balance chart
- DuckDB local file database (`finance.duckdb`) for all users (no Postgres required)

## Run

One-time setup, with Python 3.12 or newer:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Launch the web server:

```bash
source .venv/bin/activate
uvicorn app:app --reload
```

Open: <http://127.0.0.1:8000>

## Docker / Synology NAS

### Build and run locally

```bash
docker compose up --build
```

Open: <http://localhost:8000>

Data is persisted in a `./data/` folder on the host (mapped to `/data/finance.duckdb` inside the container).

### Deploy to a home server

[`deploy/`](deploy/README.md) runs a production and a QA environment side by
side, each with its own database, served privately over Tailscale. Production
follows the latest release and QA follows `main`.

---

## Notes

- Data is stored in `finance.duckdb` by default.
- You can create/switch users from the header; each user has separate categories, recurring items, one-off transactions, and settings.
- Existing single-user data is migrated to the default `personal` user at startup.
- To change DB file path:

```bash
export FINANCE_DB_PATH=/path/to/your.duckdb
```
