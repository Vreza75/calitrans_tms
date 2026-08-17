from __future__ import annotations

import pytest
from sqlalchemy import text

import db_client
from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError, NotFoundError
from application.work_items import commands as wic
from repositories import work_item_repo

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
ACCOUNTING = AuthenticatedActor(actor="accountant@calitranscorp.com", role=Role.ACCOUNTING)


@pytest.fixture
def sqlite_order_intake(monkeypatch, tmp_path):
    """A throwaway sqlite schema shaped like the columns
    application.work_items.commands actually touches - proves real
    commit/rollback atomicity without needing Postgres or jsonb."""
    db_path = tmp_path / "work_items_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table loads (id integer primary key)"))
        conn.execute(
            text(
                "create table order_intake ("
                "id integer primary key, case_id integer, review_status text, matched_load_id integer)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_events ("
                "id integer primary key autoincrement, case_id integer, event_type text, "
                "title text, details text, actor text)"
            )
        )
        # Phase 9: close_work_item now also records an inbox.review_status_
        # changed domain event in the same transaction (realtime/events.py::
        # publish_event) - same minimal SQLite-compat shape as
        # tests/test_outbox_repo.py's sqlite_outbox fixture.
        conn.execute(
            text(
                "create table domain_events ("
                "id integer primary key autoincrement, event_type text not null, "
                "aggregate_type text not null, aggregate_id text not null, version text, "
                "payload text not null default '{}', idempotency_key text not null unique, "
                "status text not null default 'pending', attempt_count integer not null default 0, "
                "actor text)"
            )
        )
        conn.execute(text("insert into loads (id) values (100)"))
        conn.execute(
            text("insert into order_intake (id, case_id, review_status) values (1, NULL, 'Open')")
        )
        conn.execute(
            text("insert into order_intake (id, case_id, review_status) values (2, 55, 'Open')")
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _review_status(work_item_id: int) -> str:
    df = db_client.read_df("select review_status from order_intake where id = :id", {"id": work_item_id})
    return df.iloc[0]["review_status"]


def _case_event_count(case_id: int) -> int:
    df = db_client.read_df(
        "select count(*) as n from operations_case_events where case_id = :case_id", {"case_id": case_id}
    )
    return int(df.iloc[0]["n"])


def test_close_work_item_without_a_case_just_updates_status(sqlite_order_intake) -> None:
    result = wic.close_work_item(1, actor=DISPATCHER, reason="duplicate")

    assert result.ok is True
    assert _review_status(1) == "Closed"


def test_close_work_item_with_a_case_writes_status_and_audit_together(sqlite_order_intake) -> None:
    wic.close_work_item(2, actor=DISPATCHER, reason="handled")

    assert _review_status(2) == "Closed"
    assert _case_event_count(55) == 1


def test_close_work_item_raises_not_found_for_missing_id(sqlite_order_intake) -> None:
    with pytest.raises(NotFoundError):
        wic.close_work_item(999, actor=DISPATCHER)


def test_close_work_item_denies_accounting_zero_mutation(sqlite_order_intake) -> None:
    with pytest.raises(AuthorizationError):
        wic.close_work_item(1, actor=ACCOUNTING, reason="duplicate")

    assert _review_status(1) == "Open"


def test_link_work_item_to_load_raises_not_found_for_missing_load(sqlite_order_intake) -> None:
    with pytest.raises(NotFoundError):
        wic.link_work_item_to_load(1, load_id=999999, actor=DISPATCHER)

    # Nothing should have been written - the load lookup fails before any write.
    assert _review_status(1) == "Open"


def test_link_work_item_to_load_updates_status_and_matched_load(sqlite_order_intake) -> None:
    result = wic.link_work_item_to_load(1, load_id=100, actor=DISPATCHER)

    assert result.ok is True
    assert _review_status(1) == "Attached"


def test_link_work_item_to_load_denies_accounting_zero_mutation(sqlite_order_intake) -> None:
    with pytest.raises(AuthorizationError):
        wic.link_work_item_to_load(1, load_id=100, actor=ACCOUNTING)

    assert _review_status(1) == "Open"


def test_close_work_item_audit_insert_failure_rolls_back_the_status_write(sqlite_order_intake, monkeypatch) -> None:
    def _boom(**kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(work_item_repo, "insert_case_event", _boom)

    with pytest.raises(RuntimeError):
        wic.close_work_item(2, actor=DISPATCHER, reason="handled")

    # Work item 2 has a case_id, so the audit insert runs after the status
    # write in the same transaction - a failure there must roll back the
    # status write too, not leave the item Closed with no audit trail.
    assert _review_status(2) == "Open"
    assert _case_event_count(55) == 0
