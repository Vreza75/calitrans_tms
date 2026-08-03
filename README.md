# Calitrans Dispatch Streamlit MVP - PostgreSQL/Supabase Upgrade

This version removes Smartsheet as the system of record and uses PostgreSQL/Supabase instead.

Your original Smartsheet app was preserved as:

```bash
app_smartsheet_legacy_backup.py
```

## What changed

- `app.py` now reads/writes loads from PostgreSQL/Supabase.
- `db_client.py` replaces the old Smartsheet client.
- `smartsheet_client.py` is now a compatibility wrapper so older imports still work.
- `database/schema.sql` creates the new TMS tables.
- `api/main.py` adds a FastAPI starter for future custom integrations.
- Existing parser files were kept:
  - `order_parser.py`
  - `email_parser.py`
  - `email_client.py`

## Recommended architecture

As of the Phase 1 backend-boundary work (see
`docs/architecture/BACKEND_BOUNDARY_PHASE_1.md` for the full design), both
the Streamlit UI and the FastAPI API call the same framework-neutral
`application/` layer instead of duplicating business logic:

```text
Streamlit dispatcher/admin UI      FastAPI (api/main.py, /api/v1/*)
   |                                    |
   +------------------+  +--------------+
                      |  |
                      v  v
              application/*  (framework-neutral: no streamlit import)
                      |
                      v
              repositories/*  (SQL only)
                      |
                      v
              db_client.py
                      |
                      v
              Supabase PostgreSQL
```

`api/main.py` also still exposes two legacy unversioned endpoints
(`/health`, `/loads`) kept for backward compatibility - new work goes
through `/api/v1/*` (`api/routers/`).

## Setup

### 1. Create Supabase project

Create a new Supabase project, then copy the PostgreSQL connection string.

Use SQLAlchemy format:

```text
postgresql+psycopg2://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres
```

### 2. Create tables

Open Supabase SQL Editor and run:

```bash
database/schema.sql
```

### 3. Configure secrets

Copy:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Update:

```toml
DATABASE_URL = "postgresql+psycopg2://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres"
```

### 4. Install requirements

```bash
pip install -r requirements.txt
```

### 5. Run Streamlit

```bash
streamlit run app.py
```

### 6. Optional: run FastAPI integration API

```bash
uvicorn api.main:app --reload --port 8000
```

Then visit:

```text
http://127.0.0.1:8000/docs
```

The API has no authentication yet (see
`docs/architecture/BACKEND_BOUNDARY_PHASE_1.md`, "Known limitations") - do
not expose it outside a trusted network. Cross-origin requests are blocked
by default; set `CORS_ALLOWED_ORIGINS` (comma-separated) to allow a specific
frontend origin.

### 7. Schema migrations

Run migrations explicitly, not by loading a page:

```bash
python scripts/run_migrations.py
```

Normal Streamlit/FastAPI page/request handling does not run `CREATE TABLE`,
`ALTER TABLE`, or `CREATE INDEX` - a lightweight readiness check
(`db_client.column_exists`) verifies the schema once per process instead.

### 8. Tests

```bash
python -m compileall .
pytest -q
```

50 tests are skipped by default - they require `MIGRATION_TEST_DATABASE_URL`
and/or `INBOX_CERTIFICATION_DATABASE_URL` pointing at a disposable,
empty PostgreSQL database (never the app's real `DATABASE_URL`). See
`tests/test_migration_runner.py` and
`tests/integration/operations_inbox/harness.py`.

## Notes

The app stores uploaded PDFs under:

```bash
storage/load_documents/
```

For production, you can later move document storage to Supabase Storage, Google Drive, S3, or Azure Blob Storage.

Smartsheet is no longer required.
