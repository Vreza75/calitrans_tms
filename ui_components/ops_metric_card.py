from __future__ import annotations

from html import escape

import streamlit as st


def render_ops_metric_card(label: str, value, subtext: str = "") -> None:
    st.markdown(
        f"""
        <div class="ops-metric-card">
            <div class="ops-metric-label">{escape(str(label))}</div>
            <div class="ops-metric-value">{escape(str(value))}</div>
            {f'<div class="ops-metric-sub">{escape(str(subtext))}</div>' if subtext else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


_render_ops_metric_card = render_ops_metric_card
