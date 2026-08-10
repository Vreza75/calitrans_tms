from __future__ import annotations

import pytest

from application.auth.password import hash_password, verify_password


def test_hash_then_verify_round_trips() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_wrong_password_does_not_verify() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_hash_is_never_the_plaintext() -> None:
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"
    assert "hunter2" not in hashed


def test_empty_password_rejected_at_hash_time() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_password_over_bcrypt_byte_limit_rejected_instead_of_silently_truncated() -> None:
    with pytest.raises(ValueError):
        hash_password("x" * 73)


def test_verify_returns_false_not_raise_for_empty_inputs() -> None:
    assert verify_password("", "somehash") is False
    assert verify_password("password", "") is False


def test_verify_returns_false_not_raise_for_malformed_stored_hash() -> None:
    assert verify_password("password", "not-a-real-bcrypt-hash") is False


def test_two_hashes_of_the_same_password_differ_due_to_salting() -> None:
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True
