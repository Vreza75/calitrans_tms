"""Operations Inbox certification harness.

Runs one fixture email through the real intake pipeline
(services.operations_inbox_service.sync_operations_email_engine) against a
disposable scratch Postgres database, then compares the resulting
order_intake row against a hand-approved expected.json.

Safety model (same shape as tests/test_migration_runner.py's
MIGRATION_TEST_DATABASE_URL gate): this module NEVER reads the app's
configured DATABASE_URL. It only accepts a URL from the
INBOX_CERTIFICATION_DATABASE_URL environment variable (or an explicit
argument). require_scratch_database_url() below parses that URL, requires
its database name to carry an unmistakable non-production marker, and
requires a second env var (INBOX_CERTIFICATION_DATABASE_IDENTITY) to
exactly acknowledge the sanitized host[:port]/database identity being
targeted before any destructive operation runs - see that function's own
docstring for why this does not compare against config.get_secret
("DATABASE_URL") (CTMS-010: conftest.py's session-wide pytest guard makes
that call return None for the whole session, which would make an equality
check against it permanently unable to fire).
"""
from __future__ import annotations

import email
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from unittest import mock

import yaml
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "operations_inbox"
ENV_VAR = "INBOX_CERTIFICATION_DATABASE_URL"
IDENTITY_ENV_VAR = "INBOX_CERTIFICATION_DATABASE_IDENTITY"

# Same convention as scripts/sample_data.py's SAFE_NAME_TOKENS (reused, not
# duplicated as a competing policy - kept as a separate literal here only
# because scripts/sample_data.py is not guaranteed to be importable from
# every context that imports this harness). "cert"/"certification" covers
# this harness's own documented scratch database name (calitrans_inbox_cert
# - see docs/operations_inbox_certification/README.md).
SAFE_NAME_TOKENS = {
    "dev", "development", "test", "testing", "qa", "sandbox", "scratch",
    "disposable", "cert", "certification", "ci",
}
REJECTED_DATABASE_NAMES = {"postgres", "production", "prod"}

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
    "full_return_terminal",
    "dates",
    "references",
    "missing_required_fields",
    "requires_human_review",
]


class CertificationSafetyError(RuntimeError):
    """Raised when the harness would otherwise risk touching a non-scratch database."""


class MissingScratchDatabaseError(RuntimeError):
    """Raised when INBOX_CERTIFICATION_DATABASE_URL is not set."""


def target_identity(database_url: str) -> str:
    """Sanitized host[:port]/database identity - never includes a username
    or password. Same shape as scripts/sample_data.py's target_identity()."""
    parsed = make_url(database_url)
    host = (parsed.host or "local").lower()
    port = f":{parsed.port}" if parsed.port else ""
    database = (parsed.database or "").strip("/")
    return f"{host}{port}/{database}"


