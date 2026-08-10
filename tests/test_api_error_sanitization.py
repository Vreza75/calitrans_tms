from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import services.operations_inbox_service as operations_inbox_service
from api.main import DispatchDatabaseClient, app

_FAKE_PASSWORD = "Sup3rS3cret!"
_FAKE_DSN_ERROR = RuntimeError(
    f"(psycopg2.OperationalError) could not connect to "
    f"postgresql://tms_app:{_FAKE_PASSWORD}@db.internal.example.com:5432/calitrans"
)


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def test_create_load_command_failure_does_not_leak_password_in_response(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise _FAKE_DSN_ERROR

    monkeypatch.setattr(operations_inbox_service, "create_load_from_inbox_item", _raise)

    response = client.post(
        "/api/v1/work-items/1/create-load",
        json={"approved_fields": {"Booking Number": "BK123"}},
    )

    assert response.status_code == 400
    body = response.json()
    assert _FAKE_PASSWORD not in response.text
    assert body["error"]["code"] == "COMMAND_FAILED"


def test_legacy_create_load_failure_does_not_leak_password_in_response(client, monkeypatch):
    def _raise(self, row_data):
        raise _FAKE_DSN_ERROR

    monkeypatch.setattr(DispatchDatabaseClient, "add_row", _raise)

    response = client.post(
        "/loads",
        json={"booking_number": "BK123"},
    )

    assert response.status_code == 400
    assert _FAKE_PASSWORD not in response.text
