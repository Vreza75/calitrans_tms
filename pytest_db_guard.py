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

Independent production backstop (not naming/ack based)
------------------------------------------------------
Approval requires, in addition to a configured+acknowledged category, that the
target identity appear in DISPOSABLE_DB_IDENTITIES (positive, operator-supplied
evidence of disposability) and NOT appear in FORBIDDEN_DATABASE_IDENTITIES
(explicit production deny). A category URL silently repointed at a production
database is therefore refused even with its acknowledgement set.

Known in-process limitations (require network isolation)
--------------------------------------------------------
Two paths cannot be fully guarded inside the interpreter:
  * a module that bound the driver connect under an alias BEFORE the guard was
    installed (e.g. `from psycopg2 import connect as c`) holds the original
    callable; only `psycopg2.connect`/`psycopg.connect` attribute access is
    wrapped (the per-test install re-wraps after a reload, but a pre-bound alias
    is unreachable), and
  * an already-open pooled connection is re-validated on CHECKOUT, but a caller
    holding a live DBAPI connection object directly bypasses the pool entirely.
The application code in this repository uses neither path (no direct psycopg
import; all access is through SQLAlchemy). Destructive integration tests that run
against a real PostgreSQL MUST therefore also be network-isolated to a disposable
host (container/network namespace with no route to production), so that even an
unguarded in-process path cannot reach a production database.
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
# Explicit routing kwargs that, when passed to a direct DBAPI connect() alongside
# a positional/keyword DSN, override the DSN per libpq precedence.
DBAPI_ROUTING_KWARGS = ("host", "hostaddr", "port", "dbname", "database", "service", "passfile")

DEFAULT_PG_PORT = 5432

# Independent production backstop (correction 3). These are evaluated in ADDITION
# to per-category approval, so neither URL naming nor acknowledgement alone can
# authorize a target:
#   * FORBIDDEN_DATABASE_IDENTITIES - comma/space separated identities
#     (full URLs or "host:port/db") that are ALWAYS denied, even if some category
#     var is (mis)configured to point at them.
#   * DISPOSABLE_DB_IDENTITIES - positive evidence: the operator must independently
#     enumerate the exact identities known to be disposable. A target is approved
#     only if its identity appears here, so a category URL silently repointed at a
#     production database (not listed) is refused.
FORBIDDEN_IDENTITIES_ENV_VAR = "FORBIDDEN_DATABASE_IDENTITIES"
DISPOSABLE_IDENTITIES_ENV_VAR = "DISPOSABLE_DB_IDENTITIES"


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


def _resolve_dbname(params: dict):
    """Collapse the libpq/psycopg `database` alias into `dbname` using the driver's
    actual precedence. psycopg2 REFUSES both at once ("you can't specify both
    'database' and 'dbname'"), so if both are present with different values there
    is no single effective target - reject as ambiguous (fail closed). If both
    are present and identical, or only one is given, resolve to that value."""
    has_dbname = params.get("dbname") is not None
    has_database = params.get("database") is not None
    if has_dbname and has_database:
        if str(params["dbname"]) != str(params["database"]):
            raise DatabaseSafetyError(
                "ambiguous database target: both 'dbname'="
                f"{params['dbname']!r} and 'database'={params['database']!r} were given "
                "with different values; the driver cannot use both."
            )
        return params["dbname"]
    if has_dbname:
        return params["dbname"]
    if has_database:
        return params["database"]
    return None


