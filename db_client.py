from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import DATABASE_URL, DOCUMENT_STORAGE_DIR, EDITABLE_COLUMNS


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
    "Delivery Need Date",
    "LFD",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
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


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. Add it to .streamlit/secrets.toml or your environment."
        )
    return create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def read_df(sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def execute(sql: str, params: dict[str, Any] | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(sql), params or {})


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

        with get_engine().begin() as conn:
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

    def update_row_fields(self, row_id: int, updates: dict[str, Any]) -> None:
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

        old_status = None
        if "status" in db_updates:
            old_df = read_df("select status from loads where id = :id", {"id": row_id})
            if not old_df.empty:
                old_status = old_df.iloc[0]["status"]

        set_clause = ", ".join([f"{column} = :{column}" for column in db_updates])
        params = dict(db_updates)
        params["id"] = row_id

        execute(
            f"""
            update loads
            set {set_clause},
                updated_at = now()
            where id = :id
            """,
            params,
        )

        if "status" in db_updates and db_updates["status"] != old_status:
            execute(
                """
                insert into status_events (load_id, old_status, new_status, notes, created_by)
                values (:load_id, :old_status, :new_status, :notes, :created_by)
                """,
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