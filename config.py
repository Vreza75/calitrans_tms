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
    "type",
    "booking_number",
    "load_id",
    "reference_number",
    "customer",
    "container_number",
    "port",
    "warehouse",
    "address",
    "delivery_need_date",
    "lfd",
    "status",
    "driver_name",
    "truck_assigned",
    "chassis",
    "dispatcher_notes",
    "billing_notes",
    "ready_for_profittools",
    "rate",
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "chassis_provider",
    "pickup_reference",
    "delivery_reference",
    "invoice_status",
    "driver_pay_status",
    "customer_rate",
    "carrier_pay",
    "accessorials",
    "margin",
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
