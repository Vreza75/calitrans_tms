"""Phase 10B P1 regression coverage - three confirmed Dispatch Board
defects fixed together:

1. Driver/truck pairing: assigning a driver with a blank truck used to
   silently retain whatever truck was already on the load.
2. Concurrent assignments: two dispatchers could overwrite each other's
   driver/truck assignment with no conflict detection.
3. Assignment-only status validation: assigning a driver to a load still
   at a pre-dispatch status (New, Booking Verified, Hold/Need Info)
   incorrectly failed validate_transition's operational-stage check,
   even though no status change was being requested.

Service-layer tests below reuse test_dispatch_transition_service.py's
own harness pattern (a fake DB wired in via monkeypatch.setattr on the
module's private helpers) rather than a real database - this repo's
dispatch_transition_service tests are deliberately DB-free (see that
file's own docstrings); real-DB integration tests are gated behind
INBOX_CERTIFICATION_DATABASE_URL and out of scope here (no production
or local database access for this fix).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError, ConflictError, ValidationError
from application.loads import commands
from services import dispatch_transition_service as svc


# ---------------------------------------------------------------------------
# Service-layer (apply_transition) - FIX 3: assignment-only status validation
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self, load: dict):
        self.load = dict(load)
        self.update_calls: list[dict] = []
        self.audit_notes: list[str] = []
        self.realtime_events: list[tuple] = []

    def read_load_for_update(self, load_id: int, *, conn=None) -> dict | None:
        return dict(self.load)

    def update_row_fields(self, load_id: int, updates: dict, *, conn=None, actor_display_name: str = "dispatcher") -> None:
        self.update_calls.append(dict(updates))
        self.load.update(updates)

    def set_closeout_stage(self, load_id: int, closeout_stage: str, *, conn=None) -> None:
        self.load["closeout_stage"] = closeout_stage

    def insert_assignment_audit(
        self, load_id: int, current_status: str, notes: str, *, conn=None, actor_display_name: str = "dispatcher"
    ) -> None:
        self.audit_notes.append(notes)

    def emit_status_changed_event(self, load_id: int, *, old_status: str, new_status: str, conn=None, actor: str) -> None:
        self.realtime_events.append(("load.status_changed", old_status, new_status))

    def emit_assignment_changed_event(self, load_id: int, assignment_updates: dict, *, conn=None, actor: str) -> None:
        self.realtime_events.append(("load.assignment_changed", dict(assignment_updates)))


@contextmanager
def _fake_transaction():
    yield object()


def _wire(fake: _FakeDb, monkeypatch) -> None:
    monkeypatch.setattr(svc, "_load_row_for_update", fake.read_load_for_update)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.set_closeout_stage)
    monkeypatch.setattr(svc, "_insert_assignment_audit", fake.insert_assignment_audit)
    monkeypatch.setattr(svc, "_emit_load_status_changed_event", fake.emit_status_changed_event)
    monkeypatch.setattr(svc, "_emit_load_assignment_changed_event", fake.emit_assignment_changed_event)
    monkeypatch.setattr(svc, "transaction", _fake_transaction)


def _load(status: str, **overrides) -> dict:
    base = {
        "_row_id": 1,
        "TYPE": "Import",
        "Status": status,
        "Driver Name": "",
        "Truck Assigned": "",
        "Port": "Bayport",
        "empty_return_location": "",
        "closeout_stage": "Not Started",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("status", ["New", "Booking Verified", "Hold/Need Info"])
def test_assignment_only_succeeds_on_pre_dispatch_statuses(status, monkeypatch):
    """FIX 3: these three statuses are not members of SHARED_STAGES, so
    assignment-only used to be rejected by validate_transition's
    "not a valid operational status" check - even though no status
    change was ever requested."""
    fake = _FakeDb(_load(status))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, None, driver="Joe Driver", truck="T-100")

    assert result["ok"] is True
    assert result["status"] == status  # unchanged, per "preserve existing status"
    assert {"Driver Name": "Joe Driver", "Truck Assigned": "T-100"} in fake.update_calls
    assert len(fake.audit_notes) == 1  # audit history preserved
    assert ("load.assignment_changed", {"Driver Name": "Joe Driver", "Truck Assigned": "T-100"}) in fake.realtime_events


def test_assignment_only_does_not_change_status(monkeypatch):
    fake = _FakeDb(_load("New"))
    _wire(fake, monkeypatch)

    svc.apply_transition(1, None, driver="Joe Driver", truck="T-100")

    assert {"Status": "New"} in fake.update_calls


def test_assignment_only_on_completed_load_still_requires_override(monkeypatch):
    """Regression guard: FIX 3 must not weaken the pre-existing
    Completed/Cancelled immutability rule - only the operational-stage
    membership check is bypassed for assignment-only calls, not this
    guard."""
    fake = _FakeDb(_load("Completed"))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, None, driver="Joe Driver", truck="T-100")

    assert result["ok"] is False
    assert "override" in result["reason"].lower()
    assert fake.update_calls == []


def test_assignment_only_on_completed_load_succeeds_with_override(monkeypatch):
    fake = _FakeDb(_load("Completed"))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, None, driver="Joe Driver", truck="T-100", override=True, override_reason="fixing record")

    assert result["ok"] is True


def test_real_transition_validation_is_unchanged(monkeypatch):
    """Regression guard: an actual status transition (new_status not
    None) still goes through validate_transition's full operational-
    stage/sequencing rules, unaffected by the assignment-only bypass."""
    fake = _FakeDb(_load("Ready to Dispatch"))
    _wire(fake, monkeypatch)

    # Skipping ahead without driver/truck must still fail exactly as before.
    result = svc.apply_transition(1, "At Pickup")

    assert result["ok"] is False
    assert fake.update_calls == []


def test_real_transition_still_succeeds_with_driver_and_truck(monkeypatch):
    fake = _FakeDb(_load("Ready to Dispatch"))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup", driver="Joe Driver", truck="T-100")

    assert result["ok"] is True
    assert result["status"] == "En Route to Pickup"


# ---------------------------------------------------------------------------
# Service-layer (apply_transition) - FIX 2: concurrent assignments
# ---------------------------------------------------------------------------


def test_assignment_rejected_when_expected_driver_no_longer_matches(monkeypatch):
    """Two dispatchers' stale-assignment scenario: dispatcher A loaded
    the board showing no driver assigned; dispatcher B assigned "Ana" in
    the meantime; dispatcher A's own assign-driver call (still expecting
    blank) must be rejected, not silently overwrite Ana's assignment."""
    fake = _FakeDb(_load("Ready to Dispatch", **{"Driver Name": "Ana Trucker", "Truck Assigned": "T-200"}))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(
        1, None, driver="Joe Driver", truck="T-100", expected_driver="", expected_truck=""
    )

    assert result["ok"] is False
    assert "someone else" in result["reason"].lower()
    assert fake.update_calls == []
    assert fake.realtime_events == []


