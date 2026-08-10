# ui_components/work_queue_panel.py

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_work_queue_panel(queue_df: pd.DataFrame, *, key_prefix: str = "dispatcher_workspace") -> int | None:
    """Render dispatcher work queue and return selected intake id."""
    st.markdown("### Work Queue")

    if queue_df is None or queue_df.empty:
        st.info("No open dispatcher work found.")
        return None

    buckets = ["All"] + sorted(queue_df.get("workspace_bucket", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
    selected_bucket = st.radio(
        "Queue",
        buckets,
        horizontal=True,
        key=f"{key_prefix}_bucket",
    )

    display_df = queue_df.copy()
    if selected_bucket != "All":
        display_df = display_df[display_df["workspace_bucket"].eq(selected_bucket)].copy()

    if display_df.empty:
        st.info(f"No items in {selected_bucket}.")
        return None

    cols = [
        "id",
        "workspace_bucket",
        "work_item_title",
        "customer_hint",
        "reference_hint",
        "next_action",
        "confidence_score",
        "source_received_at",
    ]
    available_cols = [col for col in cols if col in display_df.columns]
    table = display_df[available_cols].rename(
        columns={
            "id": "Request",
            "workspace_bucket": "Bucket",
            "work_item_title": "Work Item",
            "customer_hint": "Customer",
            "reference_hint": "Reference",
            "next_action": "Next Action",
            "confidence_score": "AI %",
            "source_received_at": "Received",
        }
    )

    event = st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key=f"{key_prefix}_table",
    )

    if event.selection.rows:
        selected_idx = event.selection.rows[0]
        return int(display_df.iloc[selected_idx]["id"])

    return None
