from __future__ import annotations

import os
import streamlit as st

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False


load_dotenv()


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read config from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or os.getenv(name, default)
YAHOO_EMAIL = get_secret("YAHOO_EMAIL")
YAHOO_APP_PASSWORD = get_secret("YAHOO_APP_PASSWORD")
IMAP_SERVER = get_secret("IMAP_SERVER", "imap.mail.yahoo.com")
IMAP_PORT = int(get_secret("IMAP_PORT", "993"))
SMTP_HOST = get_secret("SMTP_HOST", "smtp.mail.yahoo.com")
SMTP_PORT = int(get_secret("SMTP_PORT", "465"))
SMTP_USER = get_secret("SMTP_USER", YAHOO_EMAIL)
SMTP_PASSWORD = get_secret("SMTP_PASSWORD", YAHOO_APP_PASSWORD)
DISPATCH_EMAIL = get_secret("DISPATCH_EMAIL", YAHOO_EMAIL)
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
    "Document Cutoff",
    "Delivery Need Date",
    "LFD",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Size",
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
