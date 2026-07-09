from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from services.dispatch_workflow_service import _get_status_border_color
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter


def render_calendar_view(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Calendar View")
    st.caption("Monthly calendar showing Booking and Customer by Delivery Need Date.")

    calendar_df = df.copy()
    selected_flow = render_service_flow_filter("calendar_service_flow")
    calendar_df = apply_service_flow_filter(calendar_df, selected_flow)

    calendar_df["Delivery Need Date Parsed"] = pd.to_datetime(
        calendar_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce",
    )

    calendar_df = calendar_df[
        calendar_df["Delivery Need Date Parsed"].notna()
    ].copy()

    if calendar_df.empty:
        st.warning("No loads have valid Delivery Need Dates.")
        return

    selected_month = st.date_input(
        "Select Month",
        value=date.today(),
    )

    month_start = pd.Timestamp(selected_month).replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(1)

    month_df = calendar_df[
        calendar_df["Delivery Need Date Parsed"].between(month_start, month_end)
    ].copy()

    st.markdown(f"### {month_start.strftime('%B %Y')}")

    days = pd.date_range(month_start, month_end, freq="D")
    first_weekday = month_start.weekday()  # Monday = 0
    calendar_slots = [None] * first_weekday + list(days)

    while len(calendar_slots) % 7 != 0:
        calendar_slots.append(None)

    weekday_cols = st.columns(7)
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for col, day_name in zip(weekday_cols, weekdays):
        col.markdown(f"**{day_name}**")

    for week_start in range(0, len(calendar_slots), 7):
        cols = st.columns(7)

        for col, day_value in zip(cols, calendar_slots[week_start:week_start + 7]):
            if day_value is None:
                col.markdown(
                    """
                    <div style="min-height:130px; border:1px solid #e5e7eb; border-radius:10px; background:#f8fafc;"></div>
                    """,
                    unsafe_allow_html=True,
                )
                continue

            day_loads = month_df[
                month_df["Delivery Need Date Parsed"].dt.date.eq(day_value.date())
            ]

            load_html = ""

            for _, row in day_loads.iterrows():
                booking = str(row.get("Booking Number", "") or "-")
                customer = str(row.get("Customer", "") or "-")
                status = str(row.get("Status", "") or "")
                color = _get_status_border_color(status)

                load_html += f"""
                <div style="
                    margin-top:6px;
                    padding:6px;
                    border-left:4px solid {color};
                    background:white;
                    border-radius:7px;
                    font-size:11px;
                    line-height:1.2;
                ">
                    <b>{booking}</b><br>
                    {customer}
                </div>
                """

            col.markdown(
                f"""
                <div style="
                    min-height:130px;
                    border:1px solid #e5e7eb;
                    border-radius:10px;
                    background:#f8fafc;
                    padding:8px;
                    margin-bottom:8px;
                ">
                    <div style="font-weight:700; font-size:13px;">
                        {day_value.day}
                    </div>
                    {load_html}
                </div>
                """,
                unsafe_allow_html=True,
            )
