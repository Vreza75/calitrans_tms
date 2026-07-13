import pandas as pd
import pytest

from services import dispatch_transition_service as svc


class _FakeDb:
    def __init__(self, load: dict):
        self.load = dict(load)
        self.update_calls = []
        self.closeout_calls = []
        self.audit_notes = []

    def read_load(self, load_id: int) -> pd.DataFrame:
        return pd.DataFrame([self.load])

    def update_row_fields(self, load_id: int, updates: dict) -> None:
        self.update_calls.append(dict(updates))
        self.load.update(updates)

    def set_closeout_stage(self, load_id: int, closeout_stage: str) -> None:
        self.closeout_calls.append(closeout_stage)
        self.load["closeout_stage"] = closeout_stage

    def insert_assignment_audit(self, load_id: int, current_status: str, notes: str) -> None:
        self.audit_notes.append(notes)


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


def _wire(fake, monkeypatch):
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.set_closeout_stage)
    monkeypatch.setattr(svc, "_insert_assignment_audit", fake.insert_assignment_audit)


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
