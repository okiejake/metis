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

One-time setup:

```bash
python3 -m venv .venv
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

### Deploy to Synology NAS

**Option A — push to a registry (easiest for updates):**

```bash
docker build -t yourusername/metis .
docker push yourusername/metis
```

Then in **Container Manager** on your NAS:
1. Pull `yourusername/metis`
2. Create container with port `8000:8000`
3. Add volume: `/volume1/docker/metis/data` → `/data`
4. Set env var: `FINANCE_DB_PATH=/data/finance.duckdb`
5. Enable auto-restart

**Option B — export image directly (no registry needed):**

```bash
docker build -t metis .
docker save metis | gzip > metis.tar.gz
```

Copy `metis.tar.gz` to your NAS, then import it via **Container Manager → Image → Add → Import**.

---

## Notes

- Data is stored in `finance.duckdb` by default.
- You can create/switch users from the header; each user has separate categories, recurring items, one-off transactions, and settings.
- Existing single-user data is migrated to the default `personal` user at startup.
- To change DB file path:

```bash
export FINANCE_DB_PATH=/path/to/your.duckdb
```
