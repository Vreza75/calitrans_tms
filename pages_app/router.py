from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_components.app_shell import LOAD_DATA_SECTIONS, render_sidebar
from ui_components.status_legend import render_status_legend
from services.tms_data_service import load_tms_data, refresh_data

from admin_pages import render_master_data_admin
from pages_app.active_status import render_active_status_view
from pages_app.billing_profittools import render_billing
from pages_app.calendar_view import render_calendar_view
from pages_app.dashboard import render_dashboard
from pages_app.dispatch_board import render_dispatch_board_focused
from pages_app.documents import render_documents
from pages_app.email_imports import render_email_imports
from pages_app.orders_management import render_orders_management
from pages_app.port_houston_integration import (
    render_load_port_houston_panel as _render_load_port_houston_panel,
    render_port_houston_integration,
)
from pages_app.validation import render_validation


def _load_current_tms_data_or_stop() -> pd.DataFrame:
    try:
        return load_tms_data()
    except Exception as exc:
        st.error(f"Could not load PostgreSQL/Supabase data: {exc}")
        st.info("Make sure DATABASE_URL is set and database/schema.sql has been run.")
        st.stop()


def _render_selected_page(section: str, df: pd.DataFrame) -> None:
    if section == "Operations Inbox":
        from pages_app.operations_inbox import render_operations_inbox

        render_operations_inbox()
    elif section == "AI Dispatcher Workspace":
        from pages_app.dispatcher_workspace import render_dispatcher_workspace

        render_dispatcher_workspace()
    elif section == "Port Houston Integration":
        render_port_houston_integration(df)
    elif section == "Dashboard":
        render_dashboard(df)
    elif section == "Orders/Load Management":
        render_orders_management(df)
    elif section == "Active Status":
        render_active_status_view(
            df,
            refresh_callback=refresh_data,
            port_houston_panel_renderer=_render_load_port_houston_panel,
        )
    elif section == "Dispatch Board":
        render_dispatch_board_focused(
            df,
            refresh_callback=refresh_data,
            port_houston_panel_renderer=_render_load_port_houston_panel,
        )
    elif section == "Calendar View":
        render_calendar_view(df)
    elif section == "Documents":
        render_documents(df)
    elif section == "Email Imports":
        render_email_imports()
    elif section == "Billing / ProfitTools":
        render_billing(df)
    elif section == "Validation":
        render_validation(df)
    elif section == "Master Data":
        render_master_data_admin()
    else:
        st.warning(f"Unknown section: {section}")


def route_selected_page() -> None:
    section = render_sidebar(
        refresh_callback=refresh_data,
        status_legend_renderer=render_status_legend,
    )

    df = _load_current_tms_data_or_stop() if section in LOAD_DATA_SECTIONS else pd.DataFrame()
    _render_selected_page(section, df)
