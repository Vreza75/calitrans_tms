# application/auth/password.py

from __future__ import annotations

"""Password hashing for the Streamlit-login user store (app_users table).
bcrypt directly (not passlib) - passlib's bcrypt backend has had version-
compatibility warnings with recent bcrypt releases and this module only
needs hash+verify, not passlib's broader multi-scheme abstraction."""

import bcrypt

_BCRYPT_MAX_PASSWORD_BYTES = 72  # bcrypt silently truncates beyond this - reject instead.


def hash_password(plain_password: str) -> str:
    if not plain_password:
        raise ValueError("Password must not be empty.")
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {_BCRYPT_MAX_PASSWORD_BYTES} bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not plain_password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/foreign hash format - never treat as a match.
        return False
