"""Regression coverage for dispatcher AI feedback persistence.

Closes the defect where services/operations_inbox_service.py::
save_operations_ai_feedback did not exist: the facade
(services/operational_ai_feedback_service.py) delegated to
ops.save_operations_ai_feedback, ops._save_operations_ai_feedback bounced
back through the facade, and the missing attribute raised AttributeError
that a bare `except: pass` silently swallowed - so no dispatcher correction
was ever written, and recent_operations_ai_feedback_examples always returned
[] (starving generate_operations_ai_suggestion's prompt).

Two tiers:
- Pure unit tests (no database): idempotency-key determinism, parameterized
  INSERT shape / field mapping captured by monkeypatching `execute`, and the
  failure-isolation wrapper (swallow + sanitized log, workflow continues).
- Real-PostgreSQL tests gated behind MIGRATION_TEST_DATABASE_URL (an explicit
  opt-in disposable database, same convention as tests/test_migration_runner.py
  and the Operations Inbox certification harness). Skipped entirely unless set;
  they NEVER read the app's configured DATABASE_URL.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from unittest import mock

import pytest
import pytest_db_guard

import services.operations_inbox_service as ops

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")

_SUGGESTION = {
    "success": True,
    "request_type": "Customer Request",
    "action_required": "Provide pickup appointment",
    "reply_body": "Hello, your container is scheduled for Tuesday.",
    "confidence_score": 82,
    "priority": "High",
}


# --------------------------------------------------------------------------
# Pure unit tests - no database connection
# --------------------------------------------------------------------------

def test_idempotency_key_is_deterministic_for_identical_input():
    key_a = ops._operations_ai_feedback_idempotency_key(
        intake_id=10,
        correction_type="reply_edited",
        final_request_type="Customer Request",
        final_action_required="Provide appointment",
        final_reply_body="See you Tuesday.",
        source_subject="RE: container ABCU1234567",
    )
    key_b = ops._operations_ai_feedback_idempotency_key(
        intake_id=10,
        correction_type="reply_edited",
        final_request_type="Customer Request",
        final_action_required="Provide appointment",
        final_reply_body="See you  Tuesday.",  # cosmetic double space only
        source_subject="RE: container ABCU1234567",
    )
    assert key_a == key_b
    assert key_a.startswith("opsfb:")


def test_idempotency_key_changes_when_final_reply_changes():
    base = dict(
        intake_id=10,
        correction_type="reply_edited",
        final_request_type="Customer Request",
        final_action_required="Provide appointment",
        source_subject="RE: container ABCU1234567",
    )
    key_a = ops._operations_ai_feedback_idempotency_key(final_reply_body="Tuesday.", **base)
    key_b = ops._operations_ai_feedback_idempotency_key(final_reply_body="Wednesday instead.", **base)
    assert key_a != key_b


def test_save_builds_parameterized_insert_with_mapped_fields():
    """The writer must use bound parameters (no value interpolation), map the
    normalized suggestion dict onto the ai_* columns, carry an idempotency
    key, and use ON CONFLICT DO NOTHING."""
    captured: dict = {}

    def _fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params

    with mock.patch.object(ops, "execute", _fake_execute):
        ops.save_operations_ai_feedback(
            intake_id=10,
            load_id=None,
            source_subject="RE: booking",
            source_sender="customer@example.com",
            ai_suggestion=_SUGGESTION,
            final_request_type="Customer Request",
            final_action_required="Provide pickup appointment",
            final_reply_body="Edited reply.",
            correction_type="reply_edited",
            feedback_notes="",
        )

    sql = captured["sql"].lower()
    params = captured["params"]
    assert "insert into operations_ai_feedback" in sql
    assert "on conflict (idempotency_key) do nothing" in sql
    # No raw value interpolation: every value travels as a bound parameter.
    assert ":intake_id" in sql and ":idempotency_key" in sql
    # Suggestion dict mapped onto ai_* columns.
    assert params["ai_request_type"] == "Customer Request"
    assert params["ai_action_required"] == "Provide pickup appointment"
    assert params["ai_reply_body"] == "Hello, your container is scheduled for Tuesday."
    assert params["ai_confidence_score"] == 82
    assert params["ai_priority"] == "High"
    # Dispatcher-final values preserved.
    assert params["final_reply_body"] == "Edited reply."
    assert params["correction_type"] == "reply_edited"
    assert params["intake_id"] == 10
    assert params["idempotency_key"].startswith("opsfb:")


def test_save_handles_bulk_path_without_suggestion():
    """The bulk cleanup call site passes ai_suggestion=None and no
    final_reply_body; ai_* columns must be NULL and correction_type preserved."""
    captured: dict = {}

    with mock.patch.object(ops, "execute", lambda sql, params=None: captured.update(params=params)):
        ops.save_operations_ai_feedback(
            intake_id=5,
            load_id=None,
            source_subject="Marketing blast",
            source_sender="noreply@spam.example",
            ai_suggestion=None,
            final_request_type="No Action",
            final_action_required="Archive",
            correction_type="bulk_rule_archive",
            feedback_notes="Bulk cleanup rule applied",
        )

    params = captured["params"]
    assert params["ai_request_type"] is None
    assert params["ai_reply_body"] is None
    assert params["ai_confidence_score"] is None
    assert params["correction_type"] == "bulk_rule_archive"


def test_wrapper_swallows_and_logs_sanitized_error(caplog):
    """_save_operations_ai_feedback must never let a persistence failure break
    the dispatcher workflow, but (unlike the old bare `except: pass`) must log
    it - without leaking the raw exception text."""
    boom = RuntimeError("connection to db failed: password=supersecret host=prod")

    with mock.patch.object(ops, "save_operations_ai_feedback", side_effect=boom):
        with caplog.at_level(logging.WARNING, logger=ops.logger.name):
            # Must NOT raise.
            ops._save_operations_ai_feedback(intake_id=1, correction_type="reply_edited")

    assert any("operations_ai_feedback persistence failed" in rec.message for rec in caplog.records)
    # Sanitized: the raw secret-bearing exception string is not logged verbatim.
    assert "supersecret" not in caplog.text


# --------------------------------------------------------------------------
# Real-PostgreSQL tests - opt-in, disposable database only
# --------------------------------------------------------------------------

requires_db = pytest.mark.skipif(
    not pytest_db_guard.category_authorized("migration"),
    reason="Set MIGRATION_TEST_DATABASE_URL to a disposable Postgres to run feedback persistence DB tests.",
)


@contextmanager
def _scratch_database(url: str):
    """Force db_client.execute/read_df onto `url` only - same technique as
    tests/integration/operations_inbox/harness.py::scratch_database. Never
    reads the app's configured DATABASE_URL."""
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


