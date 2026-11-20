"""Regression coverage for CTMS-010: the Operations Inbox certification
harness's require_scratch_database_url() safety gate.

Pure unit tests - no real database connection, no INBOX_CERTIFICATION_
DATABASE_URL required. require_scratch_database_url() only parses and
validates a URL string; it never opens a connection itself. These tests
run unconditionally as part of the safe backend suite.

Root cause this closes: the original implementation rejected a target only
if it was byte-identical to config.get_secret("DATABASE_URL"). Root
conftest.py's session-wide pytest guard (see its own docstring) makes that
call return None for the entire pytest session, so that comparison could
never fire for any test collected under this repository's root
conftest.py - i.e. always, since every caller of require_scratch_database_url
is a pytest test. The fix replaces the equality check with a
parse-and-validate model (mirroring scripts/sample_data.py's
assert_safe_target()): the database name must carry an explicit
non-production marker, and the operator must separately acknowledge the
exact sanitized target identity via INBOX_CERTIFICATION_DATABASE_IDENTITY.
"""
from __future__ import annotations

import pytest

from tests.integration.operations_inbox.harness import (
    CertificationSafetyError,
    ENV_VAR,
    IDENTITY_ENV_VAR,
    MissingScratchDatabaseError,
    require_scratch_database_url,
    target_identity,
)

DISPOSABLE_URL = "postgresql://calitrans_test:Sup3rS3cret!@localhost:5433/calitrans_inbox_cert"
DISPOSABLE_IDENTITY = "localhost:5433/calitrans_inbox_cert"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)


def test_missing_url_is_refused():
    with pytest.raises(MissingScratchDatabaseError):
        require_scratch_database_url()


def test_malformed_url_is_refused():
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url("not-a-valid-database-url")


def test_production_like_name_is_refused(monkeypatch):
    monkeypatch.setenv(IDENTITY_ENV_VAR, "localhost:5432/production")
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url("postgresql://app:pw@localhost:5432/production")


def test_generic_ambiguous_name_is_refused(monkeypatch):
    """A real, plausible-looking app database name with no dev/test/qa/
    scratch marker at all - must not be treated as safe by default."""
    monkeypatch.setenv(IDENTITY_ENV_VAR, "localhost:5432/calitrans")
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url("postgresql://app:pw@localhost:5432/calitrans")


def test_bare_postgres_database_name_is_refused(monkeypatch):
    monkeypatch.setenv(IDENTITY_ENV_VAR, "localhost:5432/postgres")
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url("postgresql://app:pw@localhost:5432/postgres")


def test_safe_name_without_identity_acknowledgement_is_refused():
    """Database name alone is not sufficient - the operator must also set
    INBOX_CERTIFICATION_DATABASE_IDENTITY."""
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url(DISPOSABLE_URL)


def test_identity_mismatch_is_refused(monkeypatch):
    monkeypatch.setenv(IDENTITY_ENV_VAR, "localhost:5433/some-other-scratch-db")
    with pytest.raises(CertificationSafetyError):
        require_scratch_database_url(DISPOSABLE_URL)


def test_accepted_disposable_identity_is_returned(monkeypatch):
    monkeypatch.setenv(IDENTITY_ENV_VAR, DISPOSABLE_IDENTITY)
    assert require_scratch_database_url(DISPOSABLE_URL) == DISPOSABLE_URL


def test_accepted_identity_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv(IDENTITY_ENV_VAR, f"  {DISPOSABLE_IDENTITY.upper()}  ")
    assert require_scratch_database_url(DISPOSABLE_URL) == DISPOSABLE_URL


def test_env_var_is_used_when_no_explicit_url_given(monkeypatch):
    monkeypatch.setenv(ENV_VAR, DISPOSABLE_URL)
    monkeypatch.setenv(IDENTITY_ENV_VAR, DISPOSABLE_IDENTITY)
    assert require_scratch_database_url() == DISPOSABLE_URL


def test_target_identity_never_includes_credentials():
    identity = target_identity(DISPOSABLE_URL)
    assert identity == DISPOSABLE_IDENTITY
    assert "calitrans_test" not in identity
    assert "Sup3rS3cret!" not in identity


@pytest.mark.parametrize(
    "url,identity_env",
    [
        ("postgresql://calitrans_test:Sup3rS3cret!@localhost:5432/production", "localhost:5432/production"),
        ("postgresql://calitrans_test:Sup3rS3cret!@localhost:5432/calitrans", "localhost:5432/calitrans"),
        ("not-a-valid-database-url", None),
    ],
)
def test_refused_targets_never_leak_credentials_in_the_raised_error(monkeypatch, url, identity_env):
    if identity_env is not None:
        monkeypatch.setenv(IDENTITY_ENV_VAR, identity_env)

    with pytest.raises((CertificationSafetyError, MissingScratchDatabaseError)) as exc_info:
        require_scratch_database_url(url)

    message = str(exc_info.value)
    assert "Sup3rS3cret!" not in message
    assert "calitrans_test" not in message
    assert url not in message
