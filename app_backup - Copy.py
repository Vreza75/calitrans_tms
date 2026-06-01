from __future__ import annotations

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Calitrans Dispatch Center",
    page_icon="🚛",
    layout="wide",
)

st.markdown("""
<style>
.main {
    background-color: #f4f6f9;
}

.block-container {
    padding-top: 1.5rem;
}

.tms-header {
    background: linear-gradient(90deg, #0f172a, #1e3a8a);
    padding: 22px 28px;
    border-radius: 14px;
    color: white;
    margin-bottom: 20px;
}

.tms-header h1 {
    margin: 0;
    font-size: 34px;
}

.tms-header p {
    margin: 4px 0 0 0;
    color: #dbeafe;
}

.metric-card {
    background: white;
    padding: 18px;
    border-radius: 14px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
}

.metric-label {
    color: #64748b;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: .05em;
}

.metric-value {
    font-size: 30px;
    font-weight: 800;
    color: #0f172a;
}

.status-ready {
    color: #047857;
    font-weight: 700;
}

.status-hold {
    color: #b45309;
    font-weight: 700;
}

.status-cancelled {
    color: #b91c1c;
    font-weight: 700;
}

.status-assigned {
    color: #1d4ed8;
    font-weight: 700;
}
</style>
""", unsafe_allow_html=True)

from config import COLUMN_MAP, ACTIVE_STATUSES
from smartsheet_client import DispatchSmartsheetClient
from validators import validate_dispatch_rows
from profittools_export import export_ready_loads


st.markdown("""
<div class="tms-header">
    <h1>🚛 Calitrans Dispatch Center</h1>
    <p>Live TMS-style dispatch board powered by Smartsheet</p>
</div>
""", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def load_dispatch_data() -> pd.DataFrame:
    client = DispatchSmartsheetClient()
    sheet = client.get_sheet()
    return client.rows_to_dataframe(sheet)


def refresh_data():
    st.cache_data.clear()


try:
    df = load_dispatch_data()
except Exception as exc:
    st.error(f"Could not load Smartsheet data: {exc}")
    st.stop()


if df.empty:
    st.warning("No rows found in the dispatch sheet.")
    st.stop()


# Normalize expected columns if missing.
for col in COLUMN_MAP.values():
    if col not in df.columns:
        df[col] = None
active_count = len(df[df["Status"].isin(["Ready to Dispatch", "Assigned", "En Route to Pickup", "Hold/Need Info"])])
ready_count = len(df[df["Status"] == "Ready to Dispatch"])
assigned_count = len(df[df["Status"] == "Assigned"])
hold_count = len(df[df["Status"] == "Hold/Need Info"])
exported_count = len(df[df["Status"] == "Exported to ProfitTools"])

k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Active Loads</div>
        <div class="metric-value">{active_count}</div>
    </div>
    """, unsafe_allow_html=True)

with k2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Ready</div>
        <div class="metric-value">{ready_count}</div>
    </div>
    """, unsafe_allow_html=True)

with k3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Assigned</div>
        <div class="metric-value">{assigned_count}</div>
    </div>
    """, unsafe_allow_html=True)

with k4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Hold / Need Info</div>
        <div class="metric-value">{hold_count}</div>
    </div>
    """, unsafe_allow_html=True)

with k5:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Exported</div>
        <div class="metric-value">{exported_count}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)



display_columns = [
    "_row_id",
    "Load ID",
    "Customer",
    "Pickup",
    "Delivery",
    "Status",
    "Driver",
    "Truck",
    "Dispatcher Notes",
]

display_columns = [col for col in display_columns if col in df.columns]


tab_board, tab_ready, tab_problem, tab_export = st.tabs([
    "📋 Load Board",
    "✅ Ready to Dispatch",
    "⚠️ Problem Loads",
    "📤 ProfitTools Export",
])


def show_load_table(data, title):
    st.subheader(title)

    editable_columns = [
        "Status",
        "Driver",
        "Truck",
        "Dispatcher Notes",
    ]

    edited_data = st.data_editor(
        data[display_columns],
        use_container_width=True,
        hide_index=True,
        disabled=[col for col in display_columns if col not in editable_columns],
        key=f"{title}_editor",
    )

    if st.button(f"Save {title} Changes", key=f"{title}_save"):
        client = DispatchSmartsheetClient()
        changes_saved = 0

        original = data[display_columns].reset_index(drop=True)
        edited = edited_data.reset_index(drop=True)

        for i in range(len(edited)):
            row_id = int(edited.loc[i, "_row_id"])
            updates = {}

            for col in editable_columns:
                old_value = original.loc[i, col]
                new_value = edited.loc[i, col]

                old_value = "" if pd.isna(old_value) else old_value
                new_value = "" if pd.isna(new_value) else new_value

                if old_value != new_value:
                    updates[col] = new_value

            if updates:
                client.update_row_fields(row_id, updates)
                changes_saved += 1

        if changes_saved:
            st.success(f"Saved changes to {changes_saved} row(s).")
            refresh_data()
            st.rerun()
        else:
            st.info("No changes detected.")


with tab_board:
    show_load_table(df, "Load Board")

with tab_ready:
    ready_df = df[df["Status"] == "Ready to Dispatch"]
    show_load_table(ready_df, "Ready to Dispatch")

with tab_problem:
    problem_df = df[df["Status"].isin([
        "Hold/Need Info",
        "Problem Load",
        "Cancelled",
    ])]
    show_load_table(problem_df, "Problem Loads")

with tab_export:
    export_df = df[df["Status"] == "Exported to ProfitTools"]
    show_load_table(export_df, "ProfitTools Export")
    
def show_load_table(data, title):
    st.subheader(title)

    status_filter = st.multiselect(
        f"{title} - Status filter",
        options=sorted([x for x in data["Status"].dropna().unique()]),
        key=f"{title}_status",
    )

    driver_filter = st.multiselect(
        f"{title} - Driver filter",
        options=sorted([x for x in data["Driver"].dropna().unique()]),
        key=f"{title}_driver",
    )

    filtered_data = data.copy()

    if status_filter:
        filtered_data = filtered_data[filtered_data["Status"].isin(status_filter)]

    if driver_filter:
        filtered_data = filtered_data[filtered_data["Driver"].isin(driver_filter)]

    editable_columns = [
        "Status",
        "Driver",
        "Truck",
        "Dispatcher Notes",
    ]

    edited_data = st.data_editor(
        filtered_data[display_columns],
        use_container_width=True,
        hide_index=True,
        disabled=[col for col in display_columns if col not in editable_columns],
        key=f"{title}_editor",
    )

    if st.button(f"Save {title} Changes", key=f"{title}_save"):
        client = DispatchSmartsheetClient()
        changes_saved = 0

        original = filtered_data[display_columns].reset_index(drop=True)
        edited = edited_data.reset_index(drop=True)

        for i in range(len(edited)):
            row_id = int(edited.loc[i, "_row_id"])
            updates = {}

            for col in editable_columns:
                if col in edited.columns and col in original.columns:
                    old_value = original.loc[i, col]
                    new_value = edited.loc[i, col]

                    old_value = "" if pd.isna(old_value) else old_value
                    new_value = "" if pd.isna(new_value) else new_value

                    if old_value != new_value:
                        updates[col] = new_value

            if updates:
                client.update_row_fields(row_id, updates)
                changes_saved += 1

        if changes_saved:
            st.success(f"Saved changes to {changes_saved} row(s).")
            refresh_data()
            st.rerun()
        else:
            st.info("No changes detected.")

    return edited_data