def test_assignment_rejected_when_expected_truck_no_longer_matches(monkeypatch):
    fake = _FakeDb(_load("Ready to Dispatch", **{"Driver Name": "Joe Driver", "Truck Assigned": "T-999"}))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(
        1, None, driver="Joe Driver", truck="T-100", expected_driver="Joe Driver", expected_truck="T-100"
    )

    assert result["ok"] is False
    assert "someone else" in result["reason"].lower()
    assert fake.update_calls == []


def test_assignment_succeeds_when_expected_values_match_current_state(monkeypatch):
    fake = _FakeDb(_load("Ready to Dispatch", **{"Driver Name": "", "Truck Assigned": ""}))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(
        1, None, driver="Joe Driver", truck="T-100", expected_driver="", expected_truck=""
    )

    assert result["ok"] is True
    assert {"Driver Name": "Joe Driver", "Truck Assigned": "T-100"} in fake.update_calls


def test_assignment_without_expected_values_skips_the_conflict_check(monkeypatch):
    """Omitting expected_driver/expected_truck (existing callers that
    don't opt in) keeps today's behavior - no conflict check at all."""
    fake = _FakeDb(_load("Ready to Dispatch", **{"Driver Name": "Someone Else", "Truck Assigned": "T-999"}))
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, None, driver="Joe Driver", truck="T-100")

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Application-command layer (transition_load) - FIX 1 + error mapping
# ---------------------------------------------------------------------------


def _actor(role: Role = Role.DISPATCHER) -> AuthenticatedActor:
    return AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=role)


def test_transition_load_rejects_blank_truck_with_a_driver(monkeypatch):
    """FIX 1: a blank truck alongside a driver is rejected before any
    read/write - apply_transition is never even called."""
    apply_transition_mock = mock.Mock()
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    with pytest.raises(ValidationError, match="truck"):
        commands.transition_load(1, None, actor=_actor(), driver="Joe Driver", truck="")

    apply_transition_mock.assert_not_called()


def test_transition_load_rejects_whitespace_only_truck_with_a_driver(monkeypatch):
    apply_transition_mock = mock.Mock()
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    with pytest.raises(ValidationError):
        commands.transition_load(1, None, actor=_actor(), driver="Joe Driver", truck="   ")

    apply_transition_mock.assert_not_called()


