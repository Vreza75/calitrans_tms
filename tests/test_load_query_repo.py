"""Phase 8: repositories/load_query_repo.py tests.

Split the same way tests/test_outbox_repo.py / tests/test_worker_job_repo.py
are: exact-match filters, sort allowlist, and pagination are plain SQL
that SQLite can execute too, so those run unconditionally. `ilike` and
the UNION ALL timeline query are PostgreSQL-specific (SQLite has no
ILIKE operator, and rejects `(select ...) union all (select ...)`'s
parenthesized-branch syntax even though it's valid Postgres) - gated
behind MIGRATION_TEST_DATABASE_URL, an explicit disposable-database env
var, never the app's real DATABASE_URL. Same pattern as
tests/test_worker_job_repo.py's TestPostgresWorkerJobLifecycle.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import event, text

import db_client
from repositories import load_query_repo

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")

_LOADS_COLUMNS = """
    id integer primary key autoincrement,
    type text,
    load_id text,
    booking_number text,
    reference_number text,
    container_number text,
    customer text,
    port text,
    warehouse text,
    address text,
    status text,
    driver_name text,
    truck_assigned text,
    chassis text,
    size text,
    billing_notes text,
    dispatcher_notes text,
    delivery_need_date text,
    document_cutoff text,
    load_date text,
    lfd text,
    invoice_status text,
    driver_pay_status text,
    closeout_stage text,
    steamship_line text,
    vessel_name text,
    terminal text,
    pickup_appointment text,
    delivery_appointment text,
    empty_return_location text,
    empty_return_date text,
    parent_booking_key text,
    container_sequence integer,
    container_total integer,
    updated_at text,
    created_at text
