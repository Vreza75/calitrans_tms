# pages_app/dispatcher_workspace.py

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.dispatcher_workspace_service import (
    load_dispatcher_workspace_metrics,
    load_dispatcher_work_queue,
    load_lfd_risk_loads,
    load_selected_work_item,
)
from ui_components.work_queue_panel import render_work_queue_panel
from ui_components.dispatcher_action_panel import render_dispatcher_action_panel


def render_dispatcher_workspace() -> None:
    st.markdown("## AI Dispatcher Workspace")
    st.caption("Experimental workbench for exception cases, escalations, and AI-assisted action review. Daily intake should stay in Operations Inbox.")

    metrics = load_dispatcher_workspace_metrics()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Open Cases", metrics.get("open_cases", 0))
    m2.metric("Waiting Customer", metrics.get("waiting_customer", 0))
    m3.metric("New Orders", metrics.get("new_orders", 0))
    m4.metric("LFD Today", metrics.get("lfd_today", 0))
    m5.metric("Overdue Tasks", metrics.get("overdue_tasks", 0))

    st.divider()

    left, right = st.columns([1.25, 1])

    with left:
        queue_df = load_dispatcher_work_queue(limit=150)
        selected_id = render_work_queue_panel(queue_df)
        if selected_id:
            st.session_state["dispatcher_workspace_selected_intake_id"] = selected_id

    with right:
        selected_id = st.session_state.get("dispatcher_workspace_selected_intake_id")
        if selected_id:
            selected = load_selected_work_item(int(selected_id))
            action = render_dispatcher_action_panel(
                record=selected.get("record", {}),
                case=selected.get("case", {}),
                load=selected.get("load", {}),
                tasks=selected.get("tasks", []),
            )
            if action and action.get("action") != "none":
                st.info(f"Action selected: {action.get('action')}. Wire this to task/load/case service next.")
        else:
            st.info("Select a work item to review details and actions.")

    with st.expander("LFD / Risk Watch", expanded=False):
        lfd_df = load_lfd_risk_loads(limit=25)
        if lfd_df.empty:
            st.success("No LFD risk loads found for the next 48 hours.")
        else:
            st.dataframe(lfd_df, use_container_width=True, hide_index=True)
