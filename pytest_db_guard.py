"""Centralized, fail-closed test-database safety policy, enforced at CONNECTION
time (not merely create_engine time).

Imported and installed ONLY by the root conftest.py, so it is inert in
production: the application runtime never imports it, never registers the
SQLAlchemy listener, and never wraps the DBAPI connect functions.

Why connection-time and not create_engine-time
-----------------------------------------------
An earlier version validated the URL string passed to create_engine(). That is
not a real boundary:
  * cached engines (db_client._ENGINE_CACHE) are created once and reused - a
    create_engine check never re-runs, so approval removed AFTER creation was
    not caught;
  * a module that imported create_engine under a different alias, or any direct
    psycopg/psycopg2.connect(), never passed through the wrapper at all;
  * the URL string is NOT the effective target - libpq honors query overrides
    (?host=, ?port=), multi-host lists, Unix-socket hosts, hostaddr, and
    service-file routing, so the parsed URL host can differ from where the
    driver actually connects.

The only chokepoint EVERY SQLAlchemy connection shares is the dialect's
do_connect event, which fires - with the effective connection parameters - for
every physical connection of every engine, including engines created before the
listener was installed (it is registered on the Engine class, not an instance).
Direct DBAPI use is covered by wrapping psycopg/psycopg2.connect. Both validate
the same way.

Policy (fail closed, allowlist only)
------------------------------------
  * sqlite connections are always allowed (in-memory / tmp file, no network).
  * a PostgreSQL connection is allowed ONLY if its EFFECTIVE identity
    (host, port, dbname) exactly equals the identity of an approved disposable
    database whose category is BOTH configured and acknowledged. There is no
    substring/heuristic production detection and no generic-name escape hatch -
    identity must match exactly.
  * connection-routing overrides that cannot be independently validated
    (libpq service file, hostaddr, multi-host, Unix socket) are rejected before
    any identity comparison.
  * every other target - any unapproved or production-shaped PostgreSQL, any
    other driver - is refused.

Per-category approval (migration vs Operations Inbox certification) is separate:
each category has its own URL env var AND its own destructive-acknowledgement
env var, so authorizing one destructive integration-test category never
authorizes another.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy.engine import make_url

POSTGRES_BACKENDS = {"postgresql", "postgres"}
_TRUTHY = {"1", "true", "yes", "y", "on"}

# category -> (disposable-URL env var, per-category destructive-ack env var)
DESTRUCTIVE_TEST_CATEGORIES: dict[str, tuple[str, str]] = {
    "migration": ("MIGRATION_TEST_DATABASE_URL", "ALLOW_DESTRUCTIVE_MIGRATION_DB"),
    "inbox_certification": ("INBOX_CERTIFICATION_DATABASE_URL", "ALLOW_DESTRUCTIVE_INBOX_CERTIFICATION_DB"),
}

# libpq connection-routing keys whose effective target we cannot independently
# validate from the URL/params - their presence is refused outright.
UNVALIDATABLE_ROUTING_KEYS = ("service", "hostaddr", "passfile")

DEFAULT_PG_PORT = 5432


class DatabaseSafetyError(RuntimeError):
    """Raised when a test tries to open a database connection the policy forbids."""


def _flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Effective-identity extraction (the thing the driver will really connect to).
# ---------------------------------------------------------------------------

def _identity_from_effective_params(host, port, dbname) -> tuple:
    host = str(host or "").strip()
    if not host:
        raise DatabaseSafetyError("refusing a connection with no explicit host (ambiguous target).")
    if "," in host:
        raise DatabaseSafetyError(f"refusing a multi-host connection target: {host!r}.")
    if host.startswith("/"):
        raise DatabaseSafetyError(f"refusing a Unix-socket connection target: {host!r}.")
    try:
        port_int = int(port) if port not in (None, "") else DEFAULT_PG_PORT
    except (TypeError, ValueError):
        raise DatabaseSafetyError(f"refusing a connection with a non-integer port: {port!r}.")
    dbname = str(dbname or "").strip()
    if not dbname:
        raise DatabaseSafetyError("refusing a connection with no explicit database name.")
    return (host.lower(), port_int, dbname)


def _reject_unvalidatable_routing(params: dict) -> None:
    for key in UNVALIDATABLE_ROUTING_KEYS:
        if params.get(key):
            raise DatabaseSafetyError(
                f"refusing a connection that uses the libpq routing option {key!r}; its "
                "effective target cannot be independently validated."
            )


def _identity_from_cparams(cparams: dict) -> tuple:
    _reject_unvalidatable_routing(cparams)
    return _identity_from_effective_params(
        cparams.get("host"),
        cparams.get("port"),
        cparams.get("dbname") if cparams.get("dbname") is not None else cparams.get("database"),
    )


def _pg_cparams_from_url(url: str) -> dict:
    """Resolve the EFFECTIVE psycopg connect params for a PostgreSQL URL, applying
    the same query/routing resolution the driver would (so ?host=/?service= are
    reflected), using the psycopg2 dialect as the canonical resolver."""
    from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

    _cargs, cparams = PGDialect_psycopg2().create_connect_args(make_url(url))
    return dict(cparams)


def _identity_from_url(url: str) -> tuple:
    u = make_url(url)
    if u.get_backend_name() not in POSTGRES_BACKENDS:
        raise DatabaseSafetyError(f"PostgreSQL scheme required, got backend={u.get_backend_name()!r}.")
    return _identity_from_cparams(_pg_cparams_from_url(url))


# ---------------------------------------------------------------------------
# Approved-identity allowlist (exact identities of configured+acked categories).
# ---------------------------------------------------------------------------

def approved_identities() -> set[tuple]:
    identities: set[tuple] = set()
    for url_env, ack_env in DESTRUCTIVE_TEST_CATEGORIES.values():
        url = os.environ.get(url_env)
        if not url or not url.strip() or not _flag(ack_env):
            continue
        try:
            identities.add(_identity_from_url(url.strip()))
        except DatabaseSafetyError:
            # A misconfigured/ambiguous approved URL registers nothing - fail closed.
            continue
    return identities


def category_authorized(category: str) -> bool:
    """True if a destructive-test CATEGORY is fully authorized: its URL var is set,
    its own ack var is set, and the URL is a valid disposable target. Used by the
    skipif gates of destructive integration tests so every category shares one
    predicate (migration, Operations Inbox certification, and the worker/outbox/
    realtime/concurrency suites that run against the migration scratch DB)."""
    if category not in DESTRUCTIVE_TEST_CATEGORIES:
        raise KeyError(f"unknown destructive-test category: {category!r}")
    url_env, ack_env = DESTRUCTIVE_TEST_CATEGORIES[category]
    url = os.environ.get(url_env)
    if not url or not url.strip() or not _flag(ack_env):
        return False
    try:
        _identity_from_url(url.strip())
    except DatabaseSafetyError:
        return False
    return True


def _assert_identity_approved(identity: tuple, *, source: str) -> None:
    if identity not in approved_identities():
        raise DatabaseSafetyError(
            f"refusing {source} connection to unapproved database target {identity!r}. "
            "Only the exact identity of a configured + acknowledged disposable test "
            "database is permitted during pytest."
        )


# ---------------------------------------------------------------------------
# SQLAlchemy connection-time listener (covers every engine, cached or not).
# ---------------------------------------------------------------------------

def _validate_sqlalchemy_connect(dialect, conn_record, cargs, cparams):  # noqa: ANN001
    name = (getattr(dialect, "name", "") or "").lower()
    if name == "sqlite":
        return None  # disposable, no network
    if name not in POSTGRES_BACKENDS:
        raise DatabaseSafetyError(
            f"refusing a non-sqlite/non-postgresql connection during pytest: dialect={name!r}."
        )
    _assert_identity_approved(_identity_from_cparams(dict(cparams)), source="SQLAlchemy")
    return None


# ---------------------------------------------------------------------------
# Direct DBAPI (psycopg / psycopg2) connect wrapping.
# ---------------------------------------------------------------------------

def _identity_from_dbapi_args(args: tuple, kwargs: dict) -> tuple:
    if args and isinstance(args[0], str):
        dsn = args[0].strip()
        if "://" in dsn:
            return _identity_from_url(dsn)
        # libpq keyword/value connection string, e.g. "host=.. dbname=.. service=.."
        params: dict = {}
        for token in dsn.split():
            if "=" in token:
                key, value = token.split("=", 1)
                params[key.strip()] = value.strip()
        return _identity_from_cparams(params)
    return _identity_from_cparams(dict(kwargs))


def _make_guarded_dbapi_connect(real_connect):
    def guarded_connect(*args, **kwargs):
        _assert_identity_approved(_identity_from_dbapi_args(args, kwargs), source="direct DBAPI")
        return real_connect(*args, **kwargs)

    guarded_connect._is_db_guard = True  # type: ignore[attr-defined]
    guarded_connect._real_connect = real_connect  # type: ignore[attr-defined]
    return guarded_connect


# ---------------------------------------------------------------------------
# Installation (idempotent). Called by conftest at import, configure, per test.
# ---------------------------------------------------------------------------

_LISTENER_INSTALLED = False


def install_connection_guard() -> None:
    global _LISTENER_INSTALLED

    if not _LISTENER_INSTALLED:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        event.listen(Engine, "do_connect", _validate_sqlalchemy_connect)
        _LISTENER_INSTALLED = True

    for module_name in ("psycopg2", "psycopg"):
        module = sys.modules.get(module_name)
        if module is None:
            try:
                import importlib

                module = importlib.import_module(module_name)
            except Exception:
                module = None
        if module is None:
            continue
        connect = getattr(module, "connect", None)
        if connect is None or getattr(connect, "_is_db_guard", False):
            continue
        module.connect = _make_guarded_dbapi_connect(connect)


# Back-compat alias (conftest and older tests call install_engine_guard()).
install_engine_guard = install_connection_guard


# ---------------------------------------------------------------------------
# Skip helper for integration tests (proactive, per destructive category).
# ---------------------------------------------------------------------------

def require_disposable_database(category: str, *, scope: str = ""):
    """Return the approved disposable URL for a destructive-test CATEGORY, or
    pytest.skip with a precise reason. Each category requires BOTH its URL env
    var and its own destructive-acknowledgement env var, so authorizing one
    category never authorizes another."""
    import pytest

    if category not in DESTRUCTIVE_TEST_CATEGORIES:
        raise KeyError(f"unknown destructive-test category: {category!r}")
    url_env, ack_env = DESTRUCTIVE_TEST_CATEGORIES[category]
    label = f" for {scope}" if scope else f" for {category}"

    url = os.environ.get(url_env)
    if not url or not url.strip():
        pytest.skip(f"{url_env} not set{label}; requires an explicit disposable PostgreSQL database.")
    if not _flag(ack_env):
        pytest.skip(
            f"{ack_env} not set{label}; this destructive-test category requires explicit, "
            "per-category acknowledgement."
        )
    try:
        _identity_from_url(url.strip())
    except DatabaseSafetyError as exc:
        pytest.skip(f"{url_env} is not a valid disposable target{label}: {exc}")
    return url.strip()
