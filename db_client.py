from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from config import DOCUMENT_STORAGE_DIR, EDITABLE_COLUMNS, get_config_source, get_secret


SM_TO_DB_COLUMNS = {
    "TYPE": "type",
    "Load ID": "load_id",
    "Date": "load_date",
    "Booking Number": "booking_number",
    "Reference Number": "reference_number",
    "Container Number": "container_number",
    "Customer": "customer",
    "Port": "port",
    "Warehouse": "warehouse",
    "Address": "address",
    "Full Return Terminal": "full_return_terminal",
    "Document Cutoff": "document_cutoff",
    "Delivery Need Date": "delivery_need_date",
    "LFD": "lfd",
    "Status": "status",
    "Driver Name": "driver_name",
    "Truck Assigned": "truck_assigned",
    "Chassis": "chassis",
    "Size": "size",
    "Billing Notes": "billing_notes",
    "Dispatcher Notes": "dispatcher_notes",
    "Ready for ProfitTools": "ready_for_profittools",
    "Rate": "rate",
}

DB_TO_APP_COLUMNS = {value: key for key, value in SM_TO_DB_COLUMNS.items()}

LOAD_INSERT_COLUMNS = list(SM_TO_DB_COLUMNS.values())


# Fields the Order Detail Editor is allowed to update.
# This protects system columns like id, created_at, updated_at.
ORDER_EDITOR_EDITABLE_APP_COLUMNS = {
    "TYPE",
    "Load ID",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Customer",
    "Port",
    "Warehouse",
    "Address",
    "Document Cutoff",
    "Delivery Need Date",
    "LFD",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Size",
    "Dispatcher Notes",
}


def _editable_db_columns() -> set[str]:
    """
    Build allowed editable database-column names.

    Accepts both:
    - app/display names from config.py, like "TYPE"
    - raw db names, like "type"

    Also includes ORDER_EDITOR_EDITABLE_APP_COLUMNS as a safe fallback so the
    order editor does not fail if config.py is stale or partially updated.
    """
    allowed_app_columns = set(ORDER_EDITOR_EDITABLE_APP_COLUMNS)

    try:
        allowed_app_columns.update(EDITABLE_COLUMNS or [])
    except Exception:
        pass

    allowed_db_columns: set[str] = set()

    for column in allowed_app_columns:
        if column in SM_TO_DB_COLUMNS:
            allowed_db_columns.add(SM_TO_DB_COLUMNS[column])
        elif column in DB_TO_APP_COLUMNS:
            allowed_db_columns.add(column)

    return allowed_db_columns


def _column_to_db_column(column: str) -> str:
    """
    Convert either an app/display column name or a raw db column name into
    the database column name used by the loads table.
    """
    if column in SM_TO_DB_COLUMNS:
        return SM_TO_DB_COLUMNS[column]

    if column in DB_TO_APP_COLUMNS:
        return column

    raise ValueError(f"Unknown column from app: {column}")


def _normalize_date(value: Any) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _clean_value(column: str, value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, float) and pd.isna(value):
        return None

    if column in {"document_cutoff", "delivery_need_date", "load_date", "lfd"}:
        return _normalize_date(value)

    if column == "ready_for_profittools":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "yes", "1", "ready"}

    if column == "rate":
        try:
            return float(str(value).replace("$", "").replace(",", "").strip())
        except Exception:
            return None

    text_value = str(value).strip()
    return text_value if text_value else None


_ENGINE_CACHE: dict[str, Engine] = {}


