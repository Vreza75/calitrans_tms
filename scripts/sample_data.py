"""Deterministic, dataset-scoped QA data for disposable Calitrans databases.

This command never reads the application's DATABASE_URL implicitly. A target
URL, an environment marker, a write acknowledgement, and an exact sanitized
database identity must all agree before a connection is opened for writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url

ALLOW_VALUE = "YES_I_UNDERSTAND_QA_DATA_ONLY"
SAFE_ENVIRONMENTS = {"development", "test"}
SAFE_NAME_TOKENS = {"dev", "development", "test", "testing", "qa", "sandbox", "scratch", "disposable"}
DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")


class SafetyError(RuntimeError):
    pass


@dataclass
class Summary:
    created: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    deleted: int = 0
    by_table: dict[str, int] = field(default_factory=dict)

    def record(self, table: str, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)
        self.by_table[table] = self.by_table.get(table, 0) + 1


def target_identity(database_url: str) -> str:
    url = make_url(database_url)
    host = (url.host or "local").lower()
    port = f":{url.port}" if url.port else ""
    database = (url.database or "").strip("/")
    return f"{host}{port}/{database}"


def assert_safe_target(*, database_url: str, environment: str, expected_identity: str, allow_value: str) -> str:
    if environment not in SAFE_ENVIRONMENTS:
        raise SafetyError("--environment must be development or test")
    if allow_value != ALLOW_VALUE:
        raise SafetyError(f"CALITRANS_ALLOW_QA_DATA must equal {ALLOW_VALUE}")

    url = make_url(database_url)
    identity = target_identity(database_url)
    database = (url.database or "").lower()
    host = (url.host or "").lower()
    name_tokens = {token for token in re.split(r"[^a-z0-9]+", database) if token}

    if not database or not host:
        raise SafetyError("Target URL must identify both a database host and database name")
    if database in {"postgres", "production", "prod"}:
        raise SafetyError("Generic or production database names are never accepted")
    if not (name_tokens & SAFE_NAME_TOKENS):
        raise SafetyError("Database name must contain an explicit dev/test/qa/sandbox/scratch marker")
    if expected_identity.strip().lower() != identity.lower():
        raise SafetyError(f"CALITRANS_QA_DATABASE_IDENTITY must exactly equal {identity}")
    return identity


def marker(dataset: str) -> str:
    return f"SYNTHETIC-CALITRANS-QA::{dataset}"


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _verify_live_identity(conn: Connection, expected_identity: str) -> None:
    row = conn.execute(text("select current_database() as database_name, inet_server_addr()::text as server_addr")).mappings().one()
    database_name = str(row["database_name"] or "").lower()
    expected_database = expected_identity.rsplit("/", 1)[-1].lower()
    if database_name != expected_database:
        raise SafetyError(f"Connected database {database_name!r} does not match approved identity")


def _upsert_named(conn: Connection, *, table: str, key_column: str, key_value: str, values: dict[str, Any]) -> tuple[int, bool]:
    existing = conn.execute(
        text(f"select id from {table} where {key_column} = :key_value limit 1"), {"key_value": key_value}
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing), False
    columns = [key_column, *values]
    params = {"key_value": key_value, **values}
    placeholders = [":key_value", *(f":{name}" for name in values)]
    row_id = conn.execute(
        text(f"insert into {table} ({', '.join(columns)}) values ({', '.join(placeholders)}) returning id"), params
    ).scalar_one()
    return int(row_id), True


def seed(engine: Engine, dataset: str) -> Summary:
    tag = marker(dataset)
    summary = Summary()
    tables = _tables(engine)
    required = {"customers", "warehouses", "carriers", "drivers", "loads", "order_intake"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Required migrated tables are missing: {', '.join(missing)}")

    with engine.begin() as conn:
        customer_id, created = _upsert_named(
            conn, table="customers", key_column="company_name", key_value=f"{tag} Fictional Shipper",
            values={"contact_name": "QA Contact", "email": f"qa+{dataset}-shipper@example.invalid", "phone": "+1-202-555-0100"},
        )
        summary.record("customers", "created" if created else "reused")
        warehouse_id, created = _upsert_named(
            conn, table="warehouses", key_column="warehouse_name", key_value=f"{tag} Test Warehouse",
            values={"address": "100 QA Way", "city": "Testville", "state": "TX", "zip_code": "00000", "contact_name": "QA Contact", "phone": "+1-202-555-0101"},
        )
        summary.record("warehouses", "created" if created else "reused")
        carrier_id, created = _upsert_named(
            conn, table="carriers", key_column="company_name", key_value=f"{tag} Fictional Carrier",
            values={"contact_name": "QA Carrier Contact", "email": f"qa+{dataset}-carrier@example.invalid", "phone": "+1-202-555-0102", "mc_number": f"QA-{dataset.upper()}"},
        )
        summary.record("carriers", "created" if created else "reused")

        driver_ids: list[int] = []
        for suffix, phone, truck in (("Available", "+1-202-555-0110", "QA-TRACTOR-01"), ("Assigned", "+1-202-555-0111", "QA-TRACTOR-02"), ("Incomplete", None, None)):
            driver_id, created = _upsert_named(
                conn, table="drivers", key_column="driver_name", key_value=f"{tag} Driver {suffix}",
                values={"carrier_id": carrier_id, "phone": phone, "email": f"qa+{dataset}-{suffix.lower()}@example.invalid", "truck_number": truck},
            )
            driver_ids.append(driver_id)
            summary.record("drivers", "created" if created else "reused")

        load_specs = [
            ("NEW", "New", "Import", None),
            ("ASSIGNED", "Assigned", "Export", f"{tag} Driver Assigned"),
            ("DELIVERED", "Delivered", "Local Import", None),
            ("CANCELLED", "Cancelled", "Local Export", None),
        ]
        load_ids: list[int] = []
        for suffix, status, flow, driver in load_specs:
            booking = f"QA-{dataset.upper()}-{suffix}"
            load_id, created = _upsert_named(
                conn, table="loads", key_column="booking_number", key_value=booking,
                values={
                    "type": flow, "load_id": f"{tag}-{suffix}", "reference_number": f"QA-REF-{dataset.upper()}-{suffix}",
                    "customer": f"{tag} Fictional Shipper", "port": "QA Port", "warehouse": f"{tag} Test Warehouse",
                    "address": "100 QA Way, Testville, TX 00000", "status": status, "driver_name": driver,
                    "truck_assigned": "QA-TRACTOR-02" if driver else None, "size": "40HC", "dispatcher_notes": f"{tag} deterministic sample record",
                },
            )
            load_ids.append(load_id)
            summary.record("loads", "created" if created else "reused")

        intake_key = f"<{dataset}.new-order@qa.invalid>"
        intake_id = conn.execute(text("select id from order_intake where source_message_id = :key limit 1"), {"key": intake_key}).scalar_one_or_none()
        if intake_id is None:
            intake_id = conn.execute(
                text("""insert into order_intake
                    (source, source_subject, source_sender, raw_text, parsed_data, intake_status, action_required, source_message_id)
                    values ('qa_sample', :subject, :sender, :raw_text, cast(:parsed_data as jsonb), 'Needs Review', 'Review synthetic order', :message_id)
                    returning id"""),
                {"subject": f"{tag} New inbound order", "sender": f"qa+{dataset}-sender@example.invalid", "raw_text": f"{tag}\nSynthetic order requiring review.", "parsed_data": json.dumps({"Booking Number": f"QA-{dataset.upper()}-INBOX", "Customer": f"{tag} Fictional Shipper", "TYPE": "Import", "synthetic": True, "dataset_id": dataset}), "message_id": intake_key},
            ).scalar_one()
            summary.record("order_intake", "created")
        else:
            summary.record("order_intake", "reused")

        if "operations_tasks" in tables:
            task_key = f"{tag} Open task"
            _, created = _upsert_named(
                conn, table="operations_tasks", key_column="task_title", key_value=task_key,
                values={"task_description": f"{tag} deterministic task", "task_status": "Open", "task_priority": "High", "owner": "Dispatch", "intake_id": int(intake_id), "load_id": load_ids[0], "customer": f"{tag} Fictional Shipper", "booking_number": f"QA-{dataset.upper()}-NEW", "created_by": "qa-sample-data"},
            )
            summary.record("operations_tasks", "created" if created else "reused")

        async_specs = (
            ("worker_jobs", "job_type", "qa.sample.noop", "completed", "completed_at"),
            ("outbox_events", "event_type", "qa.sample.noop", "delivered", "processed_at"),
            ("domain_events", "event_type", "qa.sample.changed", "published", "published_at"),
        )
        terminal_at = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        for table, type_column, type_value, status, terminal_column in async_specs:
            if table not in tables:
                summary.record(table, "skipped")
                continue
            key = f"qa-sample:{dataset}:{table}"
            existing = conn.execute(text(f"select id from {table} where idempotency_key = :key"), {"key": key}).scalar_one_or_none()
            if existing is None:
                conn.execute(text(f"""insert into {table}
                    ({type_column}, aggregate_type, aggregate_id, payload, idempotency_key, status, actor, {terminal_column})
                    values (:type_value, 'qa_dataset', :dataset, cast(:payload as jsonb), :key, :status, 'qa-sample-data', :terminal_at)"""),
                    {"type_value": type_value, "dataset": dataset, "payload": json.dumps({"synthetic": True, "dataset_id": dataset}), "key": key, "status": status, "terminal_at": terminal_at})
                summary.record(table, "created")
            else:
                summary.record(table, "reused")
    return summary


def list_dataset(engine: Engine, dataset: str) -> dict[str, int]:
    tag = marker(dataset)
    tables = _tables(engine)
    checks = {
        "customers": ("company_name", f"{tag}%"), "warehouses": ("warehouse_name", f"{tag}%"),
        "carriers": ("company_name", f"{tag}%"), "drivers": ("driver_name", f"{tag}%"),
        "loads": ("load_id", f"{tag}%"), "order_intake": ("source_subject", f"{tag}%"),
        "operations_tasks": ("task_title", f"{tag}%"),
    }
    result: dict[str, int] = {}
    with engine.connect() as conn:
        for table, (column, pattern) in checks.items():
            if table in tables:
                result[table] = int(conn.execute(text(f"select count(*) from {table} where {column} like :pattern"), {"pattern": pattern}).scalar_one())
        for table in ("worker_jobs", "outbox_events", "domain_events"):
            if table in tables:
                result[table] = int(conn.execute(text(f"select count(*) from {table} where idempotency_key = :key"), {"key": f"qa-sample:{dataset}:{table}"}).scalar_one())
    return result


def cleanup(engine: Engine, dataset: str) -> Summary:
    tag = marker(dataset)
    summary = Summary()
    tables = _tables(engine)
    with engine.begin() as conn:
        for table in ("domain_events", "outbox_events", "worker_jobs"):
            if table in tables:
                count = conn.execute(text(f"delete from {table} where idempotency_key = :key"), {"key": f"qa-sample:{dataset}:{table}"}).rowcount
                summary.deleted += count
                summary.by_table[table] = count
        for table, column in (("operations_tasks", "task_title"), ("order_intake", "source_subject"), ("loads", "load_id"), ("drivers", "driver_name"), ("warehouses", "warehouse_name"), ("customers", "company_name"), ("carriers", "company_name")):
            if table in tables:
                count = conn.execute(text(f"delete from {table} where {column} like :pattern"), {"pattern": f"{tag}%"}).rowcount
                summary.deleted += count
                summary.by_table[table] = count
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "validate", "list", "cleanup", "reset"))
    parser.add_argument("--dataset", default="baseline-01")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--environment", choices=sorted(SAFE_ENVIRONMENTS), required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not DATASET_RE.fullmatch(args.dataset):
        print("ERROR: dataset must match [a-z0-9][a-z0-9-]{2,39}", file=sys.stderr)
        return 2
    try:
        identity = assert_safe_target(
            database_url=args.database_url,
            environment=args.environment,
            expected_identity=os.environ.get("CALITRANS_QA_DATABASE_IDENTITY", ""),
            allow_value=os.environ.get("CALITRANS_ALLOW_QA_DATA", ""),
        )
    except (SafetyError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    print(f"Target: {identity} | environment={args.environment} | dataset={args.dataset}")
    if args.dry_run:
        print(f"DRY RUN: {args.command} would operate only on marker {marker(args.dataset)}")
        return 0

    engine = create_engine(args.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            _verify_live_identity(conn, identity)
        if args.command == "seed":
            result: Any = seed(engine, args.dataset)
        elif args.command == "cleanup":
            result = cleanup(engine, args.dataset)
        elif args.command == "reset":
            cleanup(engine, args.dataset)
            result = seed(engine, args.dataset)
        else:
            counts = list_dataset(engine, args.dataset)
            result = counts
            if args.command == "validate" and not counts:
                print("Validation failed: no supported sample records found", file=sys.stderr)
                return 4
        print(json.dumps(result.__dict__ if isinstance(result, Summary) else result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: sample-data operation failed", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
