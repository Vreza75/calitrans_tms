from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

try:
    import streamlit as st
except Exception:
    st = None


load_dotenv()


DEFAULT_AUTH_URL = "https://auth-v1.america.naviscloudops.com/auth/realms/phaprod/protocol/openid-connect/token"
DEFAULT_BASE_URL = "https://api.america.naviscloudops.com/v3/evp"
DEFAULT_OPERATOR = "POHA"

UNIT_FIELDS = ",".join(
    [
        "unitId",
        "category",
        "freightKind",
        "transitState",
        "visitState",
        "eqtypeId",
        "line",
        "scope.yard_id",
        "scope.facility_id",
        "lastKnownPosition.posName",
        "routing.polId",
        "routing.pod1Id",
        "routing.carrierServiceId",
        "routing.returnToLocation",
    ]
)

BOOKING_FIELDS = ",".join(
    [
        "nbr",
        "subType",
        "lineId",
        "lineScac",
        "clientRefNo",
        "visit.visitId",
        "pod1Id",
        "polId",
        "origin",
        "destination",
        "eqStatus",
        "estimatedDate",
        "earliestDate",
        "latestDate",
        "quantity",
        "tally",
        "items",
    ]
)

VESSEL_FIELDS = ",".join(
    [
        "visitId",
        "vesName",
        "visitPhase",
        "lineId",
        "ibVyg",
        "obVyg",
        "eta",
        "etd",
        "ata",
        "atd",
        "beginReceive",
        "cargoCutoff",
        "emptyPickup",
        "timeFirstAvailability",
        "scope.yard_id",
        "scope.facility_id",
    ]
)


_TOKEN_CACHE: dict[str, Any] = {}


class PortHoustonError(RuntimeError):
    pass


@dataclass
class PortHoustonSettings:
    base_url: str
    auth_url: str
    client_id: str
    client_secret: str
    operator: str
    timeout_seconds: int = 30

    @property
    def missing(self) -> list[str]:
        missing = []
        if not self.client_id:
            missing.append("PORT_HOUSTON_CLIENT_ID")
        if not self.client_secret:
            missing.append("PORT_HOUSTON_CLIENT_SECRET")
        return missing

    @property
    def is_configured(self) -> bool:
        return not self.missing


