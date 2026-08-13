"""Phase 7: application/inbox/commands.py::request_inbox_sync tests.

Authorization boundary and wiring (mocked DB), same pattern as
tests/test_document_lifecycle.py's attach_load_document tests, plus
idempotency-window tests for _inbox_sync_idempotency_key, same pattern as
tests/test_load_commands_authorization.py's driver-dispatch-SMS window
tests.
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
MANAGER = AuthenticatedActor(actor="manager@calitranscorp.com", role=Role.MANAGER)
ACCOUNTING = AuthenticatedActor(actor="accountant@calitranscorp.com", role=Role.ACCOUNTING)


def test_unauthorized_actor_enqueues_nothing(monkeypatch):
    """Authorization runs BEFORE any enqueue - an unauthorized actor
    triggers zero job insert, not a partial one."""
    from application.auth import permissions as permissions_module
    from application.inbox.commands import request_inbox_sync

    monkeypatch.setitem(permissions_module.ROLE_PERMISSIONS, Role.DISPATCHER, frozenset())

    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.enqueue_job"
    ) as enqueue_job:
        with pytest.raises(AuthorizationError):
            request_inbox_sync(actor=DISPATCHER)

    transaction.assert_not_called()
    enqueue_job.assert_not_called()


def test_authorized_request_enqueues_inbox_sync_job_and_returns_queued():
    conn = mock.MagicMock()
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.enqueue_job", return_value=42
    ) as enqueue_job:
        transaction.return_value.__enter__.return_value = conn

        from application.inbox.commands import request_inbox_sync

        result = request_inbox_sync(actor=DISPATCHER)

    assert result.ok is True
    assert result.job_id == 42
    assert result.status == "queued"

    enqueue_job.assert_called_once()
    assert enqueue_job.call_args.kwargs["conn"] is conn
    assert enqueue_job.call_args.kwargs["job_type"] == "inbox.sync"
    assert enqueue_job.call_args.kwargs["aggregate_type"] == "mailbox"
    assert enqueue_job.call_args.kwargs["aggregate_id"] == "primary_mailbox"
    assert enqueue_job.call_args.kwargs["idempotency_key"].startswith("inbox.sync:primary_mailbox:")
    assert enqueue_job.call_args.kwargs["actor"] == DISPATCHER.actor


def test_manager_can_request_inbox_sync_matches_shared_permission():
    """WORK_ITEM_MANAGE is shared with the other Operations Inbox
    commands (application/work_items/commands.py) - manager has it there
    too (dispatcher/manager/admin only), so this proves request_inbox_sync
    doesn't accidentally narrow that to dispatcher alone."""
    conn = mock.MagicMock()
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.enqueue_job", return_value=1
    ):
        transaction.return_value.__enter__.return_value = conn

        from application.inbox.commands import request_inbox_sync

        result = request_inbox_sync(actor=MANAGER)

    assert result.ok is True


def test_accounting_cannot_request_inbox_sync():
    """WORK_ITEM_MANAGE is dispatcher/manager/admin only - accounting has
    DOCUMENT_ATTACH/BILLING_* but not this one, unlike Phase 6B's
    attach_load_document which accounting can call."""
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.enqueue_job"
    ) as enqueue_job:
        from application.inbox.commands import request_inbox_sync

        with pytest.raises(AuthorizationError):
            request_inbox_sync(actor=ACCOUNTING)

    transaction.assert_not_called()
    enqueue_job.assert_not_called()


def test_idempotency_key_within_window_is_stable():
    from application.inbox.commands import _inbox_sync_idempotency_key

    key_a = _inbox_sync_idempotency_key(now=1_000_000.0)
    key_b = _inbox_sync_idempotency_key(now=1_000_010.0)  # 10s later, same 60s bucket
    assert key_a == key_b


def test_idempotency_key_after_window_elapses_is_a_new_key():
    from application.inbox.commands import _inbox_sync_idempotency_key

    key_a = _inbox_sync_idempotency_key(now=1_000_000.0)
    key_b = _inbox_sync_idempotency_key(now=1_000_100.0)  # 100s later, next 60s bucket
    assert key_a != key_b
