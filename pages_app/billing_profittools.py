from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from services.profittools_export import export_ready_loads
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter


def render_billing(df: pd.DataFrame) -> None:
    """Render billing readiness and ProfitTools export workflow."""
    st.subheader("Billing / ProfitTools")

    status_column = "Status"
    if status_column not in df.columns:
        st.warning("Status column is missing from load data.")
        return

    selected_flow = render_service_flow_filter("billing_service_flow")
    work_df = apply_service_flow_filter(df, selected_flow)

    ready_statuses = ["POD Received", "Ready for ProfitTools", "Exported to ProfitTools", "Invoiced"]
    ready = work_df[work_df[status_column].isin(ready_statuses)]

    display_columns = [
        column
        for column in ["Booking Number", "Customer", "Container Number", "Status", "Billing Notes", "Rate"]
        if column in ready.columns
    ]
    st.dataframe(ready[display_columns] if display_columns else ready, use_container_width=True, hide_index=True)

    if st.button("Generate ProfitTools Ready CSV"):
        path = export_ready_loads(work_df)
        export_path = Path(path)
        st.success(f"Export created: {export_path}")
        st.download_button(
            "Download CSV",
            data=export_path.read_bytes(),
            file_name=export_path.name,
            mime="text/csv",
        )
