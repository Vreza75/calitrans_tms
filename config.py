from __future__ import annotations

import os
from pathlib import Path
try:
    import tomllib
except Exception:
    tomllib = None

try:
    import streamlit as st
except Exception:
    st = None

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False


APP_DIR = Path(__file__).resolve().parent

load_dotenv()


def _unquote_config_value(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _read_key_value_file(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() != name:
            continue

        value = _unquote_config_value(value)
        return value if value else None

    return None


def _read_local_streamlit_secret(name: str) -> str | None:
    path = APP_DIR / ".streamlit" / "secrets.toml"
    if tomllib is not None and path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            value = data.get(name)
            if value not in [None, ""]:
                return str(value)
        except Exception:
            pass

    return _read_key_value_file(path, name)


def _read_local_env_secret(name: str) -> str | None:
    return _read_key_value_file(APP_DIR / ".env", name)


def _load_local_env_file() -> None:
    """Small fallback for local runs where python-dotenv is unavailable."""
    env_path = APP_DIR / ".env"
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
        value = _unquote_config_value(value)
        if not key:
            continue

        if value and os.getenv(key) in [None, ""]:
            os.environ[key] = value


_load_local_env_file()


def _first_secret(names: list[str], default: str | None = None) -> str | None:
    for name in names:
        value = get_secret(name)
        if value not in [None, ""]:
            return value
    return default


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read config from Streamlit secrets, environment, and local fallback files."""
    value = get_streamlit_secret(name)
    if value not in [None, ""]:
        return str(value).strip()

    env_value = os.getenv(name)
    if env_value not in [None, ""]:
        return str(env_value).strip()

    local_env_value = _read_local_env_secret(name)
    if local_env_value not in [None, ""]:
        return local_env_value

    local_streamlit_value = _read_local_streamlit_secret(name)
    if local_streamlit_value not in [None, ""]:
        return local_streamlit_value

    return default


def get_streamlit_secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return value


def get_config_source(name: str) -> str:
    value = get_streamlit_secret(name)
    if value not in [None, ""]:
        return "streamlit secrets"

    env_value = os.getenv(name)
    if env_value not in [None, ""]:
        return "environment/.env"

    local_env_value = _read_local_env_secret(name)
    if local_env_value not in [None, ""]:
        return "local .env file"

    local_streamlit_value = _read_local_streamlit_secret(name)
    if local_streamlit_value not in [None, ""]:
        return "local .streamlit/secrets.toml"

    return "missing"


def get_int_secret(name: str, default: int) -> int:
    value = get_secret(name, str(default))
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


DISPATCH_YAHOO_EMAIL = _first_secret(["DISPATCH_YAHOO_EMAIL", "YAHOO_EMAIL_DISPATCH", "YAHOO_EMAIL"])
DISPATCH_YAHOO_APP_PASSWORD = _first_secret(
    ["DISPATCH_YAHOO_APP_PASSWORD", "YAHOO_APP_PASSWORD_DISPATCH", "YAHOO_APP_PASSWORD"]
)
MARGIE_YAHOO_EMAIL = _first_secret(["MARGIE_YAHOO_EMAIL", "YAHOO_EMAIL_MARGIE", "YAHOO_EMAIL_MARGIEA"])
MARGIE_YAHOO_APP_PASSWORD = _first_secret(
    ["MARGIE_YAHOO_APP_PASSWORD", "YAHOO_APP_PASSWORD_MARGIE", "YAHOO_APP_PASSWORD_MARGIEA"]
)
ACCOUNTING_YAHOO_EMAIL = _first_secret(["ACCOUNTING_YAHOO_EMAIL", "YAHOO_EMAIL_ACCOUNTING"])
ACCOUNTING_YAHOO_APP_PASSWORD = _first_secret(
    ["ACCOUNTING_YAHOO_APP_PASSWORD", "YAHOO_APP_PASSWORD_ACCOUNTING"]
)

YAHOO_EMAIL = _first_secret(["YAHOO_EMAIL", "DISPATCH_YAHOO_EMAIL"])
YAHOO_APP_PASSWORD = _first_secret(["YAHOO_APP_PASSWORD", "DISPATCH_YAHOO_APP_PASSWORD"])
IMAP_SERVER = get_secret("IMAP_SERVER", "imap.mail.yahoo.com")
IMAP_PORT = get_int_secret("IMAP_PORT", 993)
SMTP_HOST = get_secret("SMTP_HOST", "smtp.mail.yahoo.com")
SMTP_PORT = get_int_secret("SMTP_PORT", 465)
SMTP_USER = _first_secret(["SMTP_USER", "DISPATCH_YAHOO_EMAIL"], YAHOO_EMAIL)
SMTP_PASSWORD = _first_secret(["SMTP_PASSWORD", "DISPATCH_YAHOO_APP_PASSWORD"], YAHOO_APP_PASSWORD)
DISPATCH_EMAIL = _first_secret(["DISPATCH_EMAIL", "DISPATCH_YAHOO_EMAIL"], YAHOO_EMAIL)
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