def require_scratch_database_url(explicit_url: str | None = None) -> str:
    """Fail-closed gate for every certification/integration test that would
    otherwise TRUNCATE real tables (see RESETTABLE_TABLES / scratch_database()
    below).

    Deliberately does NOT compare the target against
    config.get_secret("DATABASE_URL") as its safety control (that was this
    function's original design and is CTMS-010: conftest.py's session-wide
    pytest guard - see its own docstring - makes that call return None for
    the whole pytest session, which made the equality check permanently
    unable to fire for any test collected under this repository's root
    conftest.py, i.e. always, since every caller of this function is a
    pytest test). Instead, mirroring scripts/sample_data.py's
    assert_safe_target(): the URL is parsed (never partially trusted from a
    raw string), the database name itself must carry an unmistakable
    non-production marker, generic/production-shaped names are rejected
    outright, and the operator must separately set
    INBOX_CERTIFICATION_DATABASE_IDENTITY to the exact sanitized
    host[:port]/database identity being targeted - a positive
    acknowledgement, not a guess - before any destructive operation runs.
    Every raised message below is built only from the sanitized identity or
    generic text; the raw URL, username, and password are never included.
    """
    url = explicit_url or os.environ.get(ENV_VAR)
    if not url:
        raise MissingScratchDatabaseError(
            f"{ENV_VAR} is not set. Point it at a disposable, empty PostgreSQL "
            "database before running Operations Inbox certification cases. "
            "This is never read from .streamlit/secrets.toml or DATABASE_URL."
        )

    try:
        parsed = make_url(url)
    except Exception:
        raise CertificationSafetyError(
            f"{ENV_VAR} could not be parsed as a database URL. Refusing to run."
        ) from None

    database = (parsed.database or "").strip("/").lower()
    host = (parsed.host or "").lower()
    if not database or not host:
        raise CertificationSafetyError(
            f"{ENV_VAR} must identify both a database host and a database name."
        )
    if database in REJECTED_DATABASE_NAMES:
        raise CertificationSafetyError(
            "Generic or production-like database names are never accepted for certification."
        )

    name_tokens = {token for token in re.split(r"[^a-z0-9]+", database) if token}
    if not (name_tokens & SAFE_NAME_TOKENS):
        raise CertificationSafetyError(
            "Database name must contain an explicit dev/test/qa/sandbox/scratch/"
            "disposable/cert marker before certification tests may run against it."
        )

    identity = f"{host}{f':{parsed.port}' if parsed.port else ''}/{database}"
    expected_identity = os.environ.get(IDENTITY_ENV_VAR, "").strip().lower()
    if not expected_identity:
        raise CertificationSafetyError(
            f"{IDENTITY_ENV_VAR} must be set to the exact sanitized target identity "
            f"({identity}) - the operator must positively acknowledge the target "
            "before any destructive certification test runs."
        )
    if expected_identity != identity:
        raise CertificationSafetyError(
            f"{IDENTITY_ENV_VAR} does not match the target identity. Refusing to "
            "run against an unacknowledged database."
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
    if not expected_path.exists():
        raise FileNotFoundError(f"Case {case_id} has no expected.json - write it before running the case.")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if not expected:
        raise ValueError(
            f"Case {case_id}'s expected.json is empty - a case may not be marked Passed unless the "
            "expected output was defined before processing. Populate it (see CASE_TEMPLATE.md)."
        )

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


def _sparse(mapping: dict) -> dict:
    """Drop empty values so pickup/delivery/dates/references only report the
    fields a given case's email actually populated - keeps expected.json
    stable as new location/date concepts are added for later cases instead
    of forcing every earlier accepted case to be reshaped."""
    return {k: v for k, v in mapping.items() if v not in (None, "", [])}


def _row_parsed_data(row: dict) -> dict:
    parsed = row.get("parsed_data") or {}
    if isinstance(parsed, str):
        parsed = json.loads(parsed) if parsed else {}
    return parsed


def _row_containers_and_count(parsed: dict) -> tuple[list[str], int | None]:
    container_number = parsed.get("Container Number") or ""
    container_numbers_list = parsed.get("Container Numbers") or []
    containers = container_numbers_list or ([container_number] if container_number else [])

    # For a multi-container booking, "Container Qty" (a stated count, e.g.
    # "4 X 40HC") is known before any physical container numbers are -
    # container_count must reflect that stated quantity, not silently
    # collapse to len(containers)==0/1, per the "never default an unknown
    # quantity to 1, never treat the quantity as a container number" rule.
    try:
        container_qty = int(str(parsed.get("Container Qty") or "").strip())
    except ValueError:
        container_qty = None
    container_count = container_qty if container_qty else (len(containers) or None)
    return containers, container_count


def capture_actual_result(fixture: Fixture) -> dict:
    """Read back the order_intake row(s) created for this case and translate
    them into the expected.json schema. This mapping is intentionally
    centralized here so per-case tuning (as real cases are certified) happens
    in one place instead of scattering ad hoc field lookups.

    A case whose email contains multiple detected order blocks (CASE-010)
    produces more than one order_intake row, linked by a shared
    email_thread_id - order_numbers/containers/container_count are
    aggregated across every row from this email; the remaining fields are
    read from the first row only (intent/service_flow/customer/queue are
    expected to agree across every row from one split email)."""
    import db_client

    df = db_client.read_df(
        """
        select * from order_intake
        where email_thread_id = :message_id or source_message_id = :message_id
        order by id asc
        """,
        {"message_id": _stable_message_id(fixture.message)},
    )

    if df.empty:
        return {field_name: None for field_name in EXPECTED_SCHEMA_FIELDS} | {"_row_count": 0}

    rows = [row.to_dict() for _, row in df.iterrows()]
    primary = rows[0]
    parsed = _row_parsed_data(primary)

    order_numbers: list[str] = []
    containers: list[str] = []
    container_counts: list[int] = []
    for row in rows:
        row_parsed = _row_parsed_data(row)
        booking = row_parsed.get("Booking Number") or ""
        if booking:
            order_numbers.append(booking)
        row_containers, row_count = _row_containers_and_count(row_parsed)
        containers.extend(row_containers)
        if row_count:
            container_counts.append(row_count)

    booking_number = order_numbers[0] if order_numbers else ""
    container_count = sum(container_counts) if container_counts else None

    decision = "Create New Order"
    if primary.get("matched_load_id"):
        decision = "Update Existing Order"
    elif primary.get("llm_review_required") and "mismatch" in str(primary.get("action_required") or "").lower():
        decision = "Human Review Required"

    actual = {
        "intent": primary.get("request_type"),
        "service_flow": parsed.get("TYPE") or parsed.get("Service Flow") or None,
        "queue": primary.get("work_queue"),
        "decision": decision,
        "existing_load_match": primary.get("matched_load_id"),
        "booking_number": booking_number,
        "order_numbers": order_numbers,
        "container_count": container_count,
        "containers": containers,
        "customer": parsed.get("Customer") or None,
        "full_return_terminal": parsed.get("Full Return Terminal") or None,
        "pickup": _sparse(
            {
                "terminal": parsed.get("Port"),
                "empty_pickup": parsed.get("Empty Pickup"),
                "customer_pickup": parsed.get("Customer Pickup"),
                "customer_pickup_address": parsed.get("Customer Pickup Address"),
            }
        ),
        "delivery": _sparse(
            {
                "warehouse": parsed.get("Warehouse"),
                "address": parsed.get("Address"),
            }
        ),
        "dates": _sparse(
            {
                "delivery_need_date": parsed.get("Delivery Need Date"),
                "last_free_day": parsed.get("LFD"),
                "pickup_date": parsed.get("Pickup Date"),
                "document_cutoff": parsed.get("Document Cutoff"),
            }
        ),
        "references": _sparse(
            {
                "reference_number": parsed.get("Reference Number"),
                "container_size": parsed.get("Size"),
                "contact_name": parsed.get("Contact Name"),
                "contact_email": parsed.get("Contact Email"),
                "contact_phone": parsed.get("Contact Phone"),
            }
        ),
        "missing_required_fields": [],
        # True whenever a dispatcher decision is still pending (no order/update
        # committed yet) - AI never creates or changes operational records
        # without confirmation, so this is the general "not yet approved" gate,
        # not just the narrower low-confidence llm_review_required flag.
        "requires_human_review": any(bool(row.get("llm_review_required")) for row in rows)
        or (str(primary.get("review_status") or "") == "Open" and not primary.get("linked_load_id")),
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
