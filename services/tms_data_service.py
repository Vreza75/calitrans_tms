from __future__ import annotations

import pandas as pd
import streamlit as st

from db_client import DispatchDatabaseClient, read_df
from services.workflow_constants import normalize_service_flow
from services.workflow_status import enrich_load_workflow_columns


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
    """Clear cached Streamlit data after database writes."""
    st.cache_data.clear()


@st.cache_data(ttl=45)
def load_dispatch_data() -> pd.DataFrame:
    """Read core dispatch/load rows from the configured database client."""
    return DispatchDatabaseClient().rows_to_dataframe()


@st.cache_data(show_spinner=False, ttl=45)
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


@st.cache_data(show_spinner=False, ttl=45)
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
                last_driver_update
            from loads
            """
        )
    except Exception:
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
