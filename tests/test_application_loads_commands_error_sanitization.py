from __future__ import annotations

import pytest

import services.operations_inbox_service as operations_inbox_service
from application.auth.models import AuthenticatedActor, Role
from application.exceptions import CommandFailedError
from application.loads.commands import create_load_from_work_item, update_load_from_work_item

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)

_FAKE_PASSWORD = "Sup3rS3cret!"
_FAKE_DSN_ERROR = RuntimeError(
    f"(psycopg2.OperationalError) could not connect to "
    f"postgresql://tms_app:{_FAKE_PASSWORD}@db.internal.example.com:5432/calitrans"
)


def test_create_load_from_work_item_sanitizes_underlying_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise _FAKE_DSN_ERROR

    monkeypatch.setattr(operations_inbox_service, "create_load_from_inbox_item", _raise)

    with pytest.raises(CommandFailedError) as exc_info:
        create_load_from_work_item(1, {"Booking Number": "BK123"}, actor=DISPATCHER)

    assert _FAKE_PASSWORD not in str(exc_info.value)


def test_update_load_from_work_item_sanitizes_underlying_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise _FAKE_DSN_ERROR

    monkeypatch.setattr(operations_inbox_service, "update_load_from_inbox_item", _raise)

    with pytest.raises(CommandFailedError) as exc_info:
        update_load_from_work_item(1, 1, {"Booking Number": "BK123"}, actor=DISPATCHER)

    assert _FAKE_PASSWORD not in str(exc_info.value)
