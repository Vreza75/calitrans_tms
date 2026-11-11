"""Centralized, fail-closed test-database safety policy.

This module is imported and installed ONLY by the root conftest.py, so it is
inert in production - the application's own runtime never imports it and
sqlalchemy.create_engine is untouched outside pytest (requirement: preserve
production behavior).

Why a create_engine guard and not just env/config clearing: clearing
DATABASE_URL in conftest protects db_client.get_engine(), but migration tools
(scripts/run_migrations.py, scripts/verify_schema.py,
scripts/data_integrity_report.py) and the Operations Inbox certification harness
call sqlalchemy.create_engine() DIRECTLY with an explicit URL, bypassing both
config and db_client. The only chokepoint every SQLAlchemy connection path
shares is create_engine itself, so the guard wraps THAT: during pytest every
engine creation - direct or via db_client - is validated by one policy.

Policy (fail closed):
  * sqlite:// targets are always allowed (in-memory / tmp file, no network,
    inherently disposable - this is how the unit tests get a real DB).
  * postgresql targets are allowed ONLY when ALL hold:
      - the URL's (backend, host, port, database) identity exactly matches one
        of the explicitly-approved disposable URLs
        (MIGRATION_TEST_DATABASE_URL / INBOX_CERTIFICATION_DATABASE_URL),
      - destructive access is explicitly acknowledged (ALLOW_DESTRUCTIVE_TEST_DB),
      - the target passes disposable-shape validation (PostgreSQL scheme, no
        production-shaped host, explicit test/scratch database naming unless the
        operator sets TEST_DB_ACK_GENERIC_NAME to vouch for a fixed-name
        disposable project, e.g. a scratch Supabase whose db is always
        'postgres').
  * everything else (production-shaped or synthetic postgres, any other driver)
    is rejected - a test can never silently open a connection to a real or
    unknown database, even via mid-test injection, dotenv/config reload, or a
    direct create_engine call.
"""
from __future__ import annotations

import os
import sys

import sqlalchemy
from sqlalchemy.engine import make_url

POSTGRES_BACKENDS = {"postgresql", "postgres"}

# Generic / production database names a disposable test target must NOT use
# (unless the operator explicitly acknowledges a fixed-name disposable project).
FORBIDDEN_DB_NAMES = {"postgres", "prod", "production", "main", "app", "live", "calitrans"}
# Host fragments that mark an obviously-production target - always rejected.
FORBIDDEN_HOST_SUBSTRINGS = ("prod", "production")
_TRUTHY = {"1", "true", "yes", "y", "on"}

APPROVED_URL_ENV_VARS = ("MIGRATION_TEST_DATABASE_URL", "INBOX_CERTIFICATION_DATABASE_URL")
DESTRUCTIVE_ACK_ENV_VAR = "ALLOW_DESTRUCTIVE_TEST_DB"
GENERIC_NAME_ACK_ENV_VAR = "TEST_DB_ACK_GENERIC_NAME"


class DatabaseSafetyError(RuntimeError):
    """Raised when a test tries to open a database connection the policy forbids."""


def _flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def destructive_ack() -> bool:
    return _flag(DESTRUCTIVE_ACK_ENV_VAR)


def approved_disposable_urls() -> list[str]:
    urls = []
    for var in APPROVED_URL_ENV_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            urls.append(value.strip())
    return urls


def _identity(url) -> tuple:
    u = make_url(url)
    return (u.get_backend_name(), (u.host or "").lower(), u.port, (u.database or ""))


def is_sqlite(url) -> bool:
    try:
        return make_url(url).get_backend_name() == "sqlite"
    except Exception:
        return False


