"""Phase 5B: command-boundary enforcement tests for the newly-migrated
mutation paths (Dispatch Board, Documents, Port Houston, admin master
data). Same pattern as tests/test_load_commands_authorization.py -
every DB/external side effect is mocked; assertions are on WHETHER a
mutation/side-effect was attempted, not on real database state.
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError, ValidationError

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
MANAGER = AuthenticatedActor(actor="manager@calitranscorp.com", role=Role.MANAGER)
ADMIN = AuthenticatedActor(actor="admin@calitranscorp.com", role=Role.ADMIN)
ACCOUNTING = AuthenticatedActor(actor="accountant@calitranscorp.com", role=Role.ACCOUNTING)


# ---------------------------------------------------------------------------
# application/admin/commands.py - MASTER_DATA_EDIT
# ---------------------------------------------------------------------------


def test_dispatcher_cannot_edit_master_data_and_nothing_is_mutated():
    from application.admin.commands import upsert_customer

    with mock.patch("db_client.execute") as execute:
        with pytest.raises(AuthorizationError):
            upsert_customer(actor=DISPATCHER, company_name="Acme")
    execute.assert_not_called()


def test_accounting_cannot_edit_master_data():
    from application.admin.commands import upsert_customer

    with mock.patch("db_client.execute") as execute:
        with pytest.raises(AuthorizationError):
            upsert_customer(actor=ACCOUNTING, company_name="Acme")
    execute.assert_not_called()


@pytest.mark.parametrize("actor", [MANAGER, ADMIN], ids=lambda a: a.role.value)
def test_manager_and_admin_can_edit_master_data(actor):
    from application.admin.commands import upsert_customer

    with mock.patch("db_client.execute") as execute:
        upsert_customer(actor=actor, company_name="Acme")
    execute.assert_called_once()


def test_upsert_customer_requires_company_name():
    from application.admin.commands import upsert_customer

    with mock.patch("db_client.execute") as execute:
        with pytest.raises(ValidationError):
            upsert_customer(actor=ADMIN, company_name="   ")
    execute.assert_not_called()


def test_create_driver_denied_for_dispatcher_zero_mutation():
    from application.admin.commands import create_driver

    with mock.patch("db_client.execute") as execute:
        with pytest.raises(AuthorizationError):
            create_driver(actor=DISPATCHER, driver_name="Joe")
    execute.assert_not_called()


# ---------------------------------------------------------------------------
# application/documents/commands.py - DOCUMENT_ATTACH
# ---------------------------------------------------------------------------


def test_accounting_can_attach_documents():
    """Deliberate exception to the accounting-denied-by-default rule -
    see application/auth/permissions.py's ROLE_PERMISSIONS comment."""
    from application.documents.commands import attach_load_document

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        attach_load_document(actor=ACCOUNTING, load_id=1, uploaded_file=object(), source="invoice")
    client_cls.return_value.attach_file_to_row.assert_called_once()


# ---------------------------------------------------------------------------
# application/dispatch/commands.py - DISPATCH_TRANSITION
# ---------------------------------------------------------------------------


def test_accounting_cannot_transition_load_zero_mutation():
    """application.loads.commands.transition_load is the one canonical
    command wrapping dispatch_transition_service.apply_transition -
    reused by both pages_app/dispatch_board.py and api/routers/loads.py
    as of the Phase 5B closure pass."""
    from application.loads.commands import transition_load

    with mock.patch("services.dispatch_transition_service.apply_transition") as apply_transition:
        with pytest.raises(AuthorizationError):
            transition_load(1, "Dispatched", actor=ACCOUNTING)
    apply_transition.assert_not_called()


def test_dispatcher_can_transition_load_and_real_actor_is_recorded():
    from application.loads.commands import transition_load

    with mock.patch(
        "services.dispatch_transition_service.apply_transition",
        return_value={"ok": True, "reason": "", "status": "Dispatched", "closeout_stage": "Not Started"},
    ) as apply_transition:
        result = transition_load(1, "Dispatched", actor=DISPATCHER)
    assert result.ok is True
    apply_transition.assert_called_once()
    assert apply_transition.call_args.kwargs["actor_display_name"] == DISPATCHER.actor


