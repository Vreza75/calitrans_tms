"""Operations Inbox certification harness.

Runs one fixture email through the real intake pipeline
(services.operations_inbox_service.sync_operations_email_engine) against a
disposable scratch Postgres database, then compares the resulting
order_intake row against a hand-approved expected.json.

Safety model (same shape as tests/test_migration_runner.py's
MIGRATION_TEST_DATABASE_URL gate): this module NEVER reads the app's
configured DATABASE_URL. It only accepts a URL from the
INBOX_CERTIFICATION_DATABASE_URL environment variable (or an explicit
argument), and refuses to run if that URL matches the app's configured
DATABASE_URL secret, so a misconfigured environment cannot point this at a
real database.
"""
from __future__ import annotations

import email
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "operations_inbox"
ENV_VAR = "INBOX_CERTIFICATION_DATABASE_URL"

# Tables touched by the intake -> classification -> triage -> load-creation
# pipeline. Truncated (RESTART IDENTITY CASCADE) before every case to
# guarantee "Fixture created -> ... -> clean database state".
RESETTABLE_TABLES = [
    "operations_ai_feedback",
    "operations_email_replies",
    "operations_tasks",
    "dispatcher_actions",
    "ai_recommendation_decisions",
    "load_communications",
    "quote_requests",
    "operations_case_notes",
    "operations_case_events",
    "operations_case_owner_history",
    "operations_cases",
    "order_intake_drafts",
    "order_intake",
    "status_events",
    "appointments",
    "documents",
    "loads",
]

EXPECTED_SCHEMA_FIELDS = [
    "intent",
    "service_flow",
    "queue",
    "decision",
    "existing_load_match",
    "booking_number",
    "order_numbers",
    "container_count",
    "containers",
    "customer",
    "pickup",
    "delivery",
    "dates",
    "references",
    "missing_required_fields",
    "requires_human_review",
]


class CertificationSafetyError(RuntimeError):
    """Raised when the harness would otherwise risk touching a non-scratch database."""


class MissingScratchDatabaseError(RuntimeError):
    """Raised when INBOX_CERTIFICATION_DATABASE_URL is not set."""


def require_scratch_database_url(explicit_url: str | None = None) -> str:
    url = explicit_url or os.environ.get(ENV_VAR)
    if not url:
        raise MissingScratchDatabaseError(
            f"{ENV_VAR} is not set. Point it at a disposable, empty PostgreSQL "
            "database before running Operations Inbox certification cases. "
            "This is never read from .streamlit/secrets.toml or DATABASE_URL."
        )

    import config

    configured = None
    try:
        configured = config.get_secret("DATABASE_URL")
    except Exception:
        configured = None

    if configured and configured.strip() == url.strip():
        raise CertificationSafetyError(
            f"{ENV_VAR} is identical to the app's configured DATABASE_URL. "
            "Refusing to run - certification must target a dedicated scratch database."
        )

    return url


@contextmanager
def scratch_database(url: str):
    """Force every db_client.execute/read_df call to use `url`, regardless of
    what .streamlit/secrets.toml or the environment has configured for
    DATABASE_URL."""
    import db_client

    original_get_secret = db_client.get_secret

    def _forced_get_secret(name: str, default: str | None = None):
        if name == "DATABASE_URL":
            return url
        return original_get_secret(name, default)

    with mock.patch.object(db_client, "get_secret", _forced_get_secret):
        db_client._ENGINE_CACHE.pop(url, None)
        yield
        db_client._ENGINE_CACHE.pop(url, None)


def reset_scratch_schema(url: str) -> None:
    from scripts.run_migrations import run as run_migrations

    exit_code = run_migrations(url)
    if exit_code != 0:
        raise RuntimeError(f"Migration run against scratch database failed (exit code {exit_code}).")


def reset_scratch_data(url: str) -> None:
    with scratch_database(url):
        import db_client

        table_list = ", ".join(RESETTABLE_TABLES)
        db_client.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")


@dataclass
class Fixture:
    case_id: str
    case_dir: Path
    config: dict[str, Any]
    message: dict[str, Any]
    expected: dict[str, Any]
    attachments: list[dict[str, Any]] = field(default_factory=list)