def _identity_from_cparams(cparams: dict) -> tuple:
    _reject_unvalidatable_routing(cparams)
    return _identity_from_effective_params(
        cparams.get("host"),
        cparams.get("port"),
        _resolve_dbname(cparams),
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


def _parse_identity_token(token: str) -> tuple:
    """Parse one entry of FORBIDDEN_DATABASE_IDENTITIES / DISPOSABLE_DB_IDENTITIES.

    Accepts a full PostgreSQL URL or a compact "host:port/db" / "host/db" form
    (port defaults to 5432). Returns a normalized (host, port, dbname) identity."""
    token = token.strip()
    if "://" in token:
        return _identity_from_url(token)
    host_part, _, db_part = token.partition("/")
    host, _, port = host_part.partition(":")
    return _identity_from_effective_params(host, port or None, db_part)


def _identity_set_from_env(env_var: str) -> set[tuple]:
    """Parse an identity list env var STRICTLY. Any malformed or ambiguous entry
    raises (fail closed) rather than being silently dropped: a bad deny-list entry
    must never weaken the backstop, and a bad evidence entry must never leave a
    partial allowlist. A broken list therefore prevents all PostgreSQL
    authorization until the operator fixes it."""
    raw = os.environ.get(env_var) or ""
    identities: set[tuple] = set()
    for token in raw.replace(",", " ").split():
        try:
            identities.add(_parse_identity_token(token))
        except DatabaseSafetyError as exc:
            raise DatabaseSafetyError(
                f"{env_var} contains a malformed/ambiguous entry {token!r}: {exc}"
            ) from exc
    return identities


def forbidden_identities() -> set[tuple]:
    return _identity_set_from_env(FORBIDDEN_IDENTITIES_ENV_VAR)


def disposable_evidence_identities() -> set[tuple]:
    return _identity_set_from_env(DISPOSABLE_IDENTITIES_ENV_VAR)


# ---------------------------------------------------------------------------
# Approved-identity allowlist (exact identities of configured+acked categories).
# ---------------------------------------------------------------------------

def approved_identities() -> set[tuple]:
    """Identities permitted to connect. An identity qualifies ONLY if ALL hold:
      * its category is configured (URL var set) and acknowledged (ack var set),
      * it is listed in DISPOSABLE_DB_IDENTITIES (independent positive evidence of
        disposability - not derived from the URL or the acknowledgement), and
      * it is NOT in FORBIDDEN_DATABASE_IDENTITIES (the production backstop).
    If no positive disposability evidence is configured, nothing is approved."""
    # Strict: a malformed backstop list propagates (fail closed) - callers that
    # must not hard-error (category_authorized / require_disposable_database)
    # catch it and refuse/skip.
    evidence = disposable_evidence_identities()
    forbidden = forbidden_identities()
    identities: set[tuple] = set()
    for url_env, ack_env in DESTRUCTIVE_TEST_CATEGORIES.values():
        url = os.environ.get(url_env)
        if not url or not url.strip() or not _flag(ack_env):
            continue
        try:
            identity = _identity_from_url(url.strip())
        except DatabaseSafetyError:
            # A misconfigured/ambiguous approved URL registers nothing - fail closed.
            continue
        if identity in forbidden:
            continue  # explicit production deny overrides any approval
        if identity not in evidence:
            continue  # no independent positive evidence that this target is disposable
        identities.add(identity)
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
        identity = _identity_from_url(url.strip())
        # Must also clear the independent backstop (positive disposability
        # evidence and not denied) - identical to the connection-time allowlist.
        # A malformed backstop list makes approved_identities() raise; treat that
        # as not-authorized (fail closed).
        return identity in approved_identities()
    except DatabaseSafetyError:
        return False


def _assert_identity_approved(identity: tuple, *, source: str) -> None:
    try:
        forbidden = forbidden_identities()
        approved = approved_identities()
    except DatabaseSafetyError as exc:
        # Malformed deny/evidence list -> cannot prove this target is safe.
        raise DatabaseSafetyError(
            f"refusing {source} connection: database-identity backstop configuration is "
            f"invalid, so no PostgreSQL target can be authorized ({exc})."
        ) from exc
    if identity in forbidden:
        raise DatabaseSafetyError(
            f"refusing {source} connection to a FORBIDDEN (production) database target "
            f"{identity!r}; this identity is explicitly denied regardless of approval."
        )
    if identity not in approved:
        raise DatabaseSafetyError(
            f"refusing {source} connection to unapproved database target {identity!r}. "
            "A target is permitted only if its category is configured + acknowledged, "
            "its identity is listed in DISPOSABLE_DB_IDENTITIES (positive evidence), and "
            "it is not in FORBIDDEN_DATABASE_IDENTITIES."
        )


# ---------------------------------------------------------------------------
# SQLAlchemy connection-time listener (covers every engine, cached or not).
# ---------------------------------------------------------------------------

_CONN_RECORD_IDENTITY_KEY = "_db_guard_identity"


def _validate_sqlalchemy_connect(dialect, conn_record, cargs, cparams):  # noqa: ANN001
    name = (getattr(dialect, "name", "") or "").lower()
    if name == "sqlite":
        return None  # disposable, no network
    if name not in POSTGRES_BACKENDS:
        raise DatabaseSafetyError(
            f"refusing a non-sqlite/non-postgresql connection during pytest: dialect={name!r}."
        )
    identity = _identity_from_cparams(dict(cparams))
    _assert_identity_approved(identity, source="SQLAlchemy")
    # Stamp the validated identity on the pooled connection record so every later
    # checkout can re-verify it is STILL approved (correction 2).
    try:
        conn_record.info[_CONN_RECORD_IDENTITY_KEY] = identity
    except Exception:
        pass
    return None


def _is_sqlite_dbapi_connection(dbapi_connection) -> bool:
    """True only if the raw DBAPI connection is verifiably a sqlite3 connection.
    Used to decide whether an UNSTAMPED pooled connection (one whose do_connect
    never ran - e.g. an engine created before the listener was installed) may be
    allowed. Anything not provably sqlite is treated as PostgreSQL/unknown."""
    module = (getattr(type(dbapi_connection), "__module__", "") or "").split(".")[0]
    return module in ("sqlite3", "pysqlite2", "_sqlite3")


def _validate_pool_checkout(dbapi_connection, conn_record, connection_proxy):  # noqa: ANN001
    """Re-validate on every pool checkout: a connection opened while a target was
    approved must NOT keep being handed out after approval is revoked mid-session
    (correction 2). An UNSTAMPED record (do_connect never ran for it - e.g. an
    engine created before the listener was installed) is allowed ONLY if the raw
    connection is verifiably sqlite; an unstamped PostgreSQL/unknown connection is
    refused (correction 3 - defect: unstamped connections must not auto-pass)."""
    identity = None
    try:
        identity = conn_record.info.get(_CONN_RECORD_IDENTITY_KEY)
    except Exception:
        identity = None
    if identity is not None:
        _assert_identity_approved(identity, source="pooled (checkout)")
        return
    if _is_sqlite_dbapi_connection(dbapi_connection):
        return  # verifiably disposable, no network
    raise DatabaseSafetyError(
        "refusing an UNSTAMPED non-sqlite pooled connection at checkout; it was not "
        "validated at connect time (likely created before the guard was installed) and "
        "cannot be proven to target an approved disposable database."
    )


# ---------------------------------------------------------------------------
# Direct DBAPI (psycopg / psycopg2) connect wrapping.
# ---------------------------------------------------------------------------

def _params_from_dsn_string(dsn: str) -> dict:
    dsn = dsn.strip()
    if "://" in dsn:
        return dict(_pg_cparams_from_url(dsn))
    # libpq keyword/value connection string, e.g. "host=.. dbname=.. service=.."
    params: dict = {}
    for token in dsn.split():
        if "=" in token:
            key, value = token.split("=", 1)
            params[key.strip()] = value.strip()
    return params


def _identity_from_dbapi_args(args: tuple, kwargs: dict) -> tuple:
    """Resolve the EFFECTIVE identity of a direct DBAPI connect(), normalizing a
    positional/keyword DSN together with explicit routing kwargs using libpq
    precedence (explicit keyword parameters override the DSN). This closes the
    mixed DSN+kwargs smuggle where a kwarg host override pointed the real
    connection at a different target than the validated DSN (correction 1)."""
    params: dict = {}
    if args and isinstance(args[0], str):
        params.update(_params_from_dsn_string(args[0]))
    dsn_kwarg = kwargs.get("dsn")
    if isinstance(dsn_kwarg, str):
        params.update(_params_from_dsn_string(dsn_kwarg))
    # Explicit routing kwargs win over anything from the DSN (libpq precedence).
    for key in DBAPI_ROUTING_KWARGS:
        if key in kwargs and kwargs[key] is not None:
            params[key] = kwargs[key]
    return _identity_from_cparams(params)


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
        event.listen(Engine, "checkout", _validate_pool_checkout)
        _LISTENER_INSTALLED = True

    # Re-wrap on every install so a reload of psycopg/psycopg2 (which restores the
    # original, unmarked connect) is re-guarded at the next per-test install
    # (correction 4). Aliases captured before the first wrap cannot be reached in
    # process - see the module docstring's network-isolation requirement.
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
        identity = _identity_from_url(url.strip())
    except DatabaseSafetyError as exc:
        pytest.skip(f"{url_env} is not a valid disposable target{label}: {exc}")
    try:
        forbidden = forbidden_identities()
        evidence = disposable_evidence_identities()
    except DatabaseSafetyError as exc:
        pytest.skip(f"database-identity backstop configuration is invalid{label}: {exc}")
    if identity in forbidden:
        pytest.skip(
            f"{url_env} resolves to a FORBIDDEN (production) identity{label}; refusing."
        )
    if identity not in evidence:
        pytest.skip(
            f"{url_env} identity is not listed in {DISPOSABLE_IDENTITIES_ENV_VAR}{label}; "
            "positive disposability evidence is required (naming/ack alone is insufficient)."
        )
    return url.strip()
