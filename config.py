from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read config from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or os.getenv(name, default)


DATABASE_URL = get_secret("DATABASE_URL")

APP_NAME = "Calitrans Dispatch Center"
DOCUMENT_STORAGE_DIR = get_secret("DOCUMENT_STORAGE_DIR", "storage/load_documents")

EDITABLE_COLUMNS = [
    "TYPE",
    "Booking Number",
    "Load ID",
    "Reference Number",
    "Customer",
    "Container Number",
    "Port",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "LFD",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Dispatcher Notes",
]

ACTIVE_STATUSES = [
    "New",
    "Hold/Need Info",
    "Awaiting Appointment",
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
    "POD Received",
    "Ready for ProfitTools",
    "Exported to ProfitTools",
    "Invoiced",
    "Closed",
    "Cancelled",
]
