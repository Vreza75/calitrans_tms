from contextlib import contextmanager

import pytest

from services import dispatch_transition_service as svc


class _FakeDb:
    def __init__(self, load: dict | None):
        self.load = dict(load) if load is not None else None
        self.update_calls = []
        self.closeout_calls = []
        self.audit_notes = []
        self.conns_seen = []
        self.created_by_seen = []
        self.realtime_events = []

    def read_load_for_update(self, load_id: int, *, conn=None) -> dict | None:
        self.conns_seen.append(conn)
        return dict(self.load) if self.load is not None else None

    def update_row_fields(self, load_id: int, updates: dict, *, conn=None, actor_display_name: str = "dispatcher") -> None:
        self.conns_seen.append(conn)
        self.update_calls.append(dict(updates))
        self.created_by_seen.append(actor_display_name)
        self.load.update(updates)

    def set_closeout_stage(self, load_id: int, closeout_stage: str, *, conn=None) -> None:
        self.conns_seen.append(conn)
        self.closeout_calls.append(closeout_stage)
        self.load["closeout_stage"] = closeout_stage

    def insert_assignment_audit(
        self, load_id: int, current_status: str, notes: str, *, conn=None, actor_display_name: str = "dispatcher"
    ) -> None:
        self.conns_seen.append(conn)
        self.audit_notes.append(notes)
        self.created_by_seen.append(actor_display_name)

    def emit_status_changed_event(self, load_id: int, *, old_status: str, new_status: str, conn=None, actor: str) -> None:
        self.conns_seen.append(conn)
        self.realtime_events.append(("load.status_changed", load_id, old_status, new_status))

    def emit_assignment_changed_event(self, load_id: int, assignment_updates: dict, *, conn=None, actor: str) -> None:
        self.conns_seen.append(conn)
        self.realtime_events.append(("load.assignment_changed", load_id, dict(assignment_updates)))


@pytest.fixture
def import_load():
    return {
        "_row_id": 1,
        "TYPE": "Import",
        "Status": "Ready to Dispatch",
        "Driver Name": "",
        "Truck Assigned": "",
        "Port": "Bayport",
        "empty_return_location": "",
        "closeout_stage": "Not Started",
    }


@contextmanager
def _fake_transaction():
    yield object()


def _wire(fake, monkeypatch):
    monkeypatch.setattr(svc, "_load_row_for_update", fake.read_load_for_update)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.set_closeout_stage)
    monkeypatch.setattr(svc, "_insert_assignment_audit", fake.insert_assignment_audit)
    monkeypatch.setattr(svc, "_emit_load_status_changed_event", fake.emit_status_changed_event)
    monkeypatch.setattr(svc, "_emit_load_assignment_changed_event", fake.emit_assignment_changed_event)
    monkeypatch.setattr(svc, "transaction", _fake_transaction)


def test_assign_and_start_writes_driver_truck_and_status_in_separate_calls(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup", driver="Alex", truck="T1")

    assert result["ok"] is True
    assert result["status"] == "En Route to Pickup"
    assert {"Driver Name": "Alex", "Truck Assigned": "T1"} in fake.update_calls
    assert {"Status": "En Route to Pickup"} in fake.update_calls
    assert len(fake.audit_notes) == 1
    assert "Alex" in fake.audit_notes[0]


def test_assignment_status_and_audit_share_one_transaction(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    svc.apply_transition(1, "En Route to Pickup", driver="Alex", truck="T1")

    # locked row read + assignment write + assignment audit +
    # assignment_changed event + status write + status_changed event
    # (Phase 9 STEP 6: both realtime events recorded in the same
    # transaction as the writes they describe)
    assert len(fake.conns_seen) == 6
    assert len(set(id(c) for c in fake.conns_seen)) == 1


def test_transition_emits_status_changed_and_assignment_changed_events(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    svc.apply_transition(1, "En Route to Pickup", driver="Alex", truck="T1")

    assert ("load.assignment_changed", 1, {"Driver Name": "Alex", "Truck Assigned": "T1"}) in fake.realtime_events
    assert ("load.status_changed", 1, "Ready to Dispatch", "En Route to Pickup") in fake.realtime_events


def test_invalid_transition_emits_no_realtime_events(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup")

    assert result["ok"] is False
    assert fake.realtime_events == []


def test_forced_failure_mid_transaction_rolls_back_status_write(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row_for_update", fake.read_load_for_update)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.set_closeout_stage)

    def _boom(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(svc, "_insert_assignment_audit", _boom)
    monkeypatch.setattr(svc, "transaction", _fake_transaction)

    with pytest.raises(RuntimeError):
        svc.apply_transition(1, "En Route to Pickup", driver="Alex", truck="T1")

    # The status write must never happen once the assignment audit inside
    # the same transaction fails - proving apply_transition doesn't fall
    # back to committing partial work if a later step in the command
    # raises.
    assert {"Status": "En Route to Pickup"} not in fake.update_calls


def test_start_en_route_with_existing_driver_needs_no_assignment_write(monkeypatch):
    load = {
        "_row_id": 2, "TYPE": "Import", "Status": "Ready to Dispatch",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(2, "En Route to Pickup")

    assert result["ok"] is True
    assert fake.audit_notes == []
    assert {"Status": "En Route to Pickup"} in fake.update_calls


def test_invalid_transition_without_driver_does_not_write_anything(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup")

    assert result["ok"] is False
    assert fake.update_calls == []
    assert fake.audit_notes == []


def test_reaching_completed_sets_closeout_stage_to_pod_needed(monkeypatch):
    load = {
        "_row_id": 3, "TYPE": "Export", "Status": "At Delivery",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Barbours Cut",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(3, "Completed")

    assert result["ok"] is True
    assert fake.closeout_calls == ["POD Needed"]


def test_override_without_reason_is_rejected(monkeypatch):
    load = {
        "_row_id": 4, "TYPE": "Import", "Status": "Completed",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "POD Needed",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(4, "En Route to Pickup", override=True, override_reason="")

    assert result["ok"] is False
    assert fake.update_calls == []


def test_missing_load_returns_not_found_without_writing(monkeypatch):
    fake = _FakeDb(None)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(999, "En Route to Pickup")

    assert result["ok"] is False
    assert "not found" in result["reason"].lower()
    assert fake.update_calls == []


def test_override_with_reason_allows_backward_transition(monkeypatch):
    load = {
        "_row_id": 5, "TYPE": "Import", "Status": "At Pickup",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(5, "En Route to Pickup", override=True, override_reason="correction")

    assert result["ok"] is True
    assert any("override: correction" in u.get("Dispatcher Notes", "").lower() for u in fake.update_calls)
