from __future__ import annotations

import application.auth.queries as auth_queries
from application.auth.models import Role
from application.auth.password import hash_password


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


def test_authenticate_user_succeeds_with_correct_credentials(monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())

    actor = auth_queries.authenticate_user("dispatcher@calitranscorp.com", "correct-password")

    assert actor is not None
    assert actor.actor == "dispatcher@calitranscorp.com"
    assert actor.role == Role.DISPATCHER


def test_authenticate_user_fails_with_wrong_password(monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user())

    assert auth_queries.authenticate_user("dispatcher@calitranscorp.com", "wrong-password") is None


def test_authenticate_user_fails_for_unknown_email(monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: None)

    assert auth_queries.authenticate_user("nobody@calitranscorp.com", "anything") is None


def test_authenticate_user_fails_for_deactivated_account(monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user(is_active=False))

    assert auth_queries.authenticate_user("dispatcher@calitranscorp.com", "correct-password") is None


def test_authenticate_user_fails_closed_for_unrecognized_stored_role(monkeypatch) -> None:
    monkeypatch.setattr(auth_queries, "get_user_by_email", lambda email: _stub_user(role="superuser"))

    assert auth_queries.authenticate_user("dispatcher@calitranscorp.com", "correct-password") is None


def test_authenticate_user_rejects_empty_email_or_password_without_querying(monkeypatch) -> None:
    def _fail_if_called(email):
        raise AssertionError("must not query the database for empty credentials")

    monkeypatch.setattr(auth_queries, "get_user_by_email", _fail_if_called)

    assert auth_queries.authenticate_user("", "password") is None
    assert auth_queries.authenticate_user("email@calitranscorp.com", "") is None
