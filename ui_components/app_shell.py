from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable

import streamlit as st


def load_local_env_file(base_dir: str | Path | None = None) -> None:
    project_root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value and os.getenv(key) in [None, ""]:
            os.environ[key] = value



def configure_streamlit_page() -> None:
    st.set_page_config(
        page_title="CaliTrans TMS",
        page_icon="CT",
        layout="wide",
        initial_sidebar_state="expanded",
    )


NAVIGATION_SECTIONS = [
    "Operations Inbox",
    "Orders/Load Management",
    "Dispatch Board",
    "Active Status",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Dashboard",
    "Admin / Diagnostics",
]

ADMIN_DIAGNOSTIC_OPTIONS = {
    "Master Data": "Master Data",
    "Email Imports / Diagnostics": "Email Imports",
    "Port Houston Setup / Testing": "Port Houston Integration",
    "Validation": "Validation",
    "AI Dispatcher Workspace (Experimental)": "AI Dispatcher Workspace",
}

LOAD_DATA_SECTIONS = {
    "Dashboard",
    "Orders/Load Management",
    "Active Status",
    "Dispatch Board",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Port Houston Integration",
    "Validation",
}

STATUS_LEGEND_SECTIONS = {"Active Status", "Dispatch Board", "Calendar View"}



def load_css() -> None:
    theme = Path("theme.css")
    if theme.exists():
        st.markdown(theme.read_text(encoding="utf-8"), unsafe_allow_html=True)

    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.2rem;}
        .metric-card {
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            padding: 16px;
            background: white;
            box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06);
        }
        .load-card {
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 12px;
            margin-bottom: 10px;
            background: #ffffff;
            box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
        }
        .load-card-title {
            font-weight: 700;
            font-size: 0.95rem;
            color: #0f172a;
        }
        .load-card-small {
            color: #475569;
            font-size: 0.82rem;
        }
        .status-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #e0f2fe;
            color: #075985;
            font-weight: 700;
        }
        .danger-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #fee2e2;
            color: #991b1b;
            font-weight: 700;
        }
        .success-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #dcfce7;
            color: #166534;
            font-weight: 700;
        }
        html, body, [class*="css"] {
            font-family: "Inter", "Segoe UI", Arial, sans-serif;
        }
        [data-testid="stAppViewContainer"] {
            background: #f6f8fb;
        }
        .block-container {
            max-width: 1320px;
            padding-top: 0.8rem;
            padding-bottom: 2rem;
        }
        h1, h2, h3 {
            letter-spacing: 0;
            color: #0f172a;
        }
        h2, h3 {
            font-weight: 750;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: none;
        }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            padding: 10px 12px;
            box-shadow: none;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.72rem;
            color: #475569;
            font-weight: 650;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.35rem;
            color: #0f172a;
            font-weight: 750;
        }
        .stButton > button {
            border-radius: 8px !important;
            min-height: 2.35rem;
            padding: 0.45rem 0.9rem;
            font-size: 0.82rem;
            font-weight: 700;
            box-shadow: none !important;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px !important;
            border-bottom: 1px solid #d8e0ec !important;
        }
        .stTabs [data-baseweb="tab"] {
            min-height: 38px !important;
            height: 38px !important;
            padding: 0 10px !important;
            border-radius: 8px 8px 0 0 !important;
            font-size: 0.78rem !important;
            font-weight: 650 !important;
            box-shadow: none !important;
        }
        .stTabs [aria-selected="true"] {
            background: #fff8d7 !important;
            border-color: #ffd200 !important;
            border-bottom: 3px solid #ffd200 !important;
        }
        [data-testid="stDataFrame"] {
            border-radius: 8px !important;
            border: 1px solid #d8e0ec !important;
            box-shadow: none !important;
        }
        .ops-header {
            margin: 0.25rem 0 0.85rem 0;
            padding: 0;
        }
        .ops-kicker {
            color: #64748b;
            font-size: 0.78rem;
            font-weight: 650;
            text-transform: uppercase;
            letter-spacing: 0;
            margin-bottom: 0.25rem;
        }
        .ops-title {
            color: #0f172a;
            font-size: 1.35rem;
            line-height: 1.2;
            font-weight: 800;
            margin: 0;
        }
        .ops-subtitle {
            color: #64748b;
            font-size: 0.86rem;
            line-height: 1.45;
            margin-top: 0.35rem;
            max-width: 780px;
        }
        .ops-metric-card {
            background: #ffffff;
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            padding: 10px 12px;
            min-height: 74px;
        }
        .ops-metric-label {
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
        }
        .ops-metric-value {
            color: #0f172a;
            font-size: 1.45rem;
            font-weight: 800;
            line-height: 1.05;
        }
        .ops-metric-sub {
            color: #64748b;
            font-size: 0.72rem;
            margin-top: 0.25rem;
        }
        .ops-alert {
            border: 1px solid #cfe0f8;
            border-radius: 8px;
            background: #eaf3ff;
            color: #064b91;
            padding: 0.75rem 0.85rem;
            font-size: 0.84rem;
            line-height: 1.45;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def image_to_base64(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return base64.b64encode(file_path.read_bytes()).decode("utf-8")


def show_header() -> None:
    banner_b64 = image_to_base64("assets/header_banner.png")
    if banner_b64:
        st.markdown(
            f"""
            <div class="banner-wrapper">
                <img class="header-banner" src="data:image/png;base64,{banner_b64}" />
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.title("CaliTrans TMS")
    st.caption("Drayage dispatch, container tracking, billing readiness, and operations dashboard")



def render_sidebar(
    *,
    refresh_callback: Callable[[], None],
    status_legend_renderer: Callable[[], None] | None = None,
) -> str:
    with st.sidebar:
        if Path("assets/calitrans_logo.png").exists():
            st.image("assets/calitrans_logo.png", width=160)

        section = st.radio(
            "Navigation",
            NAVIGATION_SECTIONS,
        )
        if section == "Admin / Diagnostics":
            admin_label = st.selectbox(
                "Admin Tool",
                list(ADMIN_DIAGNOSTIC_OPTIONS.keys()),
                key="admin_diagnostics_tool",
            )
            section = ADMIN_DIAGNOSTIC_OPTIONS[admin_label]
            st.caption("Daily intake belongs in Operations Inbox. These tools are for setup, diagnostics, and exception work.")

        st.divider()

        if st.button("Refresh Data"):
            refresh_callback()
            st.rerun()

        st.divider()
        if section in STATUS_LEGEND_SECTIONS and status_legend_renderer is not None:
            status_legend_renderer()

    return str(section)
