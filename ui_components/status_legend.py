from __future__ import annotations

import streamlit as st

from services.dispatch_workflow_service import STATUS_MEANINGS
from ui_components.status_badge import render_status_badge

STATUS_LEGEND_GROUPS = {
    "Intake / Verification": ["New Email", "Needs Review", "Order Created", "New", "Hold/Need Info", "Booking Verified", "Port Verified"],
    "Ready / Active": ["Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned", "Dispatched", "En Route to Pickup", "En Route To Delivery", "Ready for ProfitTools"],
    "Pickup / Loading": ["At Port", "At Pickup", "Loaded / Picked Up", "Loaded"],
    "Delivered / Return": ["Delivered", "Returning Empty", "POD Received"],
    "Issues / Stops": ["Hold/Need Info", "Awaiting Appointment", "Cancelled"],
    "Billing / Closed": ["Exported to ProfitTools", "Invoiced", "Closed"],
}


def render_status_legend() -> None:
    st.markdown("### Status Legend")
    st.caption("Dashboard row colors")

    for group_name, statuses in STATUS_LEGEND_GROUPS.items():
        st.markdown(f"**{group_name}**")
        for status in statuses:
            meaning = STATUS_MEANINGS.get(status, "")
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:8px; margin:6px 0 8px 0;">
                    {render_status_badge(status)}
                    <span style="font-size:12px; color:#64748b; line-height:1.2;">{meaning}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