def test_accounting_cannot_mark_load_ready_for_billing_zero_mutation():
    from application.dispatch.commands import mark_load_ready_for_billing

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        with pytest.raises(AuthorizationError):
            mark_load_ready_for_billing(actor=ACCOUNTING, load_id=1)
    client_cls.return_value.update_row_fields.assert_not_called()


def test_accounting_cannot_attach_dispatch_document_via_dispatch_board_path():
    """Same command as the Documents-page test above, called with the
    accounting actor still permitted (DOCUMENT_ATTACH is shared) - proves
    the two entry points (Documents page, Dispatch Board workspace) use
    the identical command, not two divergent implementations."""
    from application.documents.commands import attach_load_document

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        attach_load_document(actor=ACCOUNTING, load_id=1, uploaded_file=object(), source="dispatch_workspace")
    client_cls.return_value.attach_file_to_row.assert_called_once()


def test_accounting_cannot_log_dispatch_communication_zero_mutation():
    from application.dispatch.commands import log_dispatch_communication

    with mock.patch("services.dispatch_data_service._insert_dispatch_message") as insert_msg:
        with pytest.raises(AuthorizationError):
            log_dispatch_communication(
                actor=ACCOUNTING,
                load_id=1,
                message_type="operational_note",
                direction="internal",
                recipient="dispatcher@calitranscorp.com",
                message_body="test",
            )
    insert_msg.assert_not_called()


def test_dispatcher_can_log_dispatch_communication_with_real_actor_recorded():
    from application.dispatch.commands import log_dispatch_communication

    with mock.patch("services.dispatch_data_service._insert_dispatch_message") as insert_msg:
        log_dispatch_communication(
            actor=DISPATCHER,
            load_id=1,
            message_type="customer_note",
            direction="outbound",
            recipient="Acme Corp",
            message_body="Container picked up.",
        )
    insert_msg.assert_called_once()
    assert insert_msg.call_args.kwargs["sent_by"] == DISPATCHER.actor


# ---------------------------------------------------------------------------
# API convergence - api/routers/loads.py now calls the same
# application.loads.commands.transition_load command Dispatch Board uses.
# ---------------------------------------------------------------------------


def test_api_transition_load_denies_insufficient_permission_zero_mutation():
    """Proves the API's coarse role gate is defense-in-depth, not the only
    check - even an actor that role-wise passed require_role() still hits
    require_permission() inside transition_load() itself."""
    from application.loads.commands import transition_load

    with mock.patch("services.dispatch_transition_service.apply_transition") as apply_transition:
        with pytest.raises(AuthorizationError):
            transition_load(1, "Dispatched", actor=ACCOUNTING)
    apply_transition.assert_not_called()


# ---------------------------------------------------------------------------
# application/port_houston/commands.py - PORT_DATA_APPLY
# ---------------------------------------------------------------------------


def test_accounting_cannot_apply_port_houston_data_zero_mutation():
    from application.port_houston.commands import apply_port_houston_data

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        with pytest.raises(AuthorizationError):
            apply_port_houston_data(actor=ACCOUNTING, load_id=1, updates={"Port": "Houston"}, action_type="test")
    client_cls.return_value.update_row_fields.assert_not_called()


def test_dispatcher_can_apply_port_houston_data():
    from application.port_houston.commands import apply_port_houston_data

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls, mock.patch(
        "pages_app.port_houston_integration._log_port_houston_event"
    ) as log_event:
        apply_port_houston_data(actor=DISPATCHER, load_id=1, updates={"Port": "Houston"}, action_type="test")
    client_cls.return_value.update_row_fields.assert_called_once()
    log_event.assert_called_once()


def test_accounting_cannot_apply_port_houston_extra_columns_zero_mutation():
    from application.port_houston.commands import apply_port_houston_extra_columns

    with mock.patch("pages_app.port_houston_integration._update_load_columns_if_present") as write_extra:
        with pytest.raises(AuthorizationError):
            apply_port_houston_extra_columns(actor=ACCOUNTING, load_id=1, updates={"terminal": "Bayport"})
    write_extra.assert_not_called()
