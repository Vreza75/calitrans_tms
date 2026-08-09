from __future__ import annotations

from utils.error_sanitizer import sanitize_exception_message, sanitize_message


def test_redacts_password_from_postgres_dsn():
    message = (
        "(psycopg2.OperationalError) connection to server failed: "
        "could not connect to postgresql://tms_app:Sup3rS3cret!@db.internal.example.com:5432/calitrans"
    )

    sanitized = sanitize_message(message)

    assert "Sup3rS3cret!" not in sanitized
    assert "tms_app" not in sanitized
    assert "://***@db.internal.example.com" in sanitized


def test_redacts_password_query_param():
    message = "connection failed: host=db.internal password=Sup3rS3cret! sslmode=require"

    sanitized = sanitize_message(message)

    assert "Sup3rS3cret!" not in sanitized
    assert "password=***" in sanitized


def test_redacts_bearer_token():
    message = "upstream call failed: Authorization: Bearer abc123.def456-XYZ was rejected"

    sanitized = sanitize_message(message)

    assert "abc123.def456-XYZ" not in sanitized
    assert "Bearer ***" in sanitized


def test_redacts_api_key_query_param():
    message = "request to https://api.example.com/v1?api_key=sk-live-abc123 failed with 401"

    sanitized = sanitize_message(message)

    assert "sk-live-abc123" not in sanitized
    assert "api_key=***" in sanitized


def test_leaves_ordinary_messages_untouched():
    message = "Work item 42 not found."

    assert sanitize_message(message) == message


def test_handles_empty_message():
    assert sanitize_message("") == ""


def test_sanitize_exception_message_wraps_str_exc():
    exc = RuntimeError("could not connect to postgresql://tms_app:Sup3rS3cret!@db.internal:5432/calitrans")

    sanitized = sanitize_exception_message(exc)

    assert "Sup3rS3cret!" not in sanitized
    assert "tms_app" not in sanitized
    assert "://***@db.internal" in sanitized