def get_engine(database_url: str | None = None) -> Engine:
    database_url = str(database_url or get_secret("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError(
            f"DATABASE_URL is missing. Config source checked: {get_config_source('DATABASE_URL')}."
        )
    engine = _ENGINE_CACHE.get(database_url)
    if engine is None:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        _ENGINE_CACHE[database_url] = engine
    return engine


def read_df(sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with get_engine(get_secret("DATABASE_URL")).connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def execute(sql: str, params: dict[str, Any] | None = None) -> None:
    with get_engine(get_secret("DATABASE_URL")).begin() as conn:
        conn.execute(text(sql), params or {})


@contextmanager
def transaction() -> Iterator[Connection]:
    """One connection/transaction for a multi-statement business command.

    Every statement run through the yielded connection commits together on
    a clean exit and rolls back together on any exception - callers must
    execute all writes for one command through this connection instead of
    calling module-level execute()/read_df() (each of which opens and
    commits its own separate transaction), or the command is not atomic.
    """
    with get_engine(get_secret("DATABASE_URL")).begin() as conn:
        yield conn


def column_exists(table: str, column: str) -> bool:
    """Real DB-state check for schema-readiness guards. A single round trip
    that lets ensure_*_schema() functions skip their idempotent-but-slow
    ALTER/CREATE INDEX chains once already applied, instead of relying only
    on an in-memory session flag that doesn't survive process restarts.

    Used only by the explicit migration/admin DDL path (ensure_*_schema()
    functions, scripts/run_migrations.py) - callers there already intend
    to run DDL if the column turns out to be missing, so collapsing every
    exception to "not found" is an acceptable, narrow simplification.
    Normal page-render/request-path readiness checks must use
    check_schema_readiness() instead, which does not make that
    assumption - see its docstring."""
    try:
        with get_engine(get_secret("DATABASE_URL")).connect() as conn:
            result = conn.execute(
                text(
                    "select 1 from information_schema.columns "
                    "where table_name = :table and column_name = :column limit 1"
                ),
                {"table": table, "column": column},
            )
            return result.first() is not None
    except Exception:
        return False


_CREDENTIAL_PATTERN = re.compile(r"//[^/@\s]+:[^/@\s]+@")


def _redact(message: str) -> str:
    """Strip a user:password@ segment (as would appear in a DSN echoed
    into a driver error message) out of free-text error text."""
    return _CREDENTIAL_PATTERN.sub("//***@", str(message or ""))


# Postgres SQLSTATE class prefixes: '08' = connection exception, '28' =
# invalid authorization, '42501' = insufficient_privilege. See
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_CONNECTION_SQLSTATE_PREFIXES = ("08",)
_PERMISSION_SQLSTATES = {"42501"}
_PERMISSION_SQLSTATE_PREFIXES = ("28",)


@dataclass(frozen=True)
class SchemaReadiness:
    """Result of a non-mutating schema readiness check.

    `reason` is one of "ready", "schema_missing", "connection_error",
    "permission_error", or "unknown_error" - callers must not treat
    connection_error/permission_error/unknown_error as proof the schema
    is missing (that was the Phase 1 correction: column_exists() used to
    collapse every exception, including a dropped connection, into
    "column not found", which could make a render-path readiness check
    look like a fresh-install case when the real problem was network or
    credentials)."""

    ready: bool
    reason: str
    detail: str = ""


def check_schema_readiness(table: str, column: str) -> SchemaReadiness:
    """Read-only readiness check for interactive render/request paths.

    Never runs DDL, and never assumes a connection failure or permission
    error means the schema is missing - each is reported as its own
    reason so the caller can show an accurate message (and, for
    connection/permission problems, must not suggest running migrations,
    since that wouldn't fix either)."""
    try:
        with get_engine(get_secret("DATABASE_URL")).connect() as conn:
            result = conn.execute(
                text(
                    "select 1 from information_schema.columns "
                    "where table_name = :table and column_name = :column limit 1"
                ),
                {"table": table, "column": column},
            )
            found = result.first() is not None
            return SchemaReadiness(ready=found, reason="ready" if found else "schema_missing")
    except (OperationalError, ProgrammingError) as exc:
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None) or ""
        if pgcode in _PERMISSION_SQLSTATES or pgcode.startswith(_PERMISSION_SQLSTATE_PREFIXES):
            return SchemaReadiness(ready=False, reason="permission_error", detail=_redact(str(exc)))
        if pgcode.startswith(_CONNECTION_SQLSTATE_PREFIXES) or isinstance(exc, OperationalError):
            return SchemaReadiness(ready=False, reason="connection_error", detail=_redact(str(exc)))
        return SchemaReadiness(ready=False, reason="unknown_error", detail=_redact(str(exc)))
    except Exception as exc:
        return SchemaReadiness(ready=False, reason="unknown_error", detail=_redact(str(exc)))


class DispatchDatabaseClient:
    """Postgres-backed replacement for the previous Smartsheet client."""

    def get_sheet_as_dataframe(self, sheet_id: str | None = None) -> pd.DataFrame:
        # Compatibility method for older UI code.
        return self.rows_to_dataframe()

    def rows_to_dataframe(self) -> pd.DataFrame:
        df = read_df(
            """
            select
                id as _row_id,
                type,
                load_id,
                load_date,
                booking_number,
                reference_number,
                container_number,
                customer,
                port,
                warehouse,
                address,
                document_cutoff,
                delivery_need_date,
                lfd,
                status,
                driver_name,
                truck_assigned,
                chassis,
                size,
                billing_notes,
                dispatcher_notes,
                ready_for_profittools,
                rate,
                created_at,
                updated_at
            from loads
            order by updated_at desc, id desc
            """
        )

        if df.empty:
            return pd.DataFrame(columns=["_row_id"] + list(SM_TO_DB_COLUMNS.keys()))

        df = df.rename(columns=DB_TO_APP_COLUMNS)
        df["Created Date"] = df.get("created_at")
        return df

    def add_row(self, row_data: dict[str, Any]):
        db_data: dict[str, Any] = {}

        for app_col, db_col in SM_TO_DB_COLUMNS.items():
            db_data[db_col] = _clean_value(db_col, row_data.get(app_col))

        if not db_data.get("booking_number"):
            raise ValueError("Booking Number is required before creating a load.")

        columns = [col for col, val in db_data.items() if val is not None]
        placeholders = [f":{col}" for col in columns]

        sql = f"""
            insert into loads ({", ".join(columns)})
            values ({", ".join(placeholders)})
            returning id
        """

        with get_engine(get_secret("DATABASE_URL")).begin() as conn:
            new_id = conn.execute(
                text(sql),
                {col: db_data[col] for col in columns},
            ).scalar_one()

            conn.execute(
                text(
                    """
                    insert into status_events (load_id, new_status, notes, created_by)
                    values (:load_id, :new_status, :notes, :created_by)
                    """
                ),
                {
                    "load_id": new_id,
                    "new_status": db_data.get("status") or "New",
                    "notes": "Load created",
                    "created_by": "streamlit",
                },
            )

        class CreatedRow:
            def __init__(self, row_id: int) -> None:
                self.id = row_id

        return CreatedRow(int(new_id))

    def update_row_fields(self, row_id: int, updates: dict[str, Any], *, conn: Connection | None = None) -> None:
        """Update editable columns on one loads row.

        `conn` is optional and defaults to None, so every existing caller
        keeps its current one-call-one-transaction behavior unchanged. Pass
        an open connection (see db_client.transaction()) to run this update
        as part of a larger multi-statement business command instead - the
        write then commits or rolls back with the rest of that command."""
        allowed_db_columns = _editable_db_columns()
        db_updates: dict[str, Any] = {}

        for column, value in updates.items():
            # Never allow row/system identifiers to be updated.
            if column in {"id", "_row_id", "created_at", "updated_at", "Created Date"}:
                continue

            db_column = _column_to_db_column(column)

            if db_column not in allowed_db_columns:
                raise ValueError(
                    f"Columns not allowed to update from app: [{column}] "
                    f"| db_column={db_column} "
                    f"| allowed={sorted(allowed_db_columns)}"
                )

            db_updates[db_column] = _clean_value(db_column, value)

        if not db_updates:
            return

        status_sql = text("select status from loads where id = :id")
        update_sql = text(
            f"""
            update loads
            set {", ".join(f"{column} = :{column}" for column in db_updates)},
                updated_at = now()
            where id = :id
            """
        )
        audit_sql = text(
            """
            insert into status_events (load_id, old_status, new_status, notes, created_by)
            values (:load_id, :old_status, :new_status, :notes, :created_by)
            """
        )

        params = dict(db_updates)
        params["id"] = row_id

        if conn is not None:
            old_status = None
            if "status" in db_updates:
                old_row = conn.execute(status_sql, {"id": row_id}).first()
                old_status = old_row[0] if old_row is not None else None

            conn.execute(update_sql, params)

            if "status" in db_updates and db_updates["status"] != old_status:
                conn.execute(
                    audit_sql,
                    {
                        "load_id": row_id,
                        "old_status": old_status,
                        "new_status": db_updates["status"],
                        "notes": "Status updated from Streamlit",
                        "created_by": "streamlit",
                    },
                )
            return

        old_status = None
        if "status" in db_updates:
            old_df = read_df(str(status_sql), {"id": row_id})
            if not old_df.empty:
                old_status = old_df.iloc[0]["status"]

        execute(str(update_sql), params)

        if "status" in db_updates and db_updates["status"] != old_status:
            execute(
                str(audit_sql),
                {
                    "load_id": row_id,
                    "old_status": old_status,
                    "new_status": db_updates["status"],
                    "notes": "Status updated from Streamlit",
                    "created_by": "streamlit",
                },
            )

    def attach_file_to_row(self, row_id: int, uploaded_file, source: str = "streamlit") -> None:
        storage_dir = Path(DOCUMENT_STORAGE_DIR)
        storage_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(uploaded_file.name).name
        stored_filename = f"load_{row_id}_{filename}"
        file_path = storage_dir / stored_filename

        uploaded_file.seek(0)
        data = uploaded_file.read()
        file_path.write_bytes(data)

        execute(
            """
            insert into documents (load_id, document_type, filename, file_path, source)
            values (:load_id, :document_type, :filename, :file_path, :source)
            """,
            {
                "load_id": row_id,
                "document_type": "load_pdf",
                "filename": filename,
                "file_path": str(file_path),
                "source": source,
            },
        )
