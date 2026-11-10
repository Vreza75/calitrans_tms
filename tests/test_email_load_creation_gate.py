"""Learning-phase switch: EMAIL_LOAD_CREATION_ENABLED (default false).

When off, email-driven load creation (create_load_from_inbox_item and the
application command create_load_from_work_item that delegates to it) must
refuse with a fixed message. Manual, non-email load entry (create_load_from_intake)
is unaffected.

NOTE: conftest.py enables the switch by default for the suite; these tests
override it to exercise the disabled path.
"""
from __future__ import annotations

import inspect

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import CommandFailedError, ValidationError


def _dispatcher() -> AuthenticatedActor:
    return AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)


def test_email_load_creation_enabled_reads_flag_default_false(monkeypatch):
    import config
    from services.operations_inbox_service import email_load_creation_enabled

    monkeypatch.setattr(config, "get_secret", lambda name, default=None: default)  # unset -> "false"
    assert email_load_creation_enabled() is False

    for raw in ["true", "True", "1", "yes", "on"]:
        monkeypatch.setattr(config, "get_secret", lambda name, default=None, _r=raw: _r)
        assert email_load_creation_enabled() is True
    for raw in ["false", "0", "no", "", "off"]:
        monkeypatch.setattr(config, "get_secret", lambda name, default=None, _r=raw: _r)
        assert email_load_creation_enabled() is False


def test_create_load_from_inbox_item_refused_when_disabled(monkeypatch):
    import services.operations_inbox_service as ops

    monkeypatch.setattr(ops, "email_load_creation_enabled", lambda: False)
    with pytest.raises(PermissionError) as exc:
        ops.create_load_from_inbox_item(1, {"Booking Number": "BK1"})
    assert "disabled during the learning phase" in str(exc.value)


def test_create_load_from_work_item_refused_when_disabled(monkeypatch):
    import services.operations_inbox_service as ops
    from application.loads.commands import create_load_from_work_item

    monkeypatch.setattr(ops, "email_load_creation_enabled", lambda: False)
    with pytest.raises(CommandFailedError) as exc:
        create_load_from_work_item(1, {"Booking Number": "BK1"}, actor=_dispatcher())
    assert "disabled during the learning phase" in str(exc.value)


def test_create_load_from_work_item_passes_gate_when_enabled(monkeypatch):
    """Gate ON must not block: a work item missing Booking/Reference reaches
    the normal validation (ValidationError), proving the learning-phase guard
    let it through rather than short-circuiting."""
    import services.operations_inbox_service as ops
    from application.loads.commands import create_load_from_work_item

    monkeypatch.setattr(ops, "email_load_creation_enabled", lambda: True)
    with pytest.raises(ValidationError):
        create_load_from_work_item(1, {}, actor=_dispatcher())


def test_manual_create_from_intake_is_not_gated():
    """Manual, non-email load entry (create_load_from_intake) must not consult
    the email learning-phase switch - it is unaffected by it."""
    import services.order_intake as order_intake

    src = inspect.getsource(order_intake.create_load_from_intake)
    assert "email_load_creation_enabled" not in src
    assert "EMAIL_LOAD_CREATION_ENABLED" not in src
