from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable

import streamlit as st

from application.auth.models import AuthenticatedActor
from application.auth.permissions import (
    ADMIN_DIAGNOSTIC_OPTIONS,
    NAVIGATION_SECTIONS,
    allowed_admin_diagnostic_labels,
    allowed_top_level_sections,
)


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


# NAVIGATION_SECTIONS / ADMIN_DIAGNOSTIC_OPTIONS live in
# application/auth/permissions.py (framework-neutral), not here - that
# module is also the source of truth for role permissions, so the nav
# labels and the permission map can never drift apart. Re-exported here
# only for callers that imported them from this module previously.

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
    principal: AuthenticatedActor,
    refresh_callback: Callable[[], None],
    status_legend_renderer: Callable[[], None] | None = None,
) -> str:
    """Only offers sections/admin-tools permitted for principal.role - an
    unpermitted section is never rendered as a nav choice in the first
    place. pages_app/router.py independently re-checks the resolved
    section before rendering it (defense in depth, not the only gate)."""
    permitted_sections = allowed_top_level_sections(principal.role)

    with st.sidebar:
        if Path("assets/calitrans_logo.png").exists():
            st.image("assets/calitrans_logo.png", width=160)

        if not permitted_sections:
            st.error("Your account has no permitted sections. Contact an administrator.")
            st.stop()

        section = st.radio(
            "Navigation",
            permitted_sections,
        )
        if section == "Admin / Diagnostics":
            admin_options = allowed_admin_diagnostic_labels(principal.role)
            admin_label = st.selectbox(
                "Admin Tool",
                list(admin_options.keys()),
                key="admin_diagnostics_tool",
            )
            section = admin_options[admin_label]
            st.caption("Daily intake belongs in Operations Inbox. These tools are for setup, diagnostics, and exception work.")

        st.divider()

        if st.button("Refresh Data"):
            refresh_callback()
            st.rerun()

        st.divider()
        if section in STATUS_LEGEND_SECTIONS and status_legend_renderer is not None:
            status_legend_renderer()

    return str(section)
