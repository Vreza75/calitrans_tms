from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from services.workflow_constants import SERVICE_FLOW_FILTER_OPTIONS, normalize_service_flow


SERVICE_FLOW_COLUMN = "Service Flow"
_SERVICE_FLOW_KEYS = [
    "Service Flow",
    "service_flow",
    "TYPE",
    "type",
    "Load Type",
    "load_type",
    "Move Type",
    "move_type",
    "order_type",
]
_PARSED_DATA_KEYS = ["parsed_data", "Parsed Data", "parsed", "document_parsed"]


def _coerce_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_get(row: Any, key: str, default: Any = "") -> Any:
    if hasattr(row, "get"):
        try:
            return row.get(key, default)
        except Exception:
            return default
    return default


def extract_service_flow_value(row: Any) -> str:
    for key in _SERVICE_FLOW_KEYS:
        normalized = normalize_service_flow(_row_get(row, key), default="")
        if normalized:
            return normalized

    for parsed_key in _PARSED_DATA_KEYS:
        parsed = _coerce_json_dict(_row_get(row, parsed_key))
        if not parsed:
            continue
        for key in _SERVICE_FLOW_KEYS:
            normalized = normalize_service_flow(parsed.get(key), default="")
            if normalized:
                return normalized
    return ""


def ensure_service_flow_column(df: pd.DataFrame, column: str = SERVICE_FLOW_COLUMN) -> pd.DataFrame:
    prepared = df.copy()
    if prepared.empty:
        prepared[column] = pd.Series(dtype="object")
        return prepared

    if column in prepared.columns:
        prepared[column] = prepared[column].apply(lambda value: normalize_service_flow(value, default=""))
        missing_flow = prepared[column].astype(str).str.strip().eq("")
        if missing_flow.any():
            prepared.loc[missing_flow, column] = prepared.loc[missing_flow].apply(extract_service_flow_value, axis=1)
    else:
        prepared[column] = prepared.apply(extract_service_flow_value, axis=1)
    return prepared


def render_service_flow_filter(key: str, label: str = "Service Flow", default: str = "All") -> str:
    selected_default = default if default in SERVICE_FLOW_FILTER_OPTIONS else "All"
    return st.selectbox(
        label,
        SERVICE_FLOW_FILTER_OPTIONS,
        index=SERVICE_FLOW_FILTER_OPTIONS.index(selected_default),
        key=key,
    )


def apply_service_flow_filter(df: pd.DataFrame, selected_flow: str, column: str = SERVICE_FLOW_COLUMN) -> pd.DataFrame:
    prepared = ensure_service_flow_column(df, column=column)
    if selected_flow == "All":
        return prepared

    normalized_flow = normalize_service_flow(selected_flow, default="")
    if not normalized_flow:
        return prepared.iloc[0:0].copy()
    return prepared[prepared[column].eq(normalized_flow)].copy()
