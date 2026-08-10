from __future__ import annotations

import datetime as dt
import os
import threading

import pytest
from sqlalchemy import event, text

import db_client
from db_client import DispatchDatabaseClient

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


@pytest.fixture
def sqlite_loads_db(monkeypatch, tmp_path):
    """Minimal sqlite schema for exercising DispatchDatabaseClient.add_row
    directly, independent of the higher-level inbox orchestration covered
    by tests/test_operations_inbox_load_creation_atomicity.py. Same
    technique as that file's sqlite_inbox_db fixture."""
    db_path = tmp_path / "add_row_compat_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table loads ("
                "id integer primary key autoincrement, type text, booking_number text, "
                "status text, source_intake_id integer)"
            )
        )
        conn.execute(
            text(
                "create unique index ux_loads_source_intake_id "
                "on loads (source_intake_id) where source_intake_id is not null"
            )
        )
        conn.execute(
            text(
                "create table status_events ("
                "id integer primary key autoincrement, load_id integer, old_status text, "
                "new_status text, notes text, created_by text)"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _loads_count() -> int:
    df = db_client.read_df("select count(*) as n from loads")
    return int(df.iloc[0]["n"])


def _status_events_count() -> int:
    df = db_client.read_df("select count(*) as n from status_events")
    return int(df.iloc[0]["n"])


# ---------------------------------------------------------------------------
# Part B: legacy (no source_intake_id) callers must see an unchanged
# return contract. The historical contract (last committed db_client.py,
# before this session's Phase 3/idempotency work) was already "an object
# exposing .id" - never a bare int - confirmed by inspecting every real
# caller (api/main.py, pages_app/documents.py, services/order_intake.py,
# database/import_sample_csv.py): all either use `.id` or discard the
# return value entirely. `.created` is a pure addition; no caller reads it
# unless it opts in by passing source_intake_id.
# ---------------------------------------------------------------------------


def test_legacy_add_row_returns_object_with_unchanged_id_semantics(sqlite_loads_db) -> None:
    client = DispatchDatabaseClient()

    created = client.add_row({"Booking Number": "LEGACY-1", "TYPE": "Import"})

    assert isinstance(created.id, int)
    assert created.id > 0
    # New attribute, additive only - must not have altered .id's meaning.
    assert created.created is True

    df = db_client.read_df("select booking_number from loads where id = :id", {"id": created.id})
    assert df.iloc[0]["booking_number"] == "LEGACY-1"


def test_legacy_add_row_calls_are_never_constrained_against_each_other(sqlite_loads_db) -> None:
    """Two ordinary (non-inbox) load creations - e.g. two manual UI entries,
    or two legacy /loads API calls - must keep succeeding independently.
    The partial unique index only governs non-null source_intake_id, so
    NULL-source legacy rows must never conflict with each other."""
    client = DispatchDatabaseClient()

    first = client.add_row({"Booking Number": "LEGACY-A", "TYPE": "Import"})
    second = client.add_row({"Booking Number": "LEGACY-B", "TYPE": "Import"})

    assert first.id != second.id
    assert first.created is True
    assert second.created is True
    assert _loads_count() == 2


# ---------------------------------------------------------------------------
# Part D: the conflict-resolution ("loser") path, tested directly against
# add_row rather than through the full inbox orchestration. Simulates "an
# earlier call already committed a load for this source_intake_id" by
# inserting that row directly via raw SQL first, then calling add_row
# again for the same source_intake_id - exactly the state a second
# transaction would observe after losing the race (see PART D analysis in
# the accompanying report: PostgreSQL's ON CONFLICT uses speculative
# insertion, which blocks a concurrent conflicting INSERT until the first
# transaction resolves, so by the time a real second transaction's INSERT
# detects a conflict, the winner is guaranteed already committed and
# visible - this test proves the resolution logic once that state exists,
# not the concurrent blocking itself, which SQLite cannot faithfully
# reproduce).
# ---------------------------------------------------------------------------


def test_add_row_resolves_to_existing_row_on_conflict_without_duplicating(sqlite_loads_db) -> None:
    client = DispatchDatabaseClient()

    winner = client.add_row({"Booking Number": "WINNER", "TYPE": "Import"}, source_intake_id=7)
    assert winner.created is True
    assert _loads_count() == 1
    assert _status_events_count() == 1

    loser = client.add_row({"Booking Number": "SHOULD-NOT-BE-USED", "TYPE": "Import"}, source_intake_id=7)

    assert loser.created is False
    assert loser.id == winner.id
    # No second loads row, and no second "Load created" status_events row
    # for a load that was never actually (re-)created.
    assert _loads_count() == 1
    assert _status_events_count() == 1

    df = db_client.read_df("select booking_number from loads where id = :id", {"id": winner.id})
    assert df.iloc[0]["booking_number"] == "WINNER"


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason=(
        "Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable "
        "PostgreSQL database. Never set this to the app's real DATABASE_URL. "
        "This is the only test in this file that proves PostgreSQL's actual "
        "MVCC/speculative-insertion behavior under real concurrency - the "
        "sqlite tests above prove the application-side resolution logic, not "
        "PostgreSQL's specific concurrent-transaction semantics."
    ),
)
class TestAddRowConcurrencyAgainstARealScratchDatabase:
    """Two genuinely separate connections/transactions racing to create a
    load for the same source_intake_id. PostgreSQL's ON CONFLICT uses
    speculative insertion: a concurrent INSERT that would conflict blocks
    until the first transaction commits or aborts, so the loser's
    subsequent SELECT is guaranteed to see the winner's committed row -
    there is no snapshot/visibility race here (unlike a naive
    check-then-insert pattern). This test proves that against a real
    server rather than relying on documentation alone."""

    def test_two_concurrent_inserts_resolve_to_one_canonical_load(self):
        from scripts.run_migrations import run as run_migrations

        exit_code = run_migrations(MIGRATION_TEST_DATABASE_URL)
        assert exit_code == 0

        engine = db_client.get_engine(MIGRATION_TEST_DATABASE_URL)

        with engine.begin() as conn:
            intake_id = conn.execute(
                text("insert into order_intake (source) values ('concurrency_test') returning id")
            ).scalar_one()

        results: dict[str, object] = {}
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def _attempt(label: str) -> None:
            try:
                barrier.wait(timeout=10)
                with engine.begin() as conn:
                    created = DispatchDatabaseClient().add_row(
                        {"Booking Number": f"RACE-{label}", "TYPE": "Import"},
                        source_intake_id=intake_id,
                        conn=conn,
                    )
                results[label] = created
            except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`, not swallowed
                errors.append(exc)

        thread_a = threading.Thread(target=_attempt, args=("A",))
        thread_b = threading.Thread(target=_attempt, args=("B",))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)

        try:
            assert not errors, f"concurrent add_row attempt(s) raised: {errors}"
            assert "A" in results and "B" in results

            canonical_ids = {results["A"].id, results["B"].id}
            assert canonical_ids == {results["A"].id}, "both attempts must resolve to the same load id"

            created_flags = {results["A"].created, results["B"].created}
            assert created_flags == {True, False}, (
                "exactly one attempt must have actually inserted the row "
                "(created=True) and the other must have resolved to it "
                "(created=False)"
            )

            with engine.connect() as conn:
                row_count = conn.execute(
                    text("select count(*) from loads where source_intake_id = :id"), {"id": intake_id}
                ).scalar_one()
            assert row_count == 1
        finally:
            with engine.begin() as conn:
                conn.execute(text("delete from status_events where load_id in (select id from loads where source_intake_id = :id)"), {"id": intake_id})
                conn.execute(text("delete from loads where source_intake_id = :id"), {"id": intake_id})
                conn.execute(text("delete from order_intake where id = :id"), {"id": intake_id})
