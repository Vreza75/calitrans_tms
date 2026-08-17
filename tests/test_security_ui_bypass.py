"""Phase 5 architectural proof: security no longer depends on Streamlit
page/button visibility.

Before Phase 5, pages_app/orders_management.py called
DispatchDatabaseClient().update_row_fields(...) directly from inside a
Streamlit button handler - the ONLY thing standing between an accounting
user and cancelling a booking or dispatching a driver was whether the
button happened to render on a page accounting could reach. Anyone able
to import the service/repository layer directly (a script, a REPL, a
future non-Streamlit caller, a bug in some other page) could skip the
button entirely and call the same mutation with no check at all.

This test proves the replacement: application/loads/commands.py's
mark_load_ready_to_dispatch and cancel_load enforce
application.auth.permissions.require_permission() themselves, as the
first thing they do, regardless of how they're invoked. Streamlit is
never imported anywhere in this test - the actor and the call are both
constructed directly, exactly as a future FastAPI route, AI agent, or
worker would do it.
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError
from application.loads.commands import cancel_load, mark_load_ready_to_dispatch


def test_accounting_actor_bypassing_streamlit_cannot_dispatch_a_driver():
    # 1. Create an authenticated accounting actor - a real, legitimate
    #    login, not a forged/malformed token. Accounting is intentionally
    #    NOT granted LOAD_READY_TO_DISPATCH or DRIVER_MESSAGE_SEND (see
    #    application/auth/permissions.py's ROLE_PERMISSIONS).
    accounting_actor = AuthenticatedActor(actor="accounting@calitranscorp.com", role=Role.ACCOUNTING)

    # 2. Bypass Streamlit entirely: no st.session_state, no
    #    ui_components.auth_gate, no pages_app.orders_management import at
    #    all - this test never imports streamlit.
    with mock.patch("db_client.DispatchDatabaseClient") as client_cls, mock.patch(
        "services.driver_sms_service.send_sms"
    ) as send_sms, mock.patch("services.dispatch_data_service._insert_dispatch_message") as insert_msg:
        # 3. Directly call the dispatch-oriented application command.
        with pytest.raises(AuthorizationError) as excinfo:
            mark_load_ready_to_dispatch(
                actor=accounting_actor,
                load_id=999,
                driver_name="Joe Driver",
                truck="T-100",
                chassis="C-200",
                phone="5551234567",
                message="You're dispatched - please proceed.",
            )

    # 4. AuthorizationError, naming the real role and the real permission -
    #    not a generic/opaque failure.
    assert "accounting" in str(excinfo.value)
    assert "ready_to_dispatch" in str(excinfo.value)

    # 5. Zero database mutation and zero SMS send occurred - the SMS
    #    provider and the loads table were never touched, not merely
    #    "touched but then rolled back".
    send_sms.assert_not_called()
    insert_msg.assert_not_called()
    client_cls.return_value.update_row_fields.assert_not_called()


def test_accounting_actor_bypassing_streamlit_cannot_cancel_a_booking():
    """Same proof, for the other headline operational mutation the Phase 4
    audit specifically flagged accounting as able to reach via page
    visibility alone."""
    accounting_actor = AuthenticatedActor(actor="accounting@calitranscorp.com", role=Role.ACCOUNTING)

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls:
        with pytest.raises(AuthorizationError):
            cancel_load(actor=accounting_actor, load_id=999, note="trying to cancel without permission")

    client_cls.return_value.update_row_fields.assert_not_called()


def test_dispatcher_actor_bypassing_streamlit_can_still_cancel_a_booking():
    """Symmetric check: the command boundary isn't just a blanket denial -
    an actor WITH the permission succeeds via the exact same direct,
    Streamlit-free call path."""
    dispatcher_actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)

    with mock.patch("db_client.DispatchDatabaseClient") as client_cls, mock.patch("db_client.transaction") as transaction:
        transaction.return_value.__enter__.return_value = mock.MagicMock()
        result = cancel_load(actor=dispatcher_actor, load_id=999, note="bad order, cancelling")

    assert result.ok is True
    client_cls.return_value.update_row_fields.assert_called_once()