def test_transition_load_allows_truck_only_reassignment_without_a_driver(monkeypatch):
    """Scoped correctly: truck-only changes (no driver supplied) are not
    affected by the driver/truck pairing rule."""
    apply_transition_mock = mock.Mock(return_value={"ok": True, "reason": "", "status": "New", "closeout_stage": "Not Started"})
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    result = commands.transition_load(1, None, actor=_actor(), driver=None, truck="T-100")

    assert result.ok is True
    apply_transition_mock.assert_called_once()


def test_unauthorized_actor_with_blank_truck_gets_authorization_error_not_validation_error(monkeypatch):
    """Authorization is checked before the driver/truck pairing rule -
    same ordering guarantee this codebase already enforces for every
    other command (see tests/test_load_commands_authorization.py)."""
    apply_transition_mock = mock.Mock()
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)
    accounting = _actor(Role.ACCOUNTING)

    with pytest.raises(AuthorizationError):
        commands.transition_load(1, None, actor=accounting, driver="Joe Driver", truck="")

    apply_transition_mock.assert_not_called()


def test_transition_load_maps_concurrency_conflict_to_conflict_error(monkeypatch):
    """FIX 2: a stale-assignment rejection from apply_transition surfaces
    through transition_load as ConflictError (409 at the API layer),
    the same mapping every other validation rejection already uses."""
    apply_transition_mock = mock.Mock(
        return_value={
            "ok": False,
            "reason": "This load's driver assignment was changed by someone else. Refresh and try again.",
            "status": "Ready to Dispatch",
            "closeout_stage": "Not Started",
        }
    )
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    with pytest.raises(ConflictError, match="someone else"):
        commands.transition_load(
            1, None, actor=_actor(), driver="Joe Driver", truck="T-100", expected_driver="", expected_truck=""
        )

    apply_transition_mock.assert_called_once()
    assert apply_transition_mock.call_args.kwargs["expected_driver"] == ""
    assert apply_transition_mock.call_args.kwargs["expected_truck"] == ""


# ---------------------------------------------------------------------------
# API layer (TestClient, dev-mode) - end-to-end through api/routers/loads.py
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    from api.main import app

    return TestClient(app)


def test_assign_driver_endpoint_rejects_blank_truck(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    apply_transition_mock = mock.Mock()
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    r = client.post("/api/v1/loads/1/assign-driver", json={"driver": "Joe Driver", "truck": ""})

    assert r.status_code == 422
    assert "truck" in r.json()["error"]["message"].lower()
    apply_transition_mock.assert_not_called()


def test_assign_driver_endpoint_rejects_missing_truck(client: TestClient, monkeypatch):
    apply_transition_mock = mock.Mock()
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    r = client.post("/api/v1/loads/1/assign-driver", json={"driver": "Joe Driver"})

    assert r.status_code == 422
    apply_transition_mock.assert_not_called()


def test_assign_driver_endpoint_returns_409_on_stale_assignment(client: TestClient, monkeypatch):
    """Two dispatchers submitting stale assignments, exercised through
    the real HTTP endpoint: the second request's expected_driver/
    expected_truck no longer match what the first request already wrote."""
    apply_transition_mock = mock.Mock(
        return_value={
            "ok": False,
            "reason": "This load's driver assignment was changed by someone else. Refresh and try again.",
            "status": "Ready to Dispatch",
            "closeout_stage": "Not Started",
        }
    )
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    r = client.post(
        "/api/v1/loads/1/assign-driver",
        json={"driver": "Joe Driver", "truck": "T-100", "expected_driver": "", "expected_truck": ""},
    )

    assert r.status_code == 409
    assert "someone else" in r.json()["error"]["message"].lower()


def test_assign_driver_endpoint_succeeds_on_pre_dispatch_status(client: TestClient, monkeypatch):
    """FIX 3 end-to-end: assigning a driver to a load still at "New"
    (not an operational stage) succeeds through the real endpoint."""
    apply_transition_mock = mock.Mock(
        return_value={"ok": True, "reason": "", "status": "New", "closeout_stage": "Not Started"}
    )
    monkeypatch.setattr("services.dispatch_transition_service.apply_transition", apply_transition_mock)

    r = client.post(
        "/api/v1/loads/1/assign-driver",
        json={"driver": "Joe Driver", "truck": "T-100"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "New"


def test_assign_driver_endpoint_still_requires_mutate_operations_role(monkeypatch):
    """Unauthorized assignment, regression: accounting still gets 403 -
    the route-level role gate runs before any of the P1 checks."""
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "acct-token", "actor": "accounting@calitranscorp.com", "role": "accounting"}]',
    )
    from api.main import app

    client = TestClient(app)
    r = client.post(
        "/api/v1/loads/1/assign-driver",
        json={"driver": "Joe Driver", "truck": "T-100"},
        headers={"Authorization": "Bearer acct-token"},
    )

    assert r.status_code == 403
