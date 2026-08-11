"""Phase 5: command-boundary enforcement tests for the Orders/Load
Management commands added in application/loads/commands.py
(mark_load_missing_info, save_load_note, verify_load_booking,
cancel_load, update_load_fields, mark_load_ready_to_dispatch).

Every DB/SMS side effect is mocked - these tests assert on WHETHER a
mutation was attempted, not on real database state, since the point is
to prove the permission check runs (and blocks) before any side effect,
not to re-test update_row_fields itself (already covered elsewhere).
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError, ValidationError
from application.loads import commands


def _actor(role: Role, name: str = "actor") -> AuthenticatedActor:
    return AuthenticatedActor(actor=f"{name}@calitranscorp.com", role=role)


DISPATCHER = _actor(Role.DISPATCHER, "dispatcher")
MANAGER = _actor(Role.MANAGER, "manager")
ADMIN = _actor(Role.ADMIN, "admin")
ACCOUNTING = _actor(Role.ACCOUNTING, "accountant")

AUTHORIZED_ACTORS = [DISPATCHER, MANAGER, ADMIN]


@pytest.fixture
def mock_dispatch_client():
    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        yield client_cls.return_value


# ---------------------------------------------------------------------------
# 1. authorized actor succeeds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", AUTHORIZED_ACTORS, ids=lambda a: a.role.value)
def test_cancel_load_succeeds_for_authorized_roles(actor, mock_dispatch_client):
    result = commands.cancel_load(actor=actor, load_id=42, note="bad order")
    assert result.ok is True
    assert result.status == "Cancelled"
    mock_dispatch_client.update_row_fields.assert_called_once()
    call_args = mock_dispatch_client.update_row_fields.call_args
    assert call_args.args[0] == 42
    assert call_args.args[1]["Status"] == "Cancelled"
    assert call_args.kwargs["created_by"] == actor.actor


@pytest.mark.parametrize("actor", AUTHORIZED_ACTORS, ids=lambda a: a.role.value)
def test_mark_missing_info_succeeds_for_authorized_roles(actor, mock_dispatch_client):
    result = commands.mark_load_missing_info(actor=actor, load_id=1, note="need docs")
    assert result.ok is True
    mock_dispatch_client.update_row_fields.assert_called_once()


@pytest.mark.parametrize("actor", AUTHORIZED_ACTORS, ids=lambda a: a.role.value)
def test_verify_load_booking_succeeds_for_authorized_roles(actor, mock_dispatch_client):
    result = commands.verify_load_booking(actor=actor, load_id=1)
    assert result.ok is True
    assert result.status == "Booking Verified"


@pytest.mark.parametrize("actor", AUTHORIZED_ACTORS, ids=lambda a: a.role.value)
def test_save_load_note_succeeds_for_authorized_roles(actor, mock_dispatch_client):
    result = commands.save_load_note(actor=actor, load_id=1, note="called customer")
    assert result.ok is True
    mock_dispatch_client.update_row_fields.assert_called_once_with(
        1, {"Dispatcher Notes": "called customer"}, created_by=actor.actor
    )


@pytest.mark.parametrize("actor", AUTHORIZED_ACTORS, ids=lambda a: a.role.value)
def test_update_load_fields_succeeds_for_authorized_roles(actor, mock_dispatch_client):
    result = commands.update_load_fields(actor=actor, load_id=1, updates={"customer": "Acme"})
    assert result.ok is True


# ---------------------------------------------------------------------------
# 2 + 3. unauthorized actor fails, and performs ZERO mutation
# ---------------------------------------------------------------------------


def test_accounting_cannot_cancel_load_and_nothing_is_mutated(mock_dispatch_client):
    with pytest.raises(AuthorizationError):
        commands.cancel_load(actor=ACCOUNTING, load_id=1)
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_accounting_cannot_verify_booking_and_nothing_is_mutated(mock_dispatch_client):
    with pytest.raises(AuthorizationError):
        commands.verify_load_booking(actor=ACCOUNTING, load_id=1)
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_accounting_cannot_mark_missing_info_and_nothing_is_mutated(mock_dispatch_client):
    with pytest.raises(AuthorizationError):
        commands.mark_load_missing_info(actor=ACCOUNTING, load_id=1)
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_accounting_cannot_edit_load_fields_and_nothing_is_mutated(mock_dispatch_client):
    with pytest.raises(AuthorizationError):
        commands.update_load_fields(actor=ACCOUNTING, load_id=1, updates={"customer": "Acme"})
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_accounting_cannot_mark_ready_to_dispatch_and_nothing_is_mutated(mock_dispatch_client):
    with mock.patch("services.driver_sms_service.send_sms") as send_sms, mock.patch(
        "services.dispatch_data_service._insert_dispatch_message"
    ) as insert_msg:
        with pytest.raises(AuthorizationError):
            commands.mark_load_ready_to_dispatch(
                actor=ACCOUNTING,
                load_id=1,
                driver_name="Joe Driver",
                truck="T-100",
                chassis="C-200",
                phone="5551234567",
                message="You're dispatched.",
            )
    send_sms.assert_not_called()
    insert_msg.assert_not_called()
    mock_dispatch_client.update_row_fields.assert_not_called()


# ---------------------------------------------------------------------------
# 4. missing/None actor fails closed (a required keyword-only parameter -
# there is no code path where authorization is silently skipped because no
# actor was supplied).
# ---------------------------------------------------------------------------


def test_cancel_load_requires_an_actor_argument():
    with pytest.raises(TypeError):
        commands.cancel_load(load_id=1)  # missing required `actor` kwarg


def test_cancel_load_with_none_actor_raises_rather_than_silently_authorizing(mock_dispatch_client):
    with pytest.raises(AttributeError):
        commands.cancel_load(actor=None, load_id=1)
    mock_dispatch_client.update_row_fields.assert_not_called()


# ---------------------------------------------------------------------------
# 5. invalid input does not bypass authorization - the permission check
# runs before input validation, so a bad payload from an unauthorized actor
# still surfaces as AuthorizationError, not ValidationError (no
# information about what would have been valid leaks to someone who isn't
# allowed to perform the operation at all).
# ---------------------------------------------------------------------------


def test_unauthorized_actor_with_invalid_input_gets_authorization_error_not_validation_error(mock_dispatch_client):
    with pytest.raises(AuthorizationError):
        commands.update_load_fields(actor=ACCOUNTING, load_id=1, updates={})
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_authorized_actor_with_invalid_input_gets_validation_error(mock_dispatch_client):
    with pytest.raises(ValidationError):
        commands.update_load_fields(actor=DISPATCHER, load_id=1, updates={})
    mock_dispatch_client.update_row_fields.assert_not_called()


# ---------------------------------------------------------------------------
# 6. admin goes through the same enforcement path as everyone else
# ---------------------------------------------------------------------------


def test_admin_is_denied_like_anyone_else_if_a_permission_were_revoked(mock_dispatch_client, monkeypatch):
    """Proves admin's success above is because ROLE_PERMISSIONS grants it
    the permission, not a role-name shortcut - temporarily emptying
    admin's grant set makes admin fail exactly like any other role would."""
    from application.auth import permissions as permissions_module

    monkeypatch.setitem(permissions_module.ROLE_PERMISSIONS, Role.ADMIN, frozenset())
    with pytest.raises(AuthorizationError):
        commands.cancel_load(actor=ADMIN, load_id=1)
    mock_dispatch_client.update_row_fields.assert_not_called()


