"""Phase 1 correction (Codex BLOCKER finding): api/dependencies.py's
get_current_actor() returned the fixed string "api" for every caller -
the API was, in effect, publicly callable with no authentication or
authorization. These tests prove the real bearer-token scheme in
api/auth.py fails closed by default and enforces roles per-route.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthenticatedActor, Role, _load_configured_tokens
from api.main import app
from application.work_items.models import (
    AttachmentSummary,
    CommunicationSummary,
    OrderDraftSummary,
    WorkItemDetail,
)


DISPATCHER_TOKEN = "test-dispatcher-token"
ACCOUNTING_TOKEN = "test-accounting-token"

TOKENS_JSON = (
    '[{"token": "test-dispatcher-token", "actor": "dispatcher@calitranscorp.com", "role": "dispatcher"},'
    ' {"token": "test-accounting-token", "actor": "accounting@calitranscorp.com", "role": "accounting"}]'
)


@pytest.fixture
def configured_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("API_AUTH_TOKENS", TOKENS_JSON)
    return TestClient(app)


@pytest.fixture
def unconfigured_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    return TestClient(app)


def _sample_detail() -> WorkItemDetail:
    return WorkItemDetail(
        id=1,
        source_sender="ops@customer.com",
        source_subject="Booking confirmation",
        source_received_at=None,
        original_email_body="body",
        parsed_data={},
        request_type="New Booking",
        matched_load_id=None,
        conversation_key="conv-1",
        review_status="Open",
        confidence_score=80,
        attachments=AttachmentSummary(current=[], prior=[]),
        communication_summary=CommunicationSummary(message_count=0, last_message_at=None),
        order_draft=OrderDraftSummary(exists=False),
        allowed_actions=[],
    )


def test_health_is_public_no_auth_required(unconfigured_client: TestClient) -> None:
    r = unconfigured_client.get("/api/v1/health")
    assert r.status_code == 200


def test_anonymous_read_returns_401_when_tokens_are_configured(configured_client: TestClient) -> None:
    r = configured_client.get("/api/v1/work-items")
    assert r.status_code == 401


def test_anonymous_write_returns_401(configured_client: TestClient) -> None:
    r = configured_client.post("/api/v1/work-items/1/close", json={"reason": "x"})
    assert r.status_code == 401


def test_no_tokens_configured_fails_closed_even_for_reads(unconfigured_client: TestClient) -> None:
    """No API_AUTH_TOKENS and no dev mode: every protected route must
    reject every request - there is no implicit "open" state."""
    r = unconfigured_client.get("/api/v1/work-items")
    assert r.status_code == 401

    r = unconfigured_client.post("/api/v1/work-items/1/close", json={"reason": "x"})
    assert r.status_code == 401


def test_garbage_bearer_token_returns_401(configured_client: TestClient) -> None:
    r = configured_client.get("/api/v1/work-items", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_malformed_authorization_header_returns_401(configured_client: TestClient) -> None:
    r = configured_client.get("/api/v1/work-items", headers={"Authorization": "not-bearer-scheme"})
    assert r.status_code == 401


def test_accounting_role_cannot_perform_dispatch_mutation(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/work-items/1/close",
        json={"reason": "x"},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_accounting_role_can_read_loads_dispatcher_cannot_be_blocked_either(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import loads as loads_router

    monkeypatch.setattr(loads_router, "list_loads", lambda status=None, limit=100: [])

    r = configured_client.get("/api/v1/loads", headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"})
    assert r.status_code == 200


def test_dispatcher_role_can_perform_permitted_mutation(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.work_items.commands import CommandResult

    captured_actor = {}

    def _fake_close(work_item_id, *, actor, reason):
        captured_actor["actor"] = actor.actor
        return CommandResult(ok=True, work_item_id=work_item_id, detail="Closed")

    monkeypatch.setattr(router_module.work_item_commands, "close_work_item", _fake_close)

    r = configured_client.post(
        "/api/v1/work-items/1/close",
        json={"reason": "handled"},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Audit event stores the authenticated actor identity, not a
    # hardcoded placeholder string.
    assert captured_actor["actor"] == "dispatcher@calitranscorp.com"


def test_dispatcher_role_can_read_work_items(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    monkeypatch.setattr(router_module, "get_work_item_detail", lambda work_item_id: _sample_detail())

    r = configured_client.get("/api/v1/work-items/1", headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"})
    assert r.status_code == 200


def test_dev_mode_bypasses_token_check_only_when_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    client = TestClient(app)

    r = client.get("/api/v1/work-items")
    # Reaches the real handler (which will hit the DB or error some other
    # way) rather than being rejected at the auth layer - proves dev mode
    # is honored, not that the whole call necessarily succeeds.
    assert r.status_code != 401
    assert r.status_code != 403


def test_accounting_role_cannot_create_load(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/work-items/1/create-load",
        json={"approved_fields": {}},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_accounting_role_cannot_update_load(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/work-items/1/update-load",
        json={"load_id": 1, "approved_fields": {}},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_accounting_role_cannot_link_load(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/work-items/1/link-load",
        json={"load_id": 1},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_accounting_role_cannot_update_draft(configured_client: TestClient) -> None:
    r = configured_client.put(
        "/api/v1/work-items/1/draft",
        json={"fields": {}},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_unauthenticated_cannot_create_load(configured_client: TestClient) -> None:
    r = configured_client.post("/api/v1/work-items/1/create-load", json={"approved_fields": {}})
    assert r.status_code == 401


def test_unauthenticated_cannot_update_draft(configured_client: TestClient) -> None:
    r = configured_client.put("/api/v1/work-items/1/draft", json={"fields": {}})
    assert r.status_code == 401


def test_dispatcher_role_can_create_load(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.loads.commands import CreateLoadResult

    monkeypatch.setattr(
        router_module,
        "create_load_from_work_item",
        lambda work_item_id, approved_fields, *, actor: CreateLoadResult(ok=True, load_id=42, review_status="Ready"),
    )

    r = configured_client.post(
        "/api/v1/work-items/1/create-load",
        json={"approved_fields": {}},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["load_id"] == 42


def test_dispatcher_role_can_update_load(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.loads.commands import UpdateLoadResult

    monkeypatch.setattr(
        router_module,
        "update_load_from_work_item",
        lambda work_item_id, load_id, approved_fields, *, actor, fill_blank_only=True: UpdateLoadResult(
            ok=True, load_id=load_id, updated_fields=["Customer"], skipped_fields=[]
        ),
    )

    r = configured_client.post(
        "/api/v1/work-items/1/update-load",
        json={"load_id": 7, "approved_fields": {"Customer": "Acme"}},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["load_id"] == 7


def test_dispatcher_role_can_link_load(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.work_items.commands import CommandResult

    monkeypatch.setattr(
        router_module.work_item_commands,
        "link_work_item_to_load",
        lambda work_item_id, load_id, actor: CommandResult(ok=True, work_item_id=work_item_id, detail="Linked"),
    )

    r = configured_client.post(
        "/api/v1/work-items/1/link-load",
        json={"load_id": 7},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_dispatcher_role_can_update_draft(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.order_drafts.commands import UpdateDraftResult

    monkeypatch.setattr(router_module, "get_work_item_detail", lambda work_item_id: _sample_detail())
    monkeypatch.setattr(
        router_module,
        "update_order_draft",
        lambda conversation_key, fields, *, actor: UpdateDraftResult(
            ok=True, conversation_key=conversation_key, updated_fields=list(fields.keys())
        ),
    )

    r = configured_client.put(
        "/api/v1/work-items/1/draft",
        json={"fields": {"Customer": "Acme"}},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_accounting_role_cannot_transition_load(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/loads/1/transition",
        json={"new_status": "Delivered"},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_accounting_role_cannot_assign_driver(configured_client: TestClient) -> None:
    r = configured_client.post(
        "/api/v1/loads/1/assign-driver",
        json={"driver": "J. Doe"},
        headers={"Authorization": f"Bearer {ACCOUNTING_TOKEN}"},
    )
    assert r.status_code == 403


def test_unauthenticated_cannot_transition_load(configured_client: TestClient) -> None:
    r = configured_client.post("/api/v1/loads/1/transition", json={"new_status": "Delivered"})
    assert r.status_code == 401


def test_dispatcher_role_can_transition_load(configured_client: TestClient, monkeypatch) -> None:
    from api.routers import loads as loads_router
    from application.loads.commands import TransitionResult

    monkeypatch.setattr(
        loads_router,
        "transition_load",
        lambda load_id, new_status, **kwargs: TransitionResult(
            ok=True, reason="", status=new_status, closeout_stage=""
        ),
    )

    r = configured_client.post(
        "/api/v1/loads/1/transition",
        json={"new_status": "Delivered"},
        headers={"Authorization": f"Bearer {DISPATCHER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "Delivered"


def test_dev_mode_value_must_be_exactly_true(monkeypatch) -> None:
    for value in ("1", "yes", "TRUE ", "enabled"):
        monkeypatch.setenv("API_AUTH_DEV_MODE", value)
        monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
        client = TestClient(app)
        r = client.get("/api/v1/work-items")
        if value.strip().lower() == "true":
            assert r.status_code != 401
        else:
            assert r.status_code == 401, f"value={value!r} should not enable dev mode"


def test_malformed_tokens_json_fails_closed_not_open(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("API_AUTH_TOKENS", "{not valid json")
    assert _load_configured_tokens() == {}


def test_token_entry_missing_required_field_is_skipped_not_granted(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKENS", '[{"token": "abc"}, {"actor": "x", "role": "admin"}]')
    tokens = _load_configured_tokens()
    assert tokens == {}


def test_token_entry_with_unknown_role_is_skipped(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKENS", '[{"token": "abc", "actor": "x", "role": "superuser"}]')
    tokens = _load_configured_tokens()
    assert tokens == {}