"""

_SAMPLE_ROWS = [
    {
        "id": i,
        "type": "Export" if i % 2 else "Import",
        "booking_number": f"BOOK{i:03d}",
        "reference_number": f"REF{i:03d}",
        "container_number": f"MSCU{i:07d}",
        "customer": "Continental Industries Group" if i <= 3 else "Apex Retail",
        "port": "Houston",
        "warehouse": "PBP Packaging",
        "status": "Active" if i <= 3 else "Delivered",
        "driver_name": "Juan Perez" if i % 2 else "Maria Lopez",
        "truck_assigned": f"T-{i}",
        "delivery_need_date": f"2026-08-{10 + i:02d}",
        "document_cutoff": f"2026-08-{5 + i:02d}",
        "invoice_status": "Ready" if i <= 2 else "Not Ready",
        "driver_pay_status": "Pending",
        "updated_at": f"2026-08-{10 + i:02d}T00:00:00",
        "created_at": f"2026-08-{1 + i:02d}T00:00:00",
    }
    for i in range(1, 6)
]

_INSERT_COLUMNS = (
    "id, type, booking_number, reference_number, container_number, customer, port, warehouse, status, "
    "driver_name, truck_assigned, delivery_need_date, document_cutoff, invoice_status, driver_pay_status, "
    "updated_at, created_at"
)
_INSERT_PLACEHOLDERS = (
    ":id, :type, :booking_number, :reference_number, :container_number, :customer, :port, :warehouse, :status, "
    ":driver_name, :truck_assigned, :delivery_need_date, :document_cutoff, :invoice_status, :driver_pay_status, "
    ":updated_at, :created_at"
)


@pytest.fixture
def sqlite_loads(monkeypatch, tmp_path):
    db_path = tmp_path / "load_query_repo_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(text(f"create table loads ({_LOADS_COLUMNS})"))
        for row in _SAMPLE_ROWS:
            conn.execute(text(f"insert into loads ({_INSERT_COLUMNS}) values ({_INSERT_PLACEHOLDERS})"), row)

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_filters_and_pagination_never_exceed_requested_limit(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters()
    df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=2)
    assert len(df) == 2


def test_status_filter_is_pushed_to_sql_not_python(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters(status="Active")
    df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
    assert len(df) == 3
    assert set(df["status"]) == {"Active"}


def test_service_flow_exact_match_filter(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters(service_flow="Export")
    df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
    assert set(df["type"]) == {"Export"}


def test_sort_ascending_and_descending(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters()
    desc_df = load_query_repo.list_loads_page(where_sql, params, sort_by="booking_number", sort_direction="desc", page=1, page_size=50)
    asc_df = load_query_repo.list_loads_page(where_sql, params, sort_by="booking_number", sort_direction="asc", page=1, page_size=50)
    assert list(desc_df["booking_number"]) == list(reversed(list(asc_df["booking_number"])))


def test_invalid_sort_field_falls_back_to_default_not_injected():
    sort_by, direction = load_query_repo.normalize_sort("'; drop table loads; --", "desc")
    assert sort_by == load_query_repo.DEFAULT_SORT_BY
    assert direction == "desc"


def test_invalid_sort_direction_falls_back_to_default():
    sort_by, direction = load_query_repo.normalize_sort("status", "sideways")
    assert direction == load_query_repo.DEFAULT_SORT_DIRECTION


def test_page_size_is_capped():
    assert load_query_repo.normalize_page_size(100000) == load_query_repo.MAX_PAGE_SIZE
    assert load_query_repo.normalize_page_size(0) == load_query_repo.DEFAULT_PAGE_SIZE
    assert load_query_repo.normalize_page_size(None) == load_query_repo.DEFAULT_PAGE_SIZE


def test_pages_do_not_overlap_or_duplicate(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters()
    page1 = load_query_repo.list_loads_page(where_sql, params, sort_by="id", sort_direction="asc", page=1, page_size=2)
    page2 = load_query_repo.list_loads_page(where_sql, params, sort_by="id", sort_direction="asc", page=2, page_size=2)
    page3 = load_query_repo.list_loads_page(where_sql, params, sort_by="id", sort_direction="asc", page=3, page_size=2)

    all_ids = list(page1["id"]) + list(page2["id"]) + list(page3["id"])
    assert all_ids == [1, 2, 3, 4, 5]
    assert len(set(all_ids)) == len(all_ids)


def test_count_loads_matches_filtered_row_count(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters(status="Delivered")
    assert load_query_repo.count_loads(where_sql, params) == 2


def test_get_load_detail_row_found_and_not_found(sqlite_loads):
    row = load_query_repo.get_load_detail_row(1)
    assert row is not None
    assert row["booking_number"] == "BOOK001"

    assert load_query_repo.get_load_detail_row(999999) is None


def test_delivery_date_window_filter(sqlite_loads):
    where_sql, params = load_query_repo.build_load_filters(delivery_after="2026-08-13", delivery_before="2026-08-14")
    df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
    assert set(df["id"]) == {3, 4}


def test_build_load_filters_binds_every_value_never_interpolates():
    """Same regression proof as work_item_repo's equivalent test - a
    value containing SQL metacharacters must appear only in the bound
    params dict, never spliced into the WHERE string itself."""
    where_sql, params = load_query_repo.build_load_filters(customer="O'Brien Logistics", search="'; DROP TABLE loads; --")
    assert "O'Brien" not in where_sql
    assert "DROP TABLE" not in where_sql
    assert params["customer"] == "%O'Brien Logistics%"
    assert params["search"] == "%'; DROP TABLE loads; --%"


def test_no_filters_produces_true_where_clause():
    where_sql, params = load_query_repo.build_load_filters()
    assert where_sql == "true"
    assert params == {}


# ---------------------------------------------------------------------------
# PostgreSQL-only: ILIKE search, and the UNION ALL timeline query (SQLite
# rejects the parenthesized-branch syntax even though it's valid Postgres).
# Gated behind an explicit disposable-database env var.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable PostgreSQL database.",
)
class TestPostgresLoadQueries:
    @pytest.fixture(autouse=True)
    def _schema(self, monkeypatch):
        monkeypatch.setattr(
            db_client, "get_secret", lambda name, default=None: MIGRATION_TEST_DATABASE_URL if name == "DATABASE_URL" else default
        )
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)
        engine = db_client.get_engine(MIGRATION_TEST_DATABASE_URL)

        with engine.begin() as conn:
            conn.execute(text("drop table if exists dispatch_messages"))
            conn.execute(text("drop table if exists status_events"))
            conn.execute(text("drop table if exists loads"))

        from pathlib import Path

        schema_sql = (Path(__file__).resolve().parent.parent / "database" / "schema.sql").read_text()
        with engine.begin() as conn:
            for statement in schema_sql.split(";"):
                if statement.strip():
                    conn.execute(text(statement))

        with engine.begin() as conn:
            for row in _SAMPLE_ROWS:
                conn.execute(
                    text(
                        "insert into loads (id, type, booking_number, reference_number, container_number, "
                        "customer, port, warehouse, status, driver_name, truck_assigned, delivery_need_date, "
                        "document_cutoff, invoice_status, driver_pay_status, updated_at, created_at) "
                        "values (:id, :type, :booking_number, :reference_number, :container_number, :customer, "
                        ":port, :warehouse, :status, :driver_name, :truck_assigned, "
                        "cast(:delivery_need_date as date), cast(:document_cutoff as date), :invoice_status, "
                        ":driver_pay_status, cast(:updated_at as timestamptz), cast(:created_at as timestamptz))"
                    ),
                    row,
                )
            conn.execute(text("alter sequence loads_id_seq restart with 6"))

        yield
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)

    @pytest.mark.parametrize(
        "term,expected_count",
        [("BOOK001", 1), ("MSCU0000002", 1), ("REF003", 1), ("Apex", 2), ("Perez", 3)],
    )
    def test_search_matches_booking_container_reference_customer_driver(self, term, expected_count):
        where_sql, params = load_query_repo.build_load_filters(search=term)
        df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
        assert len(df) == expected_count

    def test_search_is_case_insensitive(self):
        where_sql, params = load_query_repo.build_load_filters(search="apex")
        df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
        assert len(df) == 2

    def test_search_no_match_returns_empty(self):
        where_sql, params = load_query_repo.build_load_filters(search="NoSuchCustomerXYZ")
        df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
        assert len(df) == 0

    def test_combined_filters(self):
        where_sql, params = load_query_repo.build_load_filters(status="Active", customer="Continental")
        df = load_query_repo.list_loads_page(where_sql, params, sort_by="updated_at", sort_direction="desc", page=1, page_size=50)
        assert len(df) == 3

    def test_load_timeline_unions_status_events_and_dispatch_messages(self):
        engine = db_client.get_engine(MIGRATION_TEST_DATABASE_URL)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into status_events (load_id, old_status, new_status, notes, created_by, created_at) "
                    "values (1, 'New', 'Active', 'assigned', 'dispatcher@calitranscorp.com', now())"
                )
            )
            conn.execute(
                text(
                    "insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, sent_by) "
                    "values (1, 'driver_dispatch_sms', 'outbound', '+15551234567', 'hi', 'system:inbox-worker')"
                )
            )

        assert load_query_repo.count_load_timeline_events(1) == 2
        assert load_query_repo.count_load_timeline_events(2) == 0

        df = load_query_repo.list_load_timeline_page(1, page=1, page_size=50)
        assert len(df) == 2
        assert set(df["event_type"]) == {"status_change", "dispatch_message"}

    def test_load_communications_scoped_and_paginated(self):
        engine = db_client.get_engine(MIGRATION_TEST_DATABASE_URL)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, "
                    "sent_by, provider, delivery_status, provider_message_id) "
                    "values (1, 'driver_dispatch_sms', 'outbound', '+15551234567', 'hi', 'system:inbox-worker', "
                    "'twilio', 'delivered', 'SM123')"
                )
            )

        assert load_query_repo.count_load_communications(1) == 1
        assert load_query_repo.count_load_communications(2) == 0
        df = load_query_repo.list_load_communications_page(1, page=1, page_size=50)
        assert len(df) == 1
        assert df.iloc[0]["provider_message_id"] == "SM123"
