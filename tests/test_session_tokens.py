from __future__ import annotations

import time

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.auth.session_tokens import issue_session_token, verify_session_token


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-do-not-use-in-prod")


def test_issue_then_verify_round_trips_the_actor() -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)

    resolved = verify_session_token(token)

    assert resolved == actor


def test_verify_rejects_tampered_payload() -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)
    payload_b64, _, signature = token.rpartition(".")

    tampered = f"{payload_b64}x.{signature}"

    assert verify_session_token(tampered) is None


def test_verify_rejects_tampered_signature() -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)
    payload_b64, _, signature = token.rpartition(".")

    tampered = f"{payload_b64}.{signature[:-1]}x"

    assert verify_session_token(tampered) is None


def test_verify_rejects_expired_token() -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor, ttl_seconds=1)

    # Wide margin (not 1.0-1.x seconds) - avoids flaking on a loaded CI/
    # dev machine where scheduler jitter can eat into a tight buffer.
    time.sleep(3)

    assert verify_session_token(token) is None


def test_verify_rejects_garbage_token() -> None:
    assert verify_session_token("not-a-real-token") is None
    assert verify_session_token("") is None


def test_issue_fails_closed_when_secret_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)

    with pytest.raises(RuntimeError):
        issue_session_token(actor)


def test_verify_returns_none_when_secret_not_configured(monkeypatch) -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)

    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    assert verify_session_token(token) is None


def test_verify_rejects_unknown_role_in_payload() -> None:
    import base64
    import json
    import time as time_module

    from application.auth import session_tokens as session_tokens_module

    payload = {"actor": "someone@calitranscorp.com", "role": "superuser", "exp": int(time_module.time()) + 3600}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    signature = session_tokens_module._sign(payload_b64, session_tokens_module._secret())
    forged_token = f"{payload_b64.decode('ascii')}.{signature}"

    assert verify_session_token(forged_token) is None
