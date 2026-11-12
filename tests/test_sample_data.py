from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from scripts import sample_data


SAFE_URL = "postgresql+psycopg2://qa:secret@127.0.0.1:5433/calitrans_qa"
SAFE_IDENTITY = "127.0.0.1:5433/calitrans_qa"

FULL_DATASET_COUNTS = {
    "customers": 1,
    "warehouses": 1,
    "carriers": 1,
    "drivers": 3,
    "loads": 4,
    "order_intake": 1,
    "operations_tasks": 1,
    "worker_jobs": 1,
    "outbox_events": 1,
    "domain_events": 1,
}


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


# --- CTMS-012: cleanup()/list_dataset() used an unanchored LIKE prefix
# ("<marker>%"), so dataset "baseline-01" would also match "baseline-010",
# "baseline-01-extra", or any dataset whose marker text happens to start
# with baseline-01's marker text. Fixed via _dataset_prefix_pattern()
# (marker + a literal trailing space, escaped for LIKE metacharacters) -
# a dataset id can only ever contain lowercase letters/digits/hyphens
# (DATASET_RE), never a space, so the space anchor cannot collide.
#
# These tests use a private, in-memory SQLite database - no network
# connection, no credential, not the approved disposable Postgres
# instance - purely to exercise the real seed()/list_dataset()/cleanup()
# SQL (including genuine transaction rollback) without touching any
# external resource. ---


def test_dataset_prefix_pattern_is_escaped_and_anchored():
    pattern = sample_data._dataset_prefix_pattern("baseline-01")
    assert pattern == "SYNTHETIC-CALITRANS-QA::baseline-01 %"


def test_escape_like_neutralizes_percent_underscore_and_backslash():
    assert sample_data._escape_like("a_b%c\\d") == r"a\_b\%c\\d"


