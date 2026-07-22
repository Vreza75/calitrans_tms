"""Read-only data-integrity report. Never deletes or rewrites anything -
prints findings for human review, per this repo's rule that questionable
production-like data must not be auto-corrected.

Usage:
    python scripts/data_integrity_report.py
    python scripts/data_integrity_report.py --database-url "postgresql://..."
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_migrations import _mask, _resolve_database_url  # noqa: E402
from services.dispatch_legacy_status import _DIRECT_MAP, _PRE_DISPATCH_STATUSES  # noqa: E402
from services.dispatch_stages import SHARED_STAGES  # noqa: E402

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_KNOWN_LOAD_STATUSES = (
    set(_PRE_DISPATCH_STATUSES)
    | set(_DIRECT_MAP.keys())
    | set(SHARED_STAGES)
    | {"Cancelled"}
)

_VALID_SERVICE_FLOWS = {"Import", "Export", "Local Import", "Local Export"}


def _foreign_keys(engine) -> list[dict]:
    query = """
        select
            tc.table_name,
            kcu.column_name,
            ccu.table_name as foreign_table_name,
            ccu.column_name as foreign_column_name
        from information_schema.table_constraints tc
        join information_schema.key_column_usage kcu
            on tc.constraint_name = kcu.constraint_name and tc.table_schema = kcu.table_schema
        join information_schema.constraint_column_usage ccu
            on tc.constraint_name = ccu.constraint_name and tc.table_schema = ccu.table_schema
        where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public'
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [dict(row) for row in rows]


def check_orphaned_foreign_keys(engine, findings: list[str]) -> None:
    for fk in _foreign_keys(engine):
        table, column = fk["table_name"], fk["column_name"]
        foreign_table, foreign_column = fk["foreign_table_name"], fk["foreign_column_name"]
        if not all(_IDENTIFIER_RE.match(v) for v in (table, column, foreign_table, foreign_column)):
            findings.append(f"SKIP  unexpected identifier shape for FK {table}.{column} -> {foreign_table}.{foreign_column}")
            continue
        query = (
            f"select count(*) from {table} t where t.{column} is not null "
            f"and not exists (select 1 from {foreign_table} f where f.{foreign_column} = t.{column})"
        )
        with engine.connect() as conn:
            count = conn.execute(text(query)).scalar()
        if count:
            findings.append(f"REVIEW  {count} row(s) in {table}.{column} reference a missing {foreign_table}.{foreign_column}")


def check_duplicate_multi_container_records(engine, findings: list[str]) -> None:
    query = """
        select parent_booking_key, container_sequence, count(*) as n
        from loads
        where parent_booking_key is not null and container_sequence is not null
        group by parent_booking_key, container_sequence
        having count(*) > 1
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    for parent_key, sequence, n in rows:
        findings.append(f"REVIEW  {n} loads share parent_booking_key={parent_key!r} container_sequence={sequence!r} (should be unique)")


def check_blank_required_identifiers(engine, findings: list[str]) -> None:
    checks = [("loads", "booking_number"), ("drivers", "driver_name")]
    for table, column in checks:
        query = f"select count(*) from {table} where {column} is null or {column} = ''"
        with engine.connect() as conn:
            count = conn.execute(text(query)).scalar()
        if count:
            findings.append(f"REVIEW  {count} row(s) in {table}.{column} are blank/null")


def check_invalid_load_statuses(engine, findings: list[str]) -> None:
    with engine.connect() as conn:
        rows = conn.execute(text("select distinct status from loads where status is not null")).fetchall()
    unknown = sorted(row[0] for row in rows if row[0] not in _KNOWN_LOAD_STATUSES)
    if unknown:
        findings.append(f"REVIEW  loads.status has values not recognized by dispatch_legacy_status/dispatch_stages: {unknown}")


def check_invalid_service_flows(engine, findings: list[str]) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select distinct service_flow from order_intake where service_flow is not null")
        ).fetchall()
    unknown = sorted(row[0] for row in rows if row[0] not in _VALID_SERVICE_FLOWS)
    if unknown:
        findings.append(f"REVIEW  order_intake.service_flow has values outside {_VALID_SERVICE_FLOWS}: {unknown}")


def check_duplicate_email_message_ids(engine, findings: list[str]) -> None:
    query = """
        select source_message_id, count(*) as n
        from order_intake
        where source_message_id is not null
        group by source_message_id
        having count(*) > 1
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    for message_id, n in rows:
        findings.append(f"REVIEW  {n} order_intake rows share source_message_id={message_id!r}")


def check_duplicate_communication_provider_ids(engine, findings: list[str]) -> None:
    query = """
        select provider_message_id, count(*) as n
        from dispatch_messages
        where provider_message_id is not null
        group by provider_message_id
        having count(*) > 1
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    for provider_message_id, n in rows:
        findings.append(f"REVIEW  {n} dispatch_messages rows share provider_message_id={provider_message_id!r}")


CHECKS = [
    check_orphaned_foreign_keys,
    check_duplicate_multi_container_records,
    check_blank_required_identifiers,
    check_invalid_load_statuses,
    check_invalid_service_flows,
    check_duplicate_email_message_ids,
    check_duplicate_communication_provider_ids,
]


def run(database_url: str) -> int:
    print(f"Target database: {_mask(database_url)}")
    print("Read-only report - no data will be changed.\n")

    engine = create_engine(database_url, future=True)
    findings: list[str] = []
    for check in CHECKS:
        check(engine, findings)

    if not findings:
        print("No data-integrity issues found.")
        return 0

    for line in findings:
        print(line)
    print(f"\n{len(findings)} item(s) require human review. Nothing was changed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL for this run only.")
    args = parser.parse_args()

    database_url = args.database_url or _resolve_database_url(None)
    return run(database_url)


if __name__ == "__main__":
    raise SystemExit(main())
