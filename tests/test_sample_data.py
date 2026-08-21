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


# --- CTMS-011: validate() must distinguish complete/partial/absent/schema_missing,
# not just "did the count queries execute" (see PRODUCTION_READINESS_TRACKER.md). ---


def test_validate_dataset_reports_schema_missing_without_querying_counts(monkeypatch):
    """A required table absent from the schema entirely must short-circuit
    before any count query runs - querying a nonexistent table would itself
    raise, and that's a different failure mode (connection/query error, exit
    1) from "the schema was never migrated for this feature" (exit 6)."""
    monkeypatch.setattr(sample_data, "_tables", lambda engine: {"customers", "warehouses"})
    called = False

    def forbidden_list_dataset(engine, dataset):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(sample_data, "list_dataset", forbidden_list_dataset)

    status, detail = sample_data.validate_dataset(object(), "baseline-01")

    assert status == "schema_missing"
    assert called is False
    assert set(detail["missing_tables"]) == {"carriers", "drivers", "loads", "order_intake"}


def test_validate_dataset_reports_absent_when_every_count_is_zero(monkeypatch):
    monkeypatch.setattr(sample_data, "_tables", lambda engine: set(sample_data.REQUIRED_DATASET_TABLES))
    monkeypatch.setattr(
        sample_data, "list_dataset", lambda engine, dataset: {t: 0 for t in sample_data.REQUIRED_DATASET_TABLES}
    )

    status, detail = sample_data.validate_dataset(object(), "baseline-01")

    assert status == "absent"
    assert all(count == 0 for count in detail.values())


def test_validate_dataset_reports_partial_when_a_count_does_not_match_expected(monkeypatch):
    monkeypatch.setattr(sample_data, "_tables", lambda engine: set(sample_data.REQUIRED_DATASET_TABLES))
    counts = {"customers": 1, "warehouses": 1, "carriers": 1, "drivers": 2, "loads": 4, "order_intake": 1}
    monkeypatch.setattr(sample_data, "list_dataset", lambda engine, dataset: counts)

    status, detail = sample_data.validate_dataset(object(), "baseline-01")

    assert status == "partial"
    assert detail == counts


def test_validate_dataset_reports_complete_when_every_count_matches_expected(monkeypatch):
    monkeypatch.setattr(sample_data, "_tables", lambda engine: set(sample_data.REQUIRED_DATASET_TABLES))
    counts = {"customers": 1, "warehouses": 1, "carriers": 1, "drivers": 3, "loads": 4, "order_intake": 1}
    monkeypatch.setattr(sample_data, "list_dataset", lambda engine, dataset: counts)

    status, detail = sample_data.validate_dataset(object(), "baseline-01")

    assert status == "complete"
    assert detail == counts


def test_validate_dataset_complete_ignores_optional_tables_absent_from_schema(monkeypatch):
    """operations_tasks/worker_jobs/outbox_events/domain_events are optional -
    a schema that never migrated them must still be able to reach "complete"."""
    monkeypatch.setattr(sample_data, "_tables", lambda engine: set(sample_data.REQUIRED_DATASET_TABLES))
    counts = {"customers": 1, "warehouses": 1, "carriers": 1, "drivers": 3, "loads": 4, "order_intake": 1}
    monkeypatch.setattr(sample_data, "list_dataset", lambda engine, dataset: counts)

    status, _ = sample_data.validate_dataset(object(), "baseline-01")

    assert status == "complete"


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    def __init__(self):
        self.disposed = False

    def connect(self):
        return _FakeConnection()

    def dispose(self):
        self.disposed = True


def _run_main_validate(monkeypatch, *, tables, list_dataset_result):
    monkeypatch.setenv("CALITRANS_ALLOW_QA_DATA", sample_data.ALLOW_VALUE)
    monkeypatch.setenv("CALITRANS_QA_DATABASE_IDENTITY", SAFE_IDENTITY)
    monkeypatch.setattr(sample_data, "_verify_live_identity", lambda conn, identity: None)
    monkeypatch.setattr(sample_data, "_tables", lambda engine: tables)
    monkeypatch.setattr(sample_data, "list_dataset", lambda engine, dataset: list_dataset_result)
    monkeypatch.setattr(sample_data, "create_engine", lambda *args, **kwargs: _FakeEngine())
    return sample_data.main(
        ["validate", "--database-url", SAFE_URL, "--environment", "test", "--dataset", "baseline-01"]
    )


def test_main_validate_exits_6_when_schema_missing(monkeypatch, capsys):
    rc = _run_main_validate(monkeypatch, tables={"customers"}, list_dataset_result={})
    assert rc == 6
    assert "missing" in capsys.readouterr().err.lower()


def test_main_validate_exits_4_when_dataset_absent(monkeypatch, capsys):
    rc = _run_main_validate(
        monkeypatch,
        tables=set(sample_data.REQUIRED_DATASET_TABLES),
        list_dataset_result={t: 0 for t in sample_data.REQUIRED_DATASET_TABLES},
    )
    assert rc == 4
    assert "dataset absent" in capsys.readouterr().err


def test_main_validate_exits_5_when_dataset_partial(monkeypatch, capsys):
    rc = _run_main_validate(
        monkeypatch,
        tables=set(sample_data.REQUIRED_DATASET_TABLES),
        list_dataset_result={"customers": 1, "warehouses": 1, "carriers": 1, "drivers": 2, "loads": 4, "order_intake": 1},
    )
    assert rc == 5
    assert "partial" in capsys.readouterr().err


def test_main_validate_exits_0_when_dataset_complete(monkeypatch, capsys):
    counts = {"customers": 1, "warehouses": 1, "carriers": 1, "drivers": 3, "loads": 4, "order_intake": 1}
    rc = _run_main_validate(monkeypatch, tables=set(sample_data.REQUIRED_DATASET_TABLES), list_dataset_result=counts)
    assert rc == 0
    out = capsys.readouterr().out
    assert '"customers": 1' in out


def test_cleanup_then_validate_reports_absent_not_success(monkeypatch, capsys):
    """Direct regression for the exact scenario CTMS-011 was found in:
    cleanup() zeroes every count but leaves the tables themselves in place -
    validate() must not treat that as success."""
    rc = _run_main_validate(
        monkeypatch,
        tables=set(sample_data.REQUIRED_DATASET_TABLES),
        list_dataset_result={t: 0 for t in sample_data.REQUIRED_DATASET_TABLES},
    )
    assert rc != 0
    assert rc == 4


def test_validate_query_failure_is_sanitized_not_success(monkeypatch, capsys):
    """A connection/query failure during validate must fall through to the
    generic sanitized error path (exit 1), never exit 0, and must never echo
    the underlying exception text (which could carry a DSN or credential)."""
    monkeypatch.setenv("CALITRANS_ALLOW_QA_DATA", sample_data.ALLOW_VALUE)
    monkeypatch.setenv("CALITRANS_QA_DATABASE_IDENTITY", SAFE_IDENTITY)
    monkeypatch.setattr(sample_data, "_verify_live_identity", lambda conn, identity: None)

    def exploding_tables(engine):
        raise RuntimeError("could not connect to server: password authentication failed for user 'qa'")

    monkeypatch.setattr(sample_data, "_tables", exploding_tables)
    monkeypatch.setattr(sample_data, "create_engine", lambda *args, **kwargs: _FakeEngine())

    rc = sample_data.main(["validate", "--database-url", SAFE_URL, "--environment", "test", "--dataset", "baseline-01"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "password" not in err
    assert "authentication failed" not in err
    assert "ERROR: RuntimeError: sample-data operation failed" in err