@pytest.fixture
def sqlite_engine():
    """Minimal, in-memory schema covering only the columns seed()/
    list_dataset()/cleanup()/validate_dataset() actually touch. Not a
    stand-in for the real disposable Postgres certification - just enough
    to run the real functions end-to-end, including real transaction
    rollback, without any network connection or credential."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("pragma foreign_keys = on")
        conn.exec_driver_sql(
            "create table customers (id integer primary key autoincrement, "
            "company_name text not null, contact_name text, email text, phone text)"
        )
        conn.exec_driver_sql(
            "create table warehouses (id integer primary key autoincrement, "
            "warehouse_name text not null, address text, city text, state text, "
            "zip_code text, contact_name text, phone text)"
        )
        conn.exec_driver_sql(
            "create table carriers (id integer primary key autoincrement, "
            "company_name text not null, contact_name text, email text, phone text, mc_number text)"
        )
        conn.exec_driver_sql(
            "create table drivers (id integer primary key autoincrement, "
            "driver_name text not null, carrier_id integer references carriers(id), "
            "phone text, email text, truck_number text)"
        )
        conn.exec_driver_sql(
            "create table loads (id integer primary key autoincrement, "
            "booking_number text not null, type text, load_id text, reference_number text, "
            "customer text, port text, warehouse text, address text, status text, "
            "driver_name text, truck_assigned text, size text, dispatcher_notes text)"
        )
        conn.exec_driver_sql(
            "create table order_intake (id integer primary key autoincrement, "
            "source text, source_subject text, source_sender text, raw_text text, "
            "parsed_data text, intake_status text, action_required text, source_message_id text)"
        )
        conn.exec_driver_sql(
            "create table operations_tasks (id integer primary key autoincrement, "
            "task_title text not null, task_description text, task_status text, "
            "task_priority text, owner text, intake_id integer references order_intake(id), "
            "load_id integer references loads(id), customer text, booking_number text, created_by text)"
        )
        for table, type_column, terminal_column in (
            ("worker_jobs", "job_type", "completed_at"),
            ("outbox_events", "event_type", "processed_at"),
            ("domain_events", "event_type", "published_at"),
        ):
            conn.exec_driver_sql(
                f"create table {table} (id integer primary key autoincrement, "
                f"{type_column} text, aggregate_type text, aggregate_id text, payload text, "
                f"idempotency_key text, status text, actor text, {terminal_column} timestamp)"
            )
    return engine


def test_cleanup_deletes_exactly_the_requested_dataset(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    summary = sample_data.cleanup(sqlite_engine, "baseline-01")

    assert summary.deleted == 15
    assert sample_data.list_dataset(sqlite_engine, "baseline-01") == {t: 0 for t in FULL_DATASET_COUNTS}


@pytest.mark.parametrize(
    "colliding_dataset",
    ["baseline-010", "baseline-01-extra", "x-baseline-01"],
)
def test_cleanup_baseline_01_does_not_touch_prefix_colliding_datasets(sqlite_engine, colliding_dataset):
    sample_data.seed(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, colliding_dataset)

    sample_data.cleanup(sqlite_engine, "baseline-01")

    assert sample_data.list_dataset(sqlite_engine, colliding_dataset) == FULL_DATASET_COUNTS
    assert sample_data.list_dataset(sqlite_engine, "baseline-01") == {t: 0 for t in FULL_DATASET_COUNTS}


def test_cleanup_baseline_010_does_not_touch_baseline_01(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, "baseline-010")

    sample_data.cleanup(sqlite_engine, "baseline-010")

    assert sample_data.list_dataset(sqlite_engine, "baseline-01") == FULL_DATASET_COUNTS
    assert sample_data.list_dataset(sqlite_engine, "baseline-010") == {t: 0 for t in FULL_DATASET_COUNTS}


def test_cleanup_wildcard_dataset_id_cannot_broaden_matching(sqlite_engine):
    """Bypasses the CLI's DATASET_RE gate on purpose - calls cleanup()
    directly with a dataset id containing a SQL LIKE metacharacter, to
    prove the pattern builder itself (not just the CLI regex) is safe."""
    sample_data.seed(sqlite_engine, "baseline-01")

    summary = sample_data.cleanup(sqlite_engine, "x%")

    assert summary.deleted == 0
    assert sample_data.list_dataset(sqlite_engine, "baseline-01") == FULL_DATASET_COUNTS


def test_cleanup_is_scoped_across_every_seeded_table(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, "alternate-01")

    sample_data.cleanup(sqlite_engine, "baseline-01")

    baseline_counts = sample_data.list_dataset(sqlite_engine, "baseline-01")
    alternate_counts = sample_data.list_dataset(sqlite_engine, "alternate-01")
    for table in FULL_DATASET_COUNTS:
        assert baseline_counts[table] == 0, f"{table} not cleaned"
        assert alternate_counts[table] == FULL_DATASET_COUNTS[table], f"{table} lost unrelated rows"


def test_unrelated_synthetic_dataset_survives_cleanup(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, "alternate-01")

    sample_data.cleanup(sqlite_engine, "baseline-01")

    assert sample_data.list_dataset(sqlite_engine, "alternate-01") == FULL_DATASET_COUNTS


def test_non_synthetic_record_survives_cleanup(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    with sqlite_engine.begin() as conn:
        conn.execute(
            text("insert into customers (company_name, contact_name, email, phone) values (:n, 'Real Contact', 'real@example.com', '+1-555-0000')"),
            {"n": "Acme Real Customer Inc"},
        )

    sample_data.cleanup(sqlite_engine, "baseline-01")

    with sqlite_engine.connect() as conn:
        remaining = conn.execute(
            text("select count(*) from customers where company_name = :n"), {"n": "Acme Real Customer Inc"}
        ).scalar_one()
    assert remaining == 1


def test_cleanup_counts_are_accurate(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")

    summary = sample_data.cleanup(sqlite_engine, "baseline-01")

    assert summary.deleted == 15
    assert summary.by_table == {
        "domain_events": 1, "outbox_events": 1, "worker_jobs": 1,
        "operations_tasks": 1, "order_intake": 1, "loads": 4,
        "drivers": 3, "warehouses": 1, "customers": 1, "carriers": 1,
    }


def test_cleanup_rolls_back_completely_on_mid_operation_failure(sqlite_engine):
    sample_data.seed(sqlite_engine, "baseline-01")
    with sqlite_engine.begin() as conn:
        # Break carriers' predicate column specifically (last table in
        # cleanup()'s iteration order) so every other delete in the same
        # transaction has already run before this one raises.
        conn.exec_driver_sql("alter table carriers rename column company_name to company_name_broken")

    with pytest.raises(Exception):
        sample_data.cleanup(sqlite_engine, "baseline-01")

    with sqlite_engine.connect() as conn:
        for table in (
            "operations_tasks", "order_intake", "loads", "drivers",
            "warehouses", "customers", "domain_events", "outbox_events", "worker_jobs",
        ):
            count = conn.execute(text(f"select count(*) from {table}")).scalar_one()
            assert count > 0, f"{table} was not rolled back after the forced failure"


def test_reset_cleanup_phase_stays_exactly_scoped(sqlite_engine):
    """reset() = cleanup() then seed() on the same dataset (see main()) -
    reproduced here directly since reset itself is only wired in main()."""
    sample_data.seed(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, "baseline-010")

    sample_data.cleanup(sqlite_engine, "baseline-01")
    sample_data.seed(sqlite_engine, "baseline-01")

    assert sample_data.list_dataset(sqlite_engine, "baseline-01") == FULL_DATASET_COUNTS
    assert sample_data.list_dataset(sqlite_engine, "baseline-010") == FULL_DATASET_COUNTS


# --- CTMS-013: _verify_live_identity() claimed to verify the exact
# live-connected identity but only ever compared current_database() to the
# expected database name - it queried inet_server_addr() and never used
# the result, and never checked host or port at all. Fixed by reading the
# DBAPI driver's own effective connection parameters (psycopg2's
# get_dsn_parameters() - the documented, credential-safe libpq PQconninfo
# interface, which never includes the password) instead of PostgreSQL's
# own inet_server_addr()/inet_server_port(), which report the server's
# view of the connection and are wrong inside a Docker container (a
# host-mapped port like 5433 is reported by the server as its
# container-internal 5432).
#
# These tests use a fake object standing in for a SQLAlchemy Connection -
# no real database, no network connection, no credential. ---


class _FakeDbapiConnection:
    def __init__(self, dsn_parameters, raise_on_get=False):
        self._dsn_parameters = dsn_parameters
        self._raise_on_get = raise_on_get

    def get_dsn_parameters(self):
        if self._raise_on_get:
            raise RuntimeError("driver failure")
        return self._dsn_parameters


class _FakePoolProxiedConnection:
    def __init__(self, dbapi_connection):
        self.dbapi_connection = dbapi_connection


class _FakeResult:
    def __init__(self, database_name):
        self._database_name = database_name

    def mappings(self):
        return self

    def one(self):
        return {"database_name": self._database_name}


class _FakeLiveConnection:
    """Stands in for a SQLAlchemy Connection - implements only what
    _verify_live_identity() touches: .connection.dbapi_connection and
    .execute()."""

    def __init__(self, dsn_parameters, current_database, *, raise_on_get=False, no_dbapi=False):
        self.connection = None if no_dbapi else _FakePoolProxiedConnection(
            _FakeDbapiConnection(dsn_parameters, raise_on_get=raise_on_get)
        )
        self._current_database = current_database
        self.executed_sql: list[str] = []

    def execute(self, clause, *args, **kwargs):
        self.executed_sql.append(str(clause))
        return _FakeResult(self._current_database)


def test_verify_live_identity_accepts_exact_host_port_database():
    conn = _FakeLiveConnection({"host": "localhost", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")
    sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_rejects_database_mismatch():
    conn = _FakeLiveConnection({"host": "localhost", "port": "5433", "dbname": "wrong_db"}, "wrong_db")
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_rejects_port_mismatch():
    conn = _FakeLiveConnection({"host": "localhost", "port": "5432", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_rejects_host_mismatch():
    conn = _FakeLiveConnection(
        {"host": "unexpected-host.internal", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test"
    )
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "db.internal.example.test:5433/calitrans_qa_test")


def test_verify_live_identity_accepts_localhost_127_normalization():
    conn = _FakeLiveConnection({"host": "127.0.0.1", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")
    sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_accepts_localhost_ipv6_loopback_normalization():
    conn = _FakeLiveConnection({"host": "::1", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")
    sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_rejects_remote_host_when_localhost_expected():
    conn = _FakeLiveConnection(
        {"host": "db.remote.example.test", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test"
    )
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


@pytest.mark.parametrize(
    "dsn_parameters",
    [
        {"port": "5433", "dbname": "calitrans_qa_test"},  # host missing
        {"host": "localhost", "dbname": "calitrans_qa_test"},  # port missing
        {"host": "localhost", "port": "5433"},  # dbname missing
    ],
)
def test_verify_live_identity_rejects_missing_effective_metadata(dsn_parameters):
    conn = _FakeLiveConnection(dsn_parameters, "calitrans_qa_test")
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_fails_closed_when_driver_lacks_dsn_parameters():
    conn = _FakeLiveConnection({}, "calitrans_qa_test", no_dbapi=True)
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_fails_closed_when_driver_raises():
    conn = _FakeLiveConnection({}, "calitrans_qa_test", raise_on_get=True)
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_rejects_malformed_expected_identity():
    conn = _FakeLiveConnection({"host": "localhost", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "not-a-valid-identity")


def test_verify_live_identity_errors_never_leak_credentials():
    conn = _FakeLiveConnection(
        {"host": "db.example.test", "port": "5433", "dbname": "calitrans_qa_test", "user": "qa_user"},
        "calitrans_qa_test",
    )
    with pytest.raises(sample_data.SafetyError) as exc_info:
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")

    message = str(exc_info.value)
    assert "qa_user" not in message
    assert "password" not in message.lower()
    assert "postgresql://" not in message
    assert "@" not in message


def test_verify_live_identity_still_checks_current_database_even_when_dsn_matches():
    """DBAPI-reported params can match while the session is actually
    pointed at a different live database (e.g. via SET search_path/a
    connection-pool quirk) - current_database() must still be checked."""
    conn = _FakeLiveConnection(
        {"host": "localhost", "port": "5433", "dbname": "calitrans_qa_test"}, "some_other_live_database"
    )
    with pytest.raises(sample_data.SafetyError):
        sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")


def test_verify_live_identity_never_queries_inet_server_functions():
    """inet_server_addr()/inet_server_port() report PostgreSQL's own view
    of the connection, which is wrong inside a container - they must never
    be queried or presented as proof of the client-side endpoint."""
    conn = _FakeLiveConnection({"host": "localhost", "port": "5433", "dbname": "calitrans_qa_test"}, "calitrans_qa_test")

    sample_data._verify_live_identity(conn, "localhost:5433/calitrans_qa_test")

    executed = " ".join(conn.executed_sql)
    assert "inet_server_addr" not in executed
    assert "inet_server_port" not in executed


def test_parse_identity_round_trips_through_target_identity():
    identity = sample_data.target_identity("postgresql://u:p@localhost:5433/calitrans_qa_test")
    assert sample_data._parse_identity(identity) == ("localhost", 5433, "calitrans_qa_test")


def test_parse_identity_handles_bracketed_ipv6_host():
    identity = sample_data.target_identity("postgresql://u:p@[::1]:5433/calitrans_qa_test")
    assert identity == "[::1]:5433/calitrans_qa_test"
    assert sample_data._parse_identity(identity) == ("::1", 5433, "calitrans_qa_test")


def test_parse_identity_allows_missing_port():
    assert sample_data._parse_identity("localhost/calitrans_qa_test") == ("localhost", None, "calitrans_qa_test")


@pytest.mark.parametrize("malformed", ["", "no-slash-at-all", "/missing-host", "localhost:notaport/db"])
def test_parse_identity_rejects_malformed_input(malformed):
    with pytest.raises(sample_data.SafetyError):
        sample_data._parse_identity(malformed)


def test_hosts_match_loopback_class_only():
    assert sample_data._hosts_match("localhost", "127.0.0.1") is True
    assert sample_data._hosts_match("localhost", "::1") is True
    assert sample_data._hosts_match("127.0.0.1", "::1") is True
    assert sample_data._hosts_match("localhost", "db.example.test") is False
    assert sample_data._hosts_match("db.example.test", "db.other.test") is False