def _decode_email_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    body = ""
    attachments: list[dict[str, Any]] = []
    if msg.is_multipart():
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()
            if filename or "attachment" in disposition.lower():
                attachments.append(
                    {
                        "filename": filename or "attachment",
                        "content": part.get_payload(decode=True),
                        "content_type": part.get_content_type(),
                    }
                )
            elif part.get_content_type() == "text/plain" and not body:
                body = part.get_content()
    else:
        body = msg.get_content()

    received_at = None
    date_header = msg.get("Date")
    if date_header:
        try:
            received_at = email.utils.parsedate_to_datetime(date_header).isoformat()
        except Exception:
            received_at = None

    return {
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "message_id": str(msg.get("Message-ID", "")).strip("<>"),
        "received_at": received_at,
        "body": body.strip(),
        "attachments": attachments,
    }


def _load_attachments_dir(case_dir: Path) -> list[dict[str, Any]]:
    attachments_dir = case_dir / "attachments"
    if not attachments_dir.is_dir():
        return []
    attachments = []
    for path in sorted(attachments_dir.iterdir()):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
        attachments.append(
            {
                "filename": path.name,
                "content": path.read_bytes(),
                "content_type": content_type,
            }
        )
    return attachments


def load_fixture(case_id: str) -> Fixture:
    case_dir = FIXTURES_DIR / case_id
    if not case_dir.is_dir():
        raise FileNotFoundError(f"No fixture directory at {case_dir}")

    case_config = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8")) or {}

    email_path = case_dir / "email.eml"
    if not email_path.exists():
        email_path = case_dir / "email.txt"
    if not email_path.exists():
        raise FileNotFoundError(f"Case {case_id} has no email.eml or email.txt")

    message = _decode_email_file(email_path)
    message["direction"] = "inbound"
    message["mailbox"] = "dispatch@calitranscorp.com:INBOX"
    message["mailbox_account"] = "dispatch@calitranscorp.com"
    message["id"] = case_id

    dir_attachments = _load_attachments_dir(case_dir)
    if dir_attachments:
        message["attachments"] = dir_attachments

    expected_path = case_dir / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.exists() else {}

    return Fixture(
        case_id=case_id,
        case_dir=case_dir,
        config=case_config,
        message=message,
        expected=expected,
        attachments=message.get("attachments") or [],
    )


def seed_existing_load(fixture: Fixture) -> None:
    """Insert a pre-existing load for update/change cases that declare
    existing_load_required: true plus a seed_load: block in case.yaml."""
    seed = fixture.config.get("seed_load")
    if not seed:
        return

    import db_client

    columns = list(seed.keys())
    column_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    db_client.execute(
        f"insert into loads ({column_list}) values ({placeholders})",
        seed,
    )


def _fake_fetch_operations_email_sync(message: dict):
    def _fetch(limit=12, time_budget_seconds=None):
        return [message]

    return _fetch


def process_fixture_email(fixture: Fixture) -> dict:
    """Run the fixture email through the real sync_operations_email_engine,
    exactly as if it had arrived over IMAP."""
    from services import email_client, operations_inbox_service

    fake_fetch = _fake_fetch_operations_email_sync(fixture.message)
    with mock.patch.object(email_client, "fetch_operations_email_sync", fake_fetch):
        return operations_inbox_service.sync_operations_email_engine(limit=5, time_budget_seconds=30)