# ---------------------------------------------------------------------------
# Ready-to-dispatch: requires BOTH permissions; SMS/DB side effects only
# happen for a fully authorized, fully valid attempt.
# ---------------------------------------------------------------------------


def test_mark_ready_to_dispatch_succeeds_and_records_real_actor(mock_dispatch_client):
    with mock.patch("services.driver_sms_service.send_sms", return_value=(True, "SM123")) as send_sms, mock.patch(
        "services.dispatch_data_service._insert_dispatch_message"
    ) as insert_msg:
        result = commands.mark_load_ready_to_dispatch(
            actor=DISPATCHER,
            load_id=7,
            driver_name="Joe Driver",
            truck="T-100",
            chassis="C-200",
            phone="5551234567",
            message="You're dispatched.",
        )

    assert result.ok is True
    assert result.sms_sent is True
    send_sms.assert_called_once()
    insert_msg.assert_called_once()
    assert insert_msg.call_args.kwargs["sent_by"] == DISPATCHER.actor
    mock_dispatch_client.update_row_fields.assert_called_once()
    assert mock_dispatch_client.update_row_fields.call_args.kwargs["created_by"] == DISPATCHER.actor


def test_mark_ready_to_dispatch_invalid_phone_sends_no_sms_and_mutates_nothing(mock_dispatch_client):
    with mock.patch("services.driver_sms_service.send_sms") as send_sms, mock.patch(
        "services.dispatch_data_service._insert_dispatch_message"
    ) as insert_msg:
        result = commands.mark_load_ready_to_dispatch(
            actor=DISPATCHER,
            load_id=7,
            driver_name="Joe Driver",
            truck="T-100",
            chassis="C-200",
            phone="not-a-phone",
            message="You're dispatched.",
        )

    assert result.ok is False
    send_sms.assert_not_called()
    insert_msg.assert_not_called()
    mock_dispatch_client.update_row_fields.assert_not_called()


def test_mark_ready_to_dispatch_failed_sms_mutates_nothing(mock_dispatch_client):
    with mock.patch(
        "services.driver_sms_service.send_sms", return_value=(False, "Twilio error")
    ) as send_sms, mock.patch("services.dispatch_data_service._insert_dispatch_message") as insert_msg:
        result = commands.mark_load_ready_to_dispatch(
            actor=DISPATCHER,
            load_id=7,
            driver_name="Joe Driver",
            truck="T-100",
            chassis="C-200",
            phone="5551234567",
            message="You're dispatched.",
        )

    assert result.ok is False
    assert result.reason == "Twilio error"
    send_sms.assert_called_once()
    insert_msg.assert_not_called()
    mock_dispatch_client.update_row_fields.assert_not_called()
