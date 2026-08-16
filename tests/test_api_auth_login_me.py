"""Phase 10 browser login bridge: POST /api/v1/auth/login and
GET /api/v1/me. Mirrors tests/test_api_auth.py's fixture style (real
TestClient, monkeypatched application-layer functions - no live DB)."""
from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import api.rate_limit as rate_limit
import application.auth.queries as auth_queries
from api.main import app
from application.auth.models import AuthenticatedActor, Role
from application.auth.password import hash_password


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-do-not-use-in-prod")
    # api.rate_limit's attempt log is module-level, in-process state (by
    # design - see its docstring) - reset it per test so this file's
    # several same-email login calls against TestClient's fixed host
    # don't trip the real limiter across unrelated test cases.
    monkeypatch.setattr(rate_limit, "_attempts", defaultdict(list))
    return TestClient(app)


def _stub_user(**overrides):
    user = {
        "id": 1,
        "email": "dispatcher@calitranscorp.com",
        "display_name": "Dee Dispatcher",
        "password_hash": hash_password("correct-password"),
        "role": "dispatcher",
        "is_active": True,
    }
    user.update(overrides)
    return user


def test_login_succeeds_with_correct_credentials_and_returns_usable_token(client: TestClient, monkeypatch) -> None:
    import api.routers.auth as auth_router

    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())
    monkeypatch.setattr(auth_router, "get_user_by_email", lambda email: _stub_user())

    r = client.post(
        "/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "correct-password"}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["actor"] == "dispatcher@calitranscorp.com"
    assert body["role"] == "dispatcher"
    assert body["token"]

    me_response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me_response.status_code == 200
    me_body = me_response.json()
    assert me_body["email"] == "dispatcher@calitranscorp.com"
    assert me_body["role"] == "dispatcher"
    assert "load:view" in me_body["permissions"]
    assert "user:admin" not in me_body["permissions"]


def test_login_fails_with_wrong_password(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())

    r = client.post("/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "wrong"})

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_login_fails_for_unknown_email(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: None)

    r = client.post("/api/v1/auth/login", json={"email": "nobody@calitranscorp.com", "password": "anything"})

    assert r.status_code == 401


def test_login_rate_limits_after_repeated_attempts(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())

    for _ in range(rate_limit._MAX_ATTEMPTS):
        r = client.post(
            "/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "wrong"}
        )
        assert r.status_code == 401

    r = client.post("/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "wrong"})

    assert r.status_code == 429
    body = r.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]


def test_login_rate_limit_is_scoped_per_email_not_shared_globally(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())

    for _ in range(rate_limit._MAX_ATTEMPTS):
        client.post("/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "wrong"})

    # A different email from the same client host is not blocked by the
    # other email's exhausted attempt count.
    r = client.post(
        "/api/v1/auth/login", json={"email": "someone-else@calitranscorp.com", "password": "correct-password"}
    )
    assert r.status_code in (200, 401)
    assert r.status_code != 429


def test_login_fails_for_inactive_account(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user(is_active=False))

    r = client.post(
        "/api/v1/auth/login", json={"email": "dispatcher@calitranscorp.com", "password": "correct-password"}
    )

    assert r.status_code == 401


def test_me_requires_authentication(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401


def test_me_rejects_garbage_token(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_me_falls_back_to_actor_identity_when_no_app_users_row(client: TestClient, monkeypatch) -> None:
    """Dev-mode / static API_AUTH_TOKENS actors have no app_users row -
    /me must still respond, using the actor identity as display_name."""
    import api.routers.auth as auth_router

    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    monkeypatch.setattr(auth_router, "get_user_by_email", lambda email: None)

    r = client.get("/api/v1/me")

    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "dev-mode"
    assert body["display_name"] == "dev-mode"
    assert body["role"] == "admin"
    assert set(body["permissions"]) == {p.value for p in __import__("application.auth.permissions", fromlist=["Permission"]).Permission}


def test_me_returns_full_permission_set_for_admin_role(client: TestClient, monkeypatch) -> None:
    import api.routers.auth as auth_router
    from application.auth.session_tokens import issue_session_token

    monkeypatch.setattr(auth_router, "get_user_by_email", lambda email: None)
    token = issue_session_token(AuthenticatedActor(actor="admin@calitranscorp.com", role=Role.ADMIN))

    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert "user:admin" in r.json()["permissions"]
