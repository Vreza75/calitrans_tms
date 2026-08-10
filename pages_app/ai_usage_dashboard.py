# pages_app/ai_usage_dashboard.py

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db_client import SchemaNotReadyError
from repositories.ai_usage_repo import (
    load_ai_usage_by_agent,
    load_ai_usage_recent,
    load_ai_usage_summary,
)


def _currency(value) -> str:
    try:
        return f"${float(value or 0):,.4f}"
    except Exception:
        return "$0.0000"


def render_ai_usage_dashboard() -> None:
    st.markdown("### AI Usage Dashboard")
    st.caption("Track OpenAI/API usage by agent, model, cost, latency, and failures.")

    today = date.today()
    default_start = today - timedelta(days=7)

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        start_date = st.date_input("Start Date", value=default_start, key="ai_usage_start_date")
    with c2:
        end_date = st.date_input("End Date", value=today, key="ai_usage_end_date")
    with c3:
        st.info("Use this dashboard to verify agents are running and control monthly AI spend.")

    try:
        summary = load_ai_usage_summary(start_date, end_date)
    except SchemaNotReadyError as exc:
        st.error(f"AI usage schema is not ready: {exc}")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("AI Calls", int(summary.get("calls", 0) or 0))
    k2.metric("Total Tokens", f"{int(summary.get('total_tokens', 0) or 0):,}")
    k3.metric("Estimated Cost", _currency(summary.get("estimated_cost", 0)))
    k4.metric("Avg Latency", f"{float(summary.get('avg_latency_seconds', 0) or 0):.2f}s")

    st.divider()

    by_agent = load_ai_usage_by_agent(start_date, end_date)
    st.markdown("#### Usage by Agent")
    if by_agent.empty:
        st.info("No AI usage records found for this date range yet.")
    else:
        display = by_agent.copy()
        display["estimated_cost"] = display["estimated_cost"].astype(float).map(lambda value: f"${value:,.4f}")
        display = display.rename(
            columns={
                "agent_name": "Agent",
                "model": "Model",
                "calls": "Calls",
                "total_tokens": "Total Tokens",
                "input_tokens": "Input Tokens",
                "output_tokens": "Output Tokens",
                "estimated_cost": "Estimated Cost",
                "avg_latency_seconds": "Avg Latency",
                "successful_calls": "Success",
                "failed_calls": "Failed",
            }
        )
        st.dataframe(display, use_container_width=True, hide_index=True)

    st.markdown("#### Recent AI Calls")
    recent = load_ai_usage_recent(limit=100)
    if recent.empty:
        st.info("No recent AI calls logged yet.")
    else:
        recent = recent.copy()
        recent["created_at"] = pd.to_datetime(recent["created_at"], errors="coerce").dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
        recent["estimated_cost"] = recent["estimated_cost"].astype(float).map(lambda value: f"${value:,.5f}")
        recent = recent.rename(
            columns={
                "created_at": "Time",
                "task": "Task",
                "agent_name": "Agent",
                "model": "Model",
                "ok": "OK",
                "input_tokens": "Input",
                "output_tokens": "Output",
                "total_tokens": "Total",
                "estimated_cost": "Cost",
                "latency_seconds": "Latency",
                "error": "Error",
            }
        )
        st.dataframe(recent, use_container_width=True, hide_index=True)
