"""Structural tests for scripts/run_migrations.py and scripts/verify_schema.py
that don't require a database connection, plus a small set of real-DB tests
gated behind MIGRATION_TEST_DATABASE_URL - an explicit opt-in env var that
must point at a scratch/throwaway database. These never touch the app's
configured DATABASE_URL; they're skipped entirely unless that env var is set.
"""
import io
import os
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from scripts.run_migrations import DATABASE_DIR, MIGRATION_ORDER, _mask, run
from scripts.verify_schema import _parse_expected_schema

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


def test_every_migration_order_entry_exists_on_disk():
    for filename in MIGRATION_ORDER:
        assert (DATABASE_DIR / filename).exists(), f"{filename} listed in MIGRATION_ORDER but missing on disk"


def test_every_sql_file_on_disk_is_in_migration_order():
    on_disk = {p.name for p in DATABASE_DIR.glob("*.sql")}
    assert on_disk == set(MIGRATION_ORDER), (
        "database/*.sql and MIGRATION_ORDER have drifted apart: "
        f"on disk but unlisted={on_disk - set(MIGRATION_ORDER)}, "
        f"listed but missing={set(MIGRATION_ORDER) - on_disk}"
    )


def test_dependent_migrations_come_after_what_they_depend_on():
    order = {name: i for i, name in enumerate(MIGRATION_ORDER)}
    # order_intake_migration.sql creates order_intake; anything that ALTERs
    # or FKs to it must come after.
    assert order["order_intake_migration.sql"] < order["operations_email_workflow_migration.sql"]
    assert order["order_intake_migration.sql"] < order["multi_container_migration.sql"]
    assert order["order_intake_migration.sql"] < order["operations_fast_triage_migration.sql"]
    # dispatch_messages is created by operations_email_workflow_migration.sql.
    assert order["operations_email_workflow_migration.sql"] < order["communications_foundation_migration.sql"]
    assert order["operations_email_workflow_migration.sql"] < order["dispatcher_workspace_migration.sql"]
    # schema.sql creates loads/drivers before anything ALTERs them.
    assert order["schema.sql"] < order["portpro_style_migration.sql"]
    assert order["schema.sql"] < order["drivers_master_data_migration.sql"]
    assert order["schema.sql"] < order["dispatch_closeout_migration.sql"]
    assert order["schema.sql"] < order["multi_container_migration.sql"]


def test_mask_redacts_credentials_but_keeps_host_and_db():
    masked = _mask("postgresql://user:supersecret@db.example.com:5432/calitrans")
    assert "supersecret" not in masked
    assert "user" not in masked
    assert masked == "postgresql://***@db.example.com:5432/calitrans"


def test_mask_passes_through_url_without_credentials():
    assert _mask("postgresql://localhost:5432/calitrans") == "postgresql://localhost:5432/calitrans"


def test_dry_run_lists_every_migration_and_never_opens_a_connection():
    # An unroutable host proves no connection attempt happens - dry run
    # must not even construct a working engine connection.
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = run("postgresql://nobody:nothing@0.0.0.0:1/does-not-exist", dry_run=True)
    output = buffer.getvalue()

    assert exit_code == 0
    for filename in MIGRATION_ORDER:
        assert f"WOULD RUN {filename}" in output
    assert "supersecret" not in output


def test_parse_expected_schema_finds_core_tables_and_columns():
    expected = _parse_expected_schema()

    assert "loads" in expected["tables"]
    assert "booking_number" in expected["tables"]["loads"]
    assert "status" in expected["tables"]["loads"]

    # multi_container_migration.sql's ALTER TABLE additions
    assert "parent_booking_key" in expected["tables"]["loads"]
    assert "container_sequence" in expected["tables"]["loads"]

    # order_intake_migration.sql base + operations_fast_triage_migration.sql ALTERs
    assert "order_intake" in expected["tables"]
    assert "triage_status" in expected["tables"]["order_intake"]
    assert "llm_review_required" in expected["tables"]["order_intake"]

    # order_intake has no top-level service_flow column - that only exists
    # on order_intake_drafts (multi_container_migration.sql). Locks in a bug
    # fix in scripts/data_integrity_report.py that originally queried the
    # wrong table and crashed with UndefinedColumn against the real DB.
    assert "service_flow" not in expected["tables"]["order_intake"]
    assert "service_flow" in expected["tables"]["order_intake_drafts"]

    # communications_foundation_migration.sql ALTERs dispatch_messages, created
    # by operations_email_workflow_migration.sql.
    assert "dispatch_messages" in expected["tables"]
    assert "provider" in expected["tables"]["dispatch_messages"]


def test_parse_expected_schema_finds_indexes_and_triggers():
    expected = _parse_expected_schema()

    assert expected["indexes"].get("idx_loads_booking_number") == "loads"
    assert expected["indexes"].get("idx_order_intake_triage_status") == "order_intake"
    assert "trg_loads_updated_at" in expected["triggers"]
    assert "trg_drivers_updated_at" in expected["triggers"]


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason=(
        "Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable "
        "PostgreSQL database. Never set this to the app's real DATABASE_URL."
    ),
)
class TestMigrationRunnerAgainstARealScratchDatabase:
    def test_fresh_run_applies_every_migration_then_verify_schema_passes(self):
        from scripts.verify_schema import verify

        exit_code = run(MIGRATION_TEST_DATABASE_URL)
        assert exit_code == 0

        exit_code = verify(MIGRATION_TEST_DATABASE_URL)
        assert exit_code == 0

    def test_second_run_is_idempotent_and_skips_everything(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = run(MIGRATION_TEST_DATABASE_URL)
        output = buffer.getvalue()

        assert exit_code == 0
        assert "0 newly applied" in output
        for filename in MIGRATION_ORDER:
            assert f"SKIP  {filename}" in output