@pytest.fixture
def scratch_db():
    from scripts.run_migrations import run as run_migrations

    url = MIGRATION_TEST_DATABASE_URL
    assert run_migrations(url) == 0, "Migration run against the disposable database failed."
    with _scratch_database(url):
        # Isolate from any other case's rows. intake_id is NULL in these tests,
        # so there is no order_intake/loads FK dependency to satisfy.
        ops.execute("delete from operations_ai_feedback", {})
        yield url
        ops.execute("delete from operations_ai_feedback", {})


@requires_db
def test_db_write_then_read_round_trip(scratch_db):
    ops.save_operations_ai_feedback(
        intake_id=None,
        source_subject="RE: booking ABCU1234567",
        source_sender="customer@example.com",
        ai_suggestion=_SUGGESTION,
        final_request_type="Customer Request",
        final_action_required="Provide pickup appointment",
        final_reply_body="Your container is scheduled for Tuesday.",
        correction_type="reply_accepted",
        feedback_notes="",
    )

    examples = ops.recent_operations_ai_feedback_examples(limit=6)
    assert len(examples) == 1
    row = examples[0]
    assert row["correction_type"] == "reply_accepted"
    assert row["ai_request_type"] == "Customer Request"
    assert row["final_request_type"] == "Customer Request"
    assert row["ai_reply_body"] == "Hello, your container is scheduled for Tuesday."
    assert row["final_reply_body"] == "Your container is scheduled for Tuesday."


@requires_db
def test_db_duplicate_submission_is_idempotent(scratch_db):
    kwargs = dict(
        intake_id=None,
        source_subject="RE: booking ABCU1234567",
        source_sender="customer@example.com",
        ai_suggestion=_SUGGESTION,
        final_request_type="Customer Request",
        final_action_required="Provide pickup appointment",
        final_reply_body="Your container is scheduled for Tuesday.",
        correction_type="reply_accepted",
    )
    ops.save_operations_ai_feedback(**kwargs)
    ops.save_operations_ai_feedback(**kwargs)  # rerun / double-click

    examples = ops.recent_operations_ai_feedback_examples(limit=6)
    assert len(examples) == 1, "Identical resubmission must not create a duplicate row."


@requires_db
def test_db_distinct_edit_creates_new_row(scratch_db):
    base = dict(
        intake_id=None,
        source_subject="RE: booking ABCU1234567",
        source_sender="customer@example.com",
        ai_suggestion=_SUGGESTION,
        final_request_type="Customer Request",
        final_action_required="Provide pickup appointment",
        correction_type="reply_edited",
    )
    ops.save_operations_ai_feedback(final_reply_body="Tuesday works.", **base)
    ops.save_operations_ai_feedback(final_reply_body="Actually, Wednesday.", **base)

    examples = ops.recent_operations_ai_feedback_examples(limit=6)
    assert len(examples) == 2, "A genuinely different correction must be recorded separately."


@requires_db
def test_db_hostile_values_are_stored_literally_not_executed(scratch_db):
    """Parameterization proof: SQL metacharacters in free text are persisted
    as literal data; the table is not dropped and the value round-trips."""
    hostile = "'); drop table operations_ai_feedback; --"
    ops.save_operations_ai_feedback(
        intake_id=None,
        source_subject=hostile,
        source_sender="customer@example.com",
        ai_suggestion=None,
        final_request_type="Customer Request",
        final_action_required="Review",
        final_reply_body="ok",
        correction_type="reply_edited",
    )

    examples = ops.recent_operations_ai_feedback_examples(limit=6)
    assert len(examples) == 1
    assert examples[0]["source_subject"] == hostile
