import pandas as pd
import pytest

from services import dispatch_transition_service as svc


class _FakeDb:
    """In-memory stand-in for DispatchDatabaseClient + db_client.read_df/execute,
    scoped to exactly what apply_transition needs."""

    def __init__(self, load: dict):
        self.load = dict(load)
        self.update_calls = []
        self.executed_sql = []

    def read_load(self, load_id: int) -> pd.DataFrame:
        return pd.DataFrame([self.load])

    def update_row_fields(self, load_id: int, updates: dict) -> None:
        self.update_calls.append(dict(updates))
        self.load.update(updates)

    def execute_closeout(self, load_id: int, closeout_stage: str) -> None:
        self.executed_sql.append(closeout_stage)
        self.load["closeout_stage"] = closeout_stage


@pytest.fixture
def import_load():
    return {
        "_row_id": 1,
        "TYPE": "Import",
        "Status": "Ready to Dispatch",
        "Driver Name": "Alex",
        "Truck Assigned": "T1",
        "Port": "Bayport",
        "closeout_stage": "Not Started",
    }


def test_valid_transition_updates_status_and_calls_update_row_fields(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", note="dispatcher confirmed")

    assert result["ok"] is True
    assert fake.update_calls[0]["Status"] == "Driver Assigned"


def test_invalid_transition_does_not_call_update_row_fields(import_load, monkeypatch):
    import_load["Driver Name"] = ""
    import_load["Truck Assigned"] = ""
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned")

    assert result["ok"] is False
    assert fake.update_calls == []


def test_reaching_completion_milestone_sets_closeout_stage_to_pod_needed(monkeypatch):
    load = {
        "_row_id": 2,
        "TYPE": "Export",
        "Status": "At Port",
        "Driver Name": "Sam",
        "Truck Assigned": "T2",
        "Port": "Barbours Cut",
        "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(2, "In-Gated")
    assert result["ok"] is True

    result2 = svc.apply_transition(2, "Dispatch Complete")
    assert result2["ok"] is True
    assert fake.executed_sql == ["POD Needed"]


def test_closeout_stage_not_overwritten_if_already_past_not_started(monkeypatch):
    load = {
        "_row_id": 3,
        "TYPE": "Local Import",
        "Status": "At Destination Warehouse",
        "Driver Name": "Sam",
        "Truck Assigned": "T2",
        "Warehouse": "Origin WH",
        "closeout_stage": "POD Received",
    }
    fake = _FakeDb(load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    fake.load["Status"] = "Delivered"
    result = svc.apply_transition(3, "Dispatch Complete")
    assert result["ok"] is True
    assert fake.executed_sql == []


def test_override_allows_backward_transition_with_reason(import_load, monkeypatch):
    import_load["Status"] = "At Port"
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", override=True, override_reason="dispatcher correction")

    assert result["ok"] is True
    assert "override: dispatcher correction" in fake.update_calls[0]["Dispatcher Notes"].lower()


def test_override_true_without_reason_is_rejected(import_load, monkeypatch):
    import_load["Status"] = "At Port"
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", override=True, override_reason="")

    assert result["ok"] is False
    assert fake.update_calls == []
