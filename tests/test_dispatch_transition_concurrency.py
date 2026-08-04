"""Real concurrency test for the Phase 1 correction to
dispatch_transition_service.apply_transition(): the row is now read with
SELECT ... FOR UPDATE inside the same transaction the write happens in,
so a second concurrent apply_transition() on the same load blocks until
the first commits, instead of validating against a stale pre-transition
status.

Gated behind MIGRATION_TEST_DATABASE_URL - the same disposable-database
opt-in convention as tests/test_migration_runner.py. Never touches the
app's configured DATABASE_URL.
"""
from __future__ import annotations

import os
import threading
import time

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
                values (1, 'Import', 'Ready to Dispatch', 'Bayport', '', '', 'CONCURRENCY-TEST-1')
                """
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_concurrent_transition_blocks_until_first_commits_then_sees_fresh_status(scratch_db):
    """Thread A locks load 1, advances it Ready to Dispatch -> En Route to
    Pickup, holds the transaction open briefly, then commits. Thread B
    calls the real apply_transition() targeting 'At Pickup' (which
    requires having already passed through 'En Route to Pickup') at
    roughly the same time. If the row read isn't locked, thread B can
    read the pre-transition 'Ready to Dispatch' status and incorrectly
    reject the move as skipping a stage. With FOR UPDATE, thread B blocks
    until thread A commits and correctly sees 'En Route to Pickup'."""
    import db_client
    from services.dispatch_transition_service import apply_transition

    thread_a_locked = threading.Event()
    thread_b_result: dict = {}

    def thread_a():
        with db_client.transaction() as conn:
            conn.execute(text("select * from loads where id = 1 for update"))
            conn.execute(
                text(
                    "update loads set status = 'En Route to Pickup', "
                    "driver_name = 'Alex', truck_assigned = 'T1' where id = 1"
                )
            )
            thread_a_locked.set()
            time.sleep(1.0)
            # commits on context exit

    def thread_b():
        thread_a_locked.wait(timeout=5)
        # Give thread A a moment to actually be inside its sleep holding
        # the lock before thread B's read races in.
        time.sleep(0.1)
        thread_b_result["value"] = apply_transition(1, "At Pickup")

    t_a = threading.Thread(target=thread_a)
    t_b = threading.Thread(target=thread_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert thread_b_result["value"]["ok"] is True, thread_b_result["value"]
    assert thread_b_result["value"]["status"] == "At Pickup"
