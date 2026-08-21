from __future__ import annotations

import pytest

from scripts import sample_data


SAFE_URL = "postgresql+psycopg2://qa:secret@127.0.0.1:5433/calitrans_qa"
SAFE_IDENTITY = "127.0.0.1:5433/calitrans_qa"


def test_production_like_database_name_is_refused_without_connecting(monkeypatch, capsys):
    monkeypatch.setenv("CALITRANS_ALLOW_QA_DATA", sample_data.ALLOW_VALUE)
    monkeypatch.setenv("CALITRANS_QA_DATABASE_IDENTITY", "db.example.test/postgres")
    called = False

    def forbidden_create_engine(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("production refusal must happen before connecting")

    monkeypatch.setattr(sample_data, "create_engine", forbidden_create_engine)
    result = sample_data.main(
        ["seed", "--database-url", "postgresql://user:password@db.example.test/postgres", "--environment", "test"]
    )

    assert result == 3
    assert called is False
    assert "REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("environment", "allow_value", "expected_identity"),
    [
        ("test", "", SAFE_IDENTITY),
        ("development", "wrong", SAFE_IDENTITY),
        ("test", sample_data.ALLOW_VALUE, "different-host/calitrans_qa"),
    ],
)
def test_every_safety_marker_is_required(environment, allow_value, expected_identity):
    with pytest.raises(sample_data.SafetyError):
        sample_data.assert_safe_target(
            database_url=SAFE_URL,
            environment=environment,
            expected_identity=expected_identity,
            allow_value=allow_value,
        )


def test_dry_run_is_deterministic_and_never_connects(monkeypatch, capsys):
    monkeypatch.setenv("CALITRANS_ALLOW_QA_DATA", sample_data.ALLOW_VALUE)
    monkeypatch.setenv("CALITRANS_QA_DATABASE_IDENTITY", SAFE_IDENTITY)
    monkeypatch.setattr(
        sample_data,
        "create_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not connect")),
    )

    args = ["seed", "--database-url", SAFE_URL, "--environment", "test", "--dataset", "baseline-01", "--dry-run"]
    assert sample_data.main(args) == 0
    first = capsys.readouterr().out
    assert sample_data.main(args) == 0
    second = capsys.readouterr().out

    assert first == second
    assert "SYNTHETIC-CALITRANS-QA::baseline-01" in first
    assert "secret" not in first


def test_dataset_identifier_is_restricted(monkeypatch):
    monkeypatch.setenv("CALITRANS_ALLOW_QA_DATA", sample_data.ALLOW_VALUE)
    monkeypatch.setenv("CALITRANS_QA_DATABASE_IDENTITY", SAFE_IDENTITY)
    result = sample_data.main(
        ["seed", "--database-url", SAFE_URL, "--environment", "test", "--dataset", "../../production", "--dry-run"]
    )
    assert result == 2


def test_target_identity_never_contains_credentials():
    identity = sample_data.target_identity(SAFE_URL)
    assert identity == SAFE_IDENTITY
    assert "qa:" not in identity
    assert "secret" not in identity