def validate_disposable_postgres(url) -> None:
    """Raise DatabaseSafetyError unless `url` has a disposable PostgreSQL
    shape. Does NOT check approval/identity - see assert_engine_target_allowed."""
    u = make_url(url)
    backend = u.get_backend_name()
    if backend not in POSTGRES_BACKENDS:
        raise DatabaseSafetyError(f"PostgreSQL scheme required, got backend={backend!r}.")

    host = (u.host or "").strip()
    database = (u.database or "").strip()

    if any(sub in host.lower() for sub in FORBIDDEN_HOST_SUBSTRINGS):
        raise DatabaseSafetyError(f"refusing a production-shaped host: {host!r}.")

    if not _flag(GENERIC_NAME_ACK_ENV_VAR):
        if not database or database.lower() in FORBIDDEN_DB_NAMES:
            raise DatabaseSafetyError(
                f"refusing a generic/production database name: {database!r}. Point the test "
                f"at a scratch/test-named database, or set {GENERIC_NAME_ACK_ENV_VAR}=1 to "
                "vouch for a fixed-name disposable project."
            )
        if not any(token in database.lower() for token in ("test", "scratch")):
            raise DatabaseSafetyError(
                f"database name must denote a test/scratch database: {database!r}."
            )


def assert_engine_target_allowed(url) -> None:
    """The single gate every create_engine call passes through under pytest."""
    if url is None:
        raise DatabaseSafetyError("refusing to create an engine with no URL during pytest.")

    try:
        backend = make_url(url).get_backend_name()
    except Exception as exc:  # unparseable URL - fail closed
        raise DatabaseSafetyError(f"unparseable database URL during pytest: {exc}") from exc

    if backend == "sqlite":
        return  # disposable, no network

    if backend not in POSTGRES_BACKENDS:
        raise DatabaseSafetyError(
            f"only sqlite or an approved disposable PostgreSQL target is allowed during "
            f"pytest; refusing backend={backend!r}."
        )

    if _identity(url) not in {_identity(a) for a in approved_disposable_urls()}:
        raise DatabaseSafetyError(
            "refusing to connect: this PostgreSQL URL is not an approved disposable test "
            f"target. Set one of {APPROVED_URL_ENV_VARS} to the exact disposable URL."
        )

    if not destructive_ack():
        raise DatabaseSafetyError(
            f"refusing to connect to a real database: destructive test access not "
            f"acknowledged (set {DESTRUCTIVE_ACK_ENV_VAR}=1)."
        )

    validate_disposable_postgres(url)


# ---------------------------------------------------------------------------
# Skip helper for integration tests (proactive, before any connection attempt).
# ---------------------------------------------------------------------------

def require_disposable_database(env_var: str = "MIGRATION_TEST_DATABASE_URL", *, scope: str = ""):
    """Return an approved disposable database URL for an integration test, or
    pytest.skip with a precise reason. Routes migration/worker/outbox/realtime/
    Operations-Inbox integration tests through the ONE policy."""
    import pytest

    url = os.environ.get(env_var)
    label = f" for {scope}" if scope else ""
    if not url or not url.strip():
        pytest.skip(f"{env_var} not set{label}; requires an explicit disposable PostgreSQL database.")
    if not destructive_ack():
        pytest.skip(
            f"{DESTRUCTIVE_ACK_ENV_VAR} not set{label}; destructive database tests require "
            "explicit acknowledgement."
        )
    try:
        validate_disposable_postgres(url)
    except DatabaseSafetyError as exc:
        pytest.skip(f"{env_var} is not a valid disposable target{label}: {exc}")
    return url.strip()


# ---------------------------------------------------------------------------
# Guard installation: wrap sqlalchemy.create_engine and repoint every module
# that already did `from sqlalchemy import create_engine`.
# ---------------------------------------------------------------------------

_REAL_CREATE_ENGINE = sqlalchemy.create_engine


def _guarded_create_engine(url, *args, **kwargs):
    assert_engine_target_allowed(url)
    return _REAL_CREATE_ENGINE(url, *args, **kwargs)


_guarded_create_engine._is_db_guard = True  # type: ignore[attr-defined]


def install_engine_guard() -> None:
    """Idempotent: install the guarded create_engine everywhere. Safe to call at
    conftest import, in pytest_configure, and per test (new imports get caught)."""
    if not getattr(sqlalchemy.create_engine, "_is_db_guard", False):
        sqlalchemy.create_engine = _guarded_create_engine
    guarded = sqlalchemy.create_engine

    for module in list(sys.modules.values()):
        if module is None or getattr(module, "__name__", None) == __name__:
            continue
        try:
            bound = getattr(module, "create_engine", None)
        except Exception:
            continue
        if bound is _REAL_CREATE_ENGINE:
            try:
                setattr(module, "create_engine", guarded)
            except Exception:
                pass
