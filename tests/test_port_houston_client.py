import pytest

from services import port_houston_client as phc


def _settings(**overrides):
    defaults = dict(
        base_url="https://api.example.test/v3/evp",
        auth_url="https://auth.example.test/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        operator="POHA",
        timeout_seconds=30,
    )
    defaults.update(overrides)
    return phc.PortHoustonSettings(**defaults)


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=None, ok=None):
        self.status_code = status_code
        self._payload = payload or {}
        # port_houston_client.request() treats an empty .text as "no body" and
        # returns {} before ever calling .json(), so a successful fake response
        # needs a non-empty placeholder body unless the test is only checking
        # the error path (which passes its own explicit `text`).
        self.text = text if text is not None else "non-empty-response-body"
        self.ok = ok if ok is not None else 200 <= status_code < 300

    def json(self):
        return self._payload


def test_client_raises_when_credentials_missing():
    with pytest.raises(phc.PortHoustonError):
        phc.PortHoustonClient(settings=_settings(client_id="", client_secret=""))


def test_get_token_fetches_and_caches(monkeypatch):
    phc._TOKEN_CACHE.clear()
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, {"access_token": "tok-123", "expires_in": 3600})

    monkeypatch.setattr(phc.requests, "post", fake_post)

    client = phc.PortHoustonClient(settings=_settings())
    token = client.get_token()
    assert token == "tok-123"

    # Second call should be served from cache, not a second HTTP round trip.
    token_again = client.get_token()
    assert token_again == "tok-123"
    assert len(calls) == 1


def test_get_token_raises_on_auth_failure(monkeypatch):
    phc._TOKEN_CACHE.clear()

    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResponse(401, text="invalid client_secret", ok=False)

    monkeypatch.setattr(phc.requests, "post", fake_post)

    client = phc.PortHoustonClient(settings=_settings())
    with pytest.raises(phc.PortHoustonError):
        client.get_token()


def test_safe_error_message_redacts_sensitive_fields():
    response = _FakeResponse(401, text="access_token=abc123", ok=False)
    message = phc._safe_error_message(response)
    assert "abc123" not in message
    assert "redacted" in message.lower()


def test_get_inventory_units_builds_predicate_from_container(monkeypatch):
    monkeypatch.setattr(phc.PortHoustonClient, "get_token", lambda self, force_refresh=False: "tok")
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"content": [{"unitId": "ABCD1234567"}]})

    monkeypatch.setattr(phc.requests, "request", fake_request)

    client = phc.PortHoustonClient(settings=_settings())
    result = client.get_inventory_units(container="abcd1234567")

    assert captured["method"] == "GET"
    assert captured["params"]["predicate"] == "unitId=ABCD1234567"
    assert captured["params"]["operator"] == "POHA"
    assert phc.content_records(result) == [{"unitId": "ABCD1234567"}]


def test_request_raises_port_houston_error_on_non_ok_response(monkeypatch):
    monkeypatch.setattr(phc.PortHoustonClient, "get_token", lambda self, force_refresh=False: "tok")
    monkeypatch.setattr(
        phc.requests, "request", lambda *a, **k: _FakeResponse(500, text="server error", ok=False)
    )

    client = phc.PortHoustonClient(settings=_settings())
    with pytest.raises(phc.PortHoustonError):
        client.get_bookings(booking="BK123")


def test_content_records_handles_dict_list_and_single_object():
    assert phc.content_records({"content": [{"a": 1}, {"b": 2}]}) == [{"a": 1}, {"b": 2}]
    assert phc.content_records({"content": {"a": 1}}) == [{"a": 1}]
    assert phc.content_records([{"a": 1}]) == [{"a": 1}]
    assert phc.content_records("not a dict or list") == []


def test_get_nested_returns_default_when_path_missing():
    record = {"scope": {"yard_id": "Y1"}}
    assert phc.get_nested(record, "scope.yard_id") == "Y1"
    assert phc.get_nested(record, "scope.facility_id") == ""
    assert phc.get_nested(record, "missing.path", default="n/a") == "n/a"


def test_summarize_unit_pulls_expected_fields():
    record = {
        "unitId": "ABCD1234567",
        "category": "IMPORT",
        "scope": {"yard_id": "Y1", "facility_id": "F1"},
        "routing": {"polId": "HKHKG", "pod1Id": "USHOU"},
    }
    summary = phc.summarize_unit(record)
    assert summary["Container"] == "ABCD1234567"
    assert summary["Yard"] == "Y1"
    assert summary["Facility"] == "F1"
    assert summary["POL"] == "HKHKG"
    assert summary["POD"] == "USHOU"
