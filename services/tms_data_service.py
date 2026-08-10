from __future__ import annotations

import logging

import pandas as pd

from db_client import DispatchDatabaseClient, read_df
from services.workflow_constants import normalize_service_flow
from services.workflow_status import enrich_load_workflow_columns
from utils.ttl_cache import ttl_cache

logger = logging.getLogger(__name__)


SUMMARY_COLUMNS = [
    "_row_id",
    "TYPE",
    "Booking Number",
    "Load ID",
    "Customer",
    "Container Number",
    "Warehouse",
    "Delivery Need Date",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Port",
    "LFD",
    "Dispatcher Notes",
]

EXT_LOAD_COLUMNS = [
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "chassis_provider",
    "pickup_reference",
    "delivery_reference",
    "invoice_status",
    "driver_pay_status",
    "customer_rate",
    "carrier_pay",
    "accessorials",
    "margin",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
    "parent_booking_key",
    "container_sequence",
    "container_total",
]

LOAD_SEARCH_COLUMNS = [
    "Booking Number",
    "Load ID",
    "Reference Number",
    "Container Number",
    "Customer",
    "Port",
    "Warehouse",
    "Address",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Status",
    "Dispatcher Notes",
]

BASE_OPTIONAL_COLUMNS = [
    "Reference Number",
    "Address",
    "Billing Notes",
    "Ready for ProfitTools",
    "Rate",
    "Customer Email",
    "Contact Email",
    "Public Notes",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
    "pickup_appointment",
    "delivery_appointment",
    "terminal",
    "empty_return_location",
]


def refresh_data() -> None:
    """Clear this module's caches after database writes - targeted, not a
    global st.cache_data.clear() (that would also wipe every unrelated
    cache elsewhere in the app)."""
    load_dispatch_data.clear()
    load_tms_data.clear()
    get_ext_df.clear()


@ttl_cache(ttl_seconds=45)
def load_dispatch_data() -> pd.DataFrame:
    """Read core dispatch/load rows from the configured database client."""
    return DispatchDatabaseClient().rows_to_dataframe()


@ttl_cache(ttl_seconds=45)
def load_tms_data() -> pd.DataFrame:
    """Load, normalize, and enrich TMS load data for Streamlit pages."""
    return enrich_load_workflow_columns(merge_ext(clean_df(load_dispatch_data())))


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize required dashboard columns and provide safe empty defaults."""
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    for col in SUMMARY_COLUMNS + BASE_OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["TYPE"] = df["TYPE"].apply(lambda value: normalize_service_flow(value, default=""))
    df["Status"] = df["Status"].astype(str).str.strip()
    df["Booking Number"] = df["Booking Number"].astype(str).str.strip()

    return df


@ttl_cache(ttl_seconds=45)
def get_ext_df() -> pd.DataFrame:
    """Read additional PortPro-style fields directly from the loads table."""
    try:
        return read_df(
            """
            select
                id as _row_id,
                steamship_line,
                vessel_name,
                terminal,
                pickup_appointment,
                delivery_appointment,
                empty_return_location,
                empty_return_date,
                chassis_provider,
                pickup_reference,
                delivery_reference,
                invoice_status,
                driver_pay_status,
                customer_rate,
                carrier_pay,
                accessorials,
                margin,
                current_location,
                eta,
                live_load_status,
                live_unload_status,
                last_driver_update,
                parent_booking_key,
                container_sequence,
                container_total
            from loads
            """
        )
    except Exception as exc:
        # A single missing/renamed column here previously failed the whole
        # query silently, blanking out every extended field app-wide
        # (steamship line, rates, live tracking, container grouping, etc.)
        # with no visible error. Surface it instead of hiding it.
        logger.warning("Could not load extended load fields - some columns may be showing blank: %s", exc)
        return pd.DataFrame()


def merge_ext(df: pd.DataFrame) -> pd.DataFrame:
    """Merge extended load fields into the main dispatch dataframe."""
    ext = get_ext_df()
    if ext.empty or "_row_id" not in df.columns:
        df = df.copy()
        for column in EXT_LOAD_COLUMNS:
            if column not in df.columns:
                df[column] = ""
        return df

    ext_columns = [column for column in ext.columns if column != "_row_id"]
    base = df.drop(columns=[column for column in ext_columns if column in df.columns], errors="ignore")
    merged = base.merge(ext, on="_row_id", how="left")

    for column in EXT_LOAD_COLUMNS:
        if column not in merged.columns:
            merged[column] = ""
        else:
            merged[column] = merged[column].fillna("")

    return merged
