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

```text
Streamlit app
   ↓
db_client.py
   ↓
Supabase PostgreSQL

Future custom integrations:
External systems / customer apps / webhooks
   ↓
FastAPI API layer
   ↓
Supabase PostgreSQL
```

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

## Notes

The app stores uploaded PDFs under:

```bash
storage/load_documents/
```

For production, you can later move document storage to Supabase Storage, Google Drive, S3, or Azure Blob Storage.

Smartsheet is no longer required.