def capture_actual_result(fixture: Fixture) -> dict:
    """Read back the order_intake row(s) created for this case and translate
    them into the expected.json schema. This mapping is intentionally
    centralized here so per-case tuning (as real cases are certified) happens
    in one place instead of scattering ad hoc field lookups."""
    import db_client

    df = db_client.read_df(
        "select * from order_intake where source_message_id = :message_id order by id asc",
        {"message_id": _stable_message_id(fixture.message)},
    )

    if df.empty:
        return {field_name: None for field_name in EXPECTED_SCHEMA_FIELDS} | {"_row_count": 0}

    rows = [row.to_dict() for _, row in df.iterrows()]
    primary = rows[0]
    parsed = primary.get("parsed_data") or {}
    if isinstance(parsed, str):
        parsed = json.loads(parsed) if parsed else {}

    container_number = parsed.get("Container Number") or ""
    containers = [container_number] if container_number else []
    booking_number = parsed.get("Booking Number") or ""

    decision = "Create New Order"
    if primary.get("matched_load_id"):
        decision = "Update Existing Order"

    actual = {
        "intent": primary.get("request_type"),
        "service_flow": parsed.get("TYPE") or parsed.get("Service Flow") or None,
        "queue": primary.get("work_queue"),
        "decision": decision,
        "existing_load_match": primary.get("matched_load_id"),
        "booking_number": booking_number,
        "order_numbers": [booking_number] if booking_number else [],
        "container_count": len(containers) or None,
        "containers": containers,
        "customer": parsed.get("Customer") or None,
        "pickup": {
            "warehouse": parsed.get("Loading Warehouse") or None,
            "address": parsed.get("Loading Address") or None,
        },
        "delivery": {
            "warehouse": parsed.get("Warehouse") or None,
            "address": parsed.get("Address") or None,
        },
        "dates": {
            "delivery_need_date": parsed.get("Delivery Need Date") or None,
            "last_free_day": parsed.get("Last Free Day") or None,
        },
        "references": {
            "reference_number": parsed.get("Reference Number") or None,
        },
        "missing_required_fields": [],
        "requires_human_review": bool(primary.get("llm_review_required")),
        "_row_count": len(rows),
        "_row_ids": [row.get("id") for row in rows],
    }
    return actual


def _stable_message_id(message: dict) -> str:
    from services.operations_inbox_service import _email_sync_unique_message_id

    return _email_sync_unique_message_id(message)


def compare(expected: dict, actual: dict) -> dict:
    """Field-by-field diff plus every accuracy measurement required by the
    certification framework."""
    diffs = {}
    matched = 0
    compared = 0
    for key in EXPECTED_SCHEMA_FIELDS:
        if key not in expected:
            continue
        compared += 1
        expected_value = expected.get(key)
        actual_value = actual.get(key)
        if expected_value == actual_value:
            matched += 1
        else:
            diffs[key] = {"expected": expected_value, "actual": actual_value}

    all_field_accuracy = (matched / compared * 100) if compared else 100.0

    critical_fields = expected.get("_critical_fields", [])
    critical_matched = sum(1 for f in critical_fields if f not in diffs)
    critical_accuracy = (critical_matched / len(critical_fields) * 100) if critical_fields else 100.0

    return {
        "diffs": diffs,
        "classification_accuracy": 100.0 if "intent" not in diffs else 0.0,
        "service_flow_accuracy": 100.0 if "service_flow" not in diffs else 0.0,
        "critical_field_accuracy": critical_accuracy,
        "all_field_accuracy": all_field_accuracy,
        "container_count_accuracy": 100.0 if "container_count" not in diffs else 0.0,
        "container_number_accuracy": 100.0 if "containers" not in diffs else 0.0,
        "existing_load_match_accuracy": 100.0 if "existing_load_match" not in diffs else 0.0,
        "queue_resolution": "PASS" if "queue" not in diffs else "FAIL",
        "exact_record_pass": len(diffs) == 0,
    }


@dataclass
class CaseReport:
    case_id: str
    actual: dict
    comparison: dict
    duplicate_protection: str
    row_count_first_run: int
    row_count_after_rerun: int


def run_case(case_id: str, *, database_url: str | None = None) -> CaseReport:
    url = require_scratch_database_url(database_url)
    fixture = load_fixture(case_id)

    reset_scratch_schema(url)
    reset_scratch_data(url)

    with scratch_database(url):
        seed_existing_load(fixture)
        process_fixture_email(fixture)
        actual_first = capture_actual_result(fixture)

    comparison = compare(fixture.expected, actual_first)

    # Duplicate-rerun check: process the exact same message again without
    # resetting data, confirm no additional order_intake rows appear.
    with scratch_database(url):
        process_fixture_email(fixture)
        actual_after_rerun = capture_actual_result(fixture)

    duplicate_protection = (
        "PASS" if actual_after_rerun["_row_count"] == actual_first["_row_count"] else "FAIL"
    )

    actual_path = fixture.case_dir / "actual.json"
    actual_path.write_text(json.dumps(actual_first, indent=2, default=str), encoding="utf-8")

    return CaseReport(
        case_id=case_id,
        actual=actual_first,
        comparison=comparison,
        duplicate_protection=duplicate_protection,
        row_count_first_run=actual_first["_row_count"],
        row_count_after_rerun=actual_after_rerun["_row_count"],
    )
