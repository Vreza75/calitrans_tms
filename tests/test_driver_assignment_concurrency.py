"""Regression test for Issue 2: concurrent driver assignment must not
silently let an earlier save win over a later one.

Reproduces the manual test that found the bug: Window A loads a load, then
saves Driver A. Window B loaded the SAME load *before* A's save (so it
holds the same stale `updated_at`), then tries to save Driver B. Before
this fix, both writes went through db_client.DispatchDatabaseClient.
update_row_fields() with a blind `UPDATE ... WHERE id = :id` - whichever
write physically landed last in Postgres won, with no rejection and no
audit trail identifying the conflict.

Required contract (see application/dispatch/commands.py's
update_dispatch_assignment and db_client.py's update_row_fields):
  - A saves first: succeeds, load now shows Driver A.
  - B saves next with the stale `updated_at` it loaded before A's save:
    rejected with ConflictError - Driver A remains in the database. No
    silent overwrite.
  - If B reloads (picks up the fresh `updated_at` after A's save) and
    saves again: succeeds, load now shows Driver B - a genuinely later
    write is not blocked forever, only a *stale* one.

Gated behind MIGRATION_TEST_DATABASE_URL - same disposable-database
opt-in convention as tests/test_dispatch_transition_concurrency.py. Never
touches the app's configured DATABASE_URL.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable PostgreSQL database.",
)


@pytest.fixture
def scratch_db(monkeypatch):
    import config
    import db_client

    url = MIGRATION_TEST_DATABASE_URL
    configured = None
    try:
        configured = config.get_secret("DATABASE_URL")
    except Exception:
        configured = None
    if configured and configured.strip() == url.strip():
        pytest.fail("MIGRATION_TEST_DATABASE_URL is identical to the app's configured DATABASE_URL - refusing to run.")

    original_get_secret = db_client.get_secret

    def _forced_get_secret(name, default=None):
        if name == "DATABASE_URL":
            return url
        return original_get_secret(name, default)

    monkeypatch.setattr(db_client, "get_secret", _forced_get_secret)
    db_client._ENGINE_CACHE.pop(url, None)

    from scripts.run_migrations import run as run_migrations

    assert run_migrations(url) == 0

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("truncate table status_events, loads restart identity cascade"))
        conn.execute(
            text(
                """
                insert into loads (id, type, status, port, driver_name, truck_assigned, booking_number)
                values (1, 'Import', 'Ready to Dispatch', 'Bayport', '', '', 'CONCURRENCY-DRIVER-1')
                """
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _load_row(url: str) -> dict:
    import db_client

    with db_client.get_engine(url).connect() as conn:
        row = conn.execute(
            text("select driver_name, updated_at from loads where id = 1")
        ).mappings().first()
        return dict(row)


def test_stale_second_save_is_rejected_not_silently_overwritten(scratch_db):
    from application.auth.models import AuthenticatedActor, Role
    from application.dispatch.commands import update_dispatch_assignment
    from application.exceptions import ConflictError

    principal = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)

    # Both windows load the row before either saves - same starting
    # updated_at, exactly like two dispatchers with the same load open.
    stale_snapshot = _load_row(scratch_db)

    # Window A saves Driver A first.
    update_dispatch_assignment(
        actor=principal,
        load_id=1,
        updates={"Driver Name": "Driver A"},
        expected_updated_at=stale_snapshot["updated_at"],
    )

    after_a = _load_row(scratch_db)
    assert after_a["driver_name"] == "Driver A"
    assert after_a["updated_at"] != stale_snapshot["updated_at"]

    # Window B saves Driver B using the SAME stale updated_at it loaded
    # before A's save (it never reloaded) - must be rejected, not silently
    # overwrite Driver A.
    with pytest.raises(ConflictError):
        update_dispatch_assignment(
            actor=principal,
            load_id=1,
            updates={"Driver Name": "Driver B"},
            expected_updated_at=stale_snapshot["updated_at"],
        )

    after_b_rejected = _load_row(scratch_db)
    assert after_b_rejected["driver_name"] == "Driver A", "Stale write must not overwrite the winning save."


def test_reloaded_second_save_succeeds_and_becomes_final(scratch_db):
    """A later write is only blocked while it targets a stale
    updated_at - once Window B reloads (picks up A's fresh updated_at)
    and saves again, B legitimately becomes the final assignment."""
    from application.auth.models import AuthenticatedActor, Role
    from application.dispatch.commands import update_dispatch_assignment

    principal = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)

    stale_snapshot = _load_row(scratch_db)
    update_dispatch_assignment(
        actor=principal,
        load_id=1,
        updates={"Driver Name": "Driver A"},
        expected_updated_at=stale_snapshot["updated_at"],
    )

    fresh_snapshot = _load_row(scratch_db)
    update_dispatch_assignment(
        actor=principal,
        load_id=1,
        updates={"Driver Name": "Driver B"},
        expected_updated_at=fresh_snapshot["updated_at"],
    )

    final_row = _load_row(scratch_db)
    assert final_row["driver_name"] == "Driver B"


def test_driver_only_update_writes_an_audit_row_identifying_actor_and_change(scratch_db):
    """Previously driver/truck-only updates (no status change) left no
    status_events row at all - no actor, no old/new driver anywhere."""
    import db_client
    from application.auth.models import AuthenticatedActor, Role
    from application.dispatch.commands import update_dispatch_assignment

    principal = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    stale_snapshot = _load_row(scratch_db)

    update_dispatch_assignment(
        actor=principal,
        load_id=1,
        updates={"Driver Name": "Driver A"},
        expected_updated_at=stale_snapshot["updated_at"],
    )

    with db_client.get_engine(scratch_db).connect() as conn:
        events = conn.execute(
            text("select notes, created_by from status_events where load_id = 1 order by id")
        ).mappings().all()

    assert events, "Driver-only assignment must write an audit row."
    last_event = events[-1]
    assert "Driver A" in last_event["notes"]
    assert last_event["created_by"] == "dispatcher@calitranscorp.com"
