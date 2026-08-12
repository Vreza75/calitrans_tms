"""Deterministic migration runner for the SQL files under database/.

There is no migrations/ subfolder and no numeric filename ordering - the
order below was derived by inspecting real FK/table dependencies between
the files (see MIGRATION_ORDER comment). Applied filenames are recorded in
a schema_migrations tracking table so reruns skip what's already applied.

Usage:
    python scripts/run_migrations.py                         # uses configured DATABASE_URL
    python scripts/run_migrations.py --database-url "postgresql://..."
    python scripts/run_migrations.py --dry-run                # list what would run, touches no DB

Exit code 0 on success (including "nothing new to apply"), 1 on failure.
Never prints the database URL's credentials - only scheme/host/db name.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = REPO_ROOT / "database"

# Authoritative apply order. Each entry must exist as database/<filename>.
# Dependency reasons (do not reorder without re-checking these):
#   schema.sql                              - base tables, no dependencies
#   portpro_style_migration.sql              - ALTERs loads (needs schema.sql)
#   order_intake_migration.sql               - FK -> loads
#   port_houston_integration_migration.sql   - FK -> loads
#   operations_email_workflow_migration.sql  - ALTERs order_intake; creates
#                                               operations_cases/dispatch_messages/etc.
#   dispatcher_workspace_migration.sql       - FK -> operations_cases, order_intake, loads
#   drivers_master_data_migration.sql        - ALTERs drivers (needs schema.sql only)
#   dispatch_closeout_migration.sql          - ALTERs loads (needs schema.sql only)
#   company_memory.sql                       - independent, no FKs
#   multi_container_migration.sql            - FK -> order_intake; reuses
#                                               set_load_updated_at() from schema.sql
#   communications_foundation_migration.sql  - ALTERs dispatch_messages (needs
#                                               operations_email_workflow_migration.sql)
#   operations_fast_triage_migration.sql     - ALTERs order_intake (needs
#                                               order_intake_migration.sql)
#   loads_source_intake_idempotency_migration.sql - ALTERs loads, FK -> order_intake
#                                               (needs schema.sql and order_intake_migration.sql)
#   ai_usage_log_migration.sql               - independent, no FKs (needs schema.sql only)
#   app_users_migration.sql                  - independent, no FKs (Streamlit login accounts)
#   outbox_migration.sql                     - independent, no FKs (Phase 6 transactional outbox)
MIGRATION_ORDER = [
    "schema.sql",
    "portpro_style_migration.sql",
    "order_intake_migration.sql",
    "port_houston_integration_migration.sql",
    "operations_email_workflow_migration.sql",
    "dispatcher_workspace_migration.sql",
    "drivers_master_data_migration.sql",
    "dispatch_closeout_migration.sql",
    "company_memory.sql",
    "multi_container_migration.sql",
    "communications_foundation_migration.sql",
    "operations_fast_triage_migration.sql",
    "loads_source_intake_idempotency_migration.sql",
    "ai_usage_log_migration.sql",
    "app_users_migration.sql",
    "outbox_migration.sql",
]


def _resolve_database_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    sys.path.insert(0, str(REPO_ROOT))
    from config import get_secret  # same resolution every other module uses

    database_url = get_secret("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "DATABASE_URL is not configured. Pass --database-url or set it "
            "via env/.env/.streamlit secrets."
        )
    return database_url


def _mask(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    scheme_and_creds, rest = database_url.split("@", 1)
    scheme = scheme_and_creds.split("://", 1)[0] if "://" in scheme_and_creds else "postgresql"
    return f"{scheme}://***@{rest}"


def _ensure_tracking_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            create table if not exists schema_migrations (
                id bigserial primary key,
                filename text not null unique,
                applied_at timestamptz not null default now()
            )
            """
        )


def _already_applied(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("select filename from schema_migrations")).fetchall()
    return {row[0] for row in rows}


def run(database_url: str, dry_run: bool = False) -> int:
    missing = [f for f in MIGRATION_ORDER if not (DATABASE_DIR / f).exists()]
    if missing:
        for filename in missing:
            print(f"FAIL  {filename}: migration file not found on disk")
        return 1

    print(f"Target database: {_mask(database_url)}")

    if dry_run:
        print("Dry run - no database connection will be made.")
        for filename in MIGRATION_ORDER:
            print(f"WOULD RUN {filename}")
        return 0

    engine = create_engine(database_url, future=True)
    _ensure_tracking_table(engine)
    applied = _already_applied(engine)

    applied_count = 0
    skipped_count = 0

    for filename in MIGRATION_ORDER:
        if filename in applied:
            print(f"SKIP  {filename} (already applied)")
            skipped_count += 1
            continue

        sql = (DATABASE_DIR / filename).read_text(encoding="utf-8")

        try:
            # DDL is transactional in PostgreSQL, so a failure partway
            # through a file rolls back that whole file; previously
            # applied files stay applied and recorded.
            with engine.begin() as conn:
                conn.exec_driver_sql(sql)
                conn.execute(
                    text("insert into schema_migrations (filename) values (:filename)"),
                    {"filename": filename},
                )
        except Exception as exc:
            print(f"FAIL  {filename}: {exc}")
            print(
                f"Stopped. {applied_count} newly applied, {skipped_count} already "
                f"applied before this failure."
            )
            return 1

        print(f"OK    {filename}")
        applied_count += 1

    print(
        f"Done. {applied_count} newly applied, {skipped_count} already applied, "
        f"{len(MIGRATION_ORDER)} total."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL for this run only.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would run without touching any database.")
    args = parser.parse_args()

    database_url = args.database_url or _resolve_database_url(None)
    return run(database_url, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
