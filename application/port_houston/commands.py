# application/port_houston/commands.py

from __future__ import annotations

"""Applying Port Houston lookup data (container/booking/PIN/appointment
records) onto a load - previously pages_app/port_houston_integration.py
called DispatchDatabaseClient().update_row_fields() directly with no
authorization check. Read/lookup operations (Test Connection, Lookup
Container/Booking, Run Lookup, Check Appointment Time Slots) are
unaffected - only the "apply this external data to the load" write path
requires Permission.PORT_DATA_APPLY.

The `_log_port_houston_event` audit-log call is lazily imported from
pages_app/port_houston_integration.py (which imports streamlit at module
top) - same established pattern as application/loads/commands.py's lazy
import of services.operations_inbox_service, so this module itself never
imports streamlit at module load time."""

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission


def apply_port_houston_data(*, actor: AuthenticatedActor, load_id: int, updates: dict[str, Any], action_type: str) -> None:
    require_permission(actor, Permission.PORT_DATA_APPLY)

    if not updates:
        return

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)

    from pages_app.port_houston_integration import _log_port_houston_event

    _log_port_houston_event(
        action_type=action_type,
        load_id=load_id,
        response_summary={"updated_fields": list(updates.keys())},
    )


def apply_port_houston_extra_columns(*, actor: AuthenticatedActor, load_id: int, updates: dict[str, Any]) -> list[str]:
    """A second, independently-enforced write path for "extra" (non-core)
    load columns applied alongside a Port Houston sync/PIN save - kept
    separate from apply_port_houston_data() because it uses a different
    column-existence-filtered write (services/pages_app/port_houston_
    integration.py::_update_load_columns_if_present, defensive against a
    column not yet present depending on migration state), not
    DispatchDatabaseClient.update_row_fields()'s allowlist. Enforces its
    own require_permission() call rather than relying on a caller having
    already checked via apply_port_houston_data() - callers that pass an
    empty core `updates` dict there (a no-op, permission never checked)
    must not slip an unauthorized extra-columns write through."""
    require_permission(actor, Permission.PORT_DATA_APPLY)

    if not updates:
        return []

    from pages_app.port_houston_integration import _update_load_columns_if_present

    return _update_load_columns_if_present(load_id, updates)