def get_setting(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value not in [None, ""]:
        return str(value)
    if st is not None:
        try:
            secret_value = st.secrets.get(name)
            if secret_value not in [None, ""]:
                return str(secret_value)
        except Exception:
            pass
    return default


def get_port_houston_settings() -> PortHoustonSettings:
    return PortHoustonSettings(
        base_url=get_setting("PORT_HOUSTON_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        auth_url=get_setting("PORT_HOUSTON_AUTH_URL", DEFAULT_AUTH_URL) or DEFAULT_AUTH_URL,
        client_id=get_setting("PORT_HOUSTON_CLIENT_ID", "") or "",
        client_secret=get_setting("PORT_HOUSTON_CLIENT_SECRET", "") or "",
        operator=get_setting("PORT_HOUSTON_OPERATOR", DEFAULT_OPERATOR) or DEFAULT_OPERATOR,
        timeout_seconds=int(get_setting("PORT_HOUSTON_TIMEOUT_SECONDS", "30") or 30),
    )


def _safe_error_message(response: requests.Response) -> str:
    text = response.text or ""
    for marker in ["access_token", "client_secret", "Authorization", "password"]:
        if marker.lower() in text.lower():
            text = "Response contained sensitive authentication details and was redacted."
            break
    return f"Port Houston API returned {response.status_code}: {text[:500]}"


def content_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, list):
            return [item for item in content if isinstance(item, dict)]
        if isinstance(content, dict):
            return [content]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def flatten_record(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in (record or {}).items():
        label = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_record(value, label))
        elif isinstance(value, list):
            flat[label] = f"{len(value)} item(s)"
        else:
            flat[label] = value
    return flat


def get_nested(record: dict[str, Any], path: str, default: str = "") -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return default
        value = value.get(part)
    return default if value in [None, ""] else value


def summarize_unit(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "Container": record.get("unitId", ""),
        "Category": record.get("category", ""),
        "Freight": record.get("freightKind", ""),
        "Transit": record.get("transitState", ""),
        "Visit": record.get("visitState", ""),
        "Size": record.get("eqtypeId", ""),
        "Line": record.get("line", ""),
        "Facility": get_nested(record, "scope.facility_id"),
        "Yard": get_nested(record, "scope.yard_id"),
        "Position": get_nested(record, "lastKnownPosition.posName"),
        "POL": get_nested(record, "routing.polId"),
        "POD": get_nested(record, "routing.pod1Id"),
        "Service": get_nested(record, "routing.carrierServiceId"),
        "Return Location": get_nested(record, "routing.returnToLocation"),
    }


class PortHoustonClient:
    def __init__(self, settings: PortHoustonSettings | None = None) -> None:
        self.settings = settings or get_port_houston_settings()
        if not self.settings.is_configured:
            missing = ", ".join(self.settings.missing)
            raise PortHoustonError(f"Port Houston credentials are missing: {missing}")

    def get_token(self, force_refresh: bool = False) -> str:
        cache_key = f"{self.settings.auth_url}|{self.settings.client_id}"
        cached = _TOKEN_CACHE.get(cache_key)
        if (
            not force_refresh
            and cached
            and cached.get("token")
            and float(cached.get("expires_at", 0)) > time.time() + 90
        ):
            return str(cached["token"])

        response = requests.post(
            self.settings.auth_url,
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.settings.timeout_seconds,
        )
        if not response.ok:
            raise PortHoustonError(_safe_error_message(response))

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise PortHoustonError("Port Houston token response did not include an access token.")

        expires_in = int(payload.get("expires_in", 3600) or 3600)
        _TOKEN_CACHE[cache_key] = {
            "token": token,
            "expires_at": time.time() + expires_in,
        }
        return str(token)

    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        endpoint = "/" + endpoint.strip("/") if endpoint else ""
        params = dict(params or {})
        if self.settings.operator and "operator" not in params:
            params["operator"] = self.settings.operator

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.get_token()}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        response = requests.request(
            method.upper(),
            f"{self.settings.base_url.rstrip('/')}{endpoint}",
            params=params,
            json=payload,
            headers=headers,
            timeout=self.settings.timeout_seconds,
        )
        if not response.ok:
            raise PortHoustonError(_safe_error_message(response))
        if not response.text:
            return {}
        try:
            return response.json()
        except Exception:
            return {"text": response.text}

    def get_inventory_units(self, *, container: str = "", predicate: str = "", fields: str = UNIT_FIELDS) -> Any:
        active_predicate = predicate.strip()
        if container.strip():
            active_predicate = f"unitId={container.strip().upper()}"
        params = {"fields": fields}
        if active_predicate:
            params["predicate"] = active_predicate
        return self.request("/inventory/units", params=params)

    def get_bookings(self, *, booking: str = "", predicate: str = "", fields: str = BOOKING_FIELDS) -> Any:
        active_predicate = predicate.strip()
        if booking.strip():
            active_predicate = f"nbr={booking.strip().upper()}"
        params = {"fields": fields}
        if active_predicate:
            params["predicate"] = active_predicate
        return self.request("/orders/bookings", params=params)

    def get_vessel_visits(self, *, visit_id: str = "", predicate: str = "", fields: str = VESSEL_FIELDS) -> Any:
        active_predicate = predicate.strip()
        if visit_id.strip():
            active_predicate = f"visitId={visit_id.strip()}"
        params = {"fields": fields}
        if active_predicate:
            params["predicate"] = active_predicate
        return self.request("/vessel/vesselvisits", params=params)

    def get_gate_appointments(self, *, predicate: str = "") -> Any:
        params: dict[str, Any] = {}
        if predicate.strip():
            params["predicate"] = predicate.strip()
        return self.request("/road/gateappointments", params=params)

    def get_appointment_time_slots(self, *, predicate: str = "") -> Any:
        params: dict[str, Any] = {}
        if predicate.strip():
            params["predicate"] = predicate.strip()
        return self.request("/road/appointmenttimeslots", params=params)

    def get_subscribers(self) -> Any:
        return self.request("/notify/subscribers")

    def create_subscriber(self, payload: dict[str, Any]) -> Any:
        return self.request("/notify/subscribers", method="POST", payload=payload)
