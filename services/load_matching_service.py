from __future__ import annotations

import re
from typing import Any

import pandas as pd

from db_client import read_df


AI_LOAD_CONTEXT_COLUMNS = [
    "id",
    "load_id",
    "type",
    "booking_number",
    "reference_number",
    "container_number",
    "customer",
    "port",
    "warehouse",
    "address",
    "delivery_need_date",
    "lfd",
    "status",
    "driver_name",
    "truck_assigned",
    "chassis",
    "size",
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
]

AI_LOAD_CONTEXT_LABELS = {
    "id": "Load ID",
    "load_id": "External Load ID",
    "type": "Move Type",
    "booking_number": "Booking Number",
    "reference_number": "Reference Number",
    "container_number": "Container Number",
    "customer": "Customer",
    "port": "Pickup / Port",
    "warehouse": "Delivery / Warehouse",
    "address": "Delivery Address",
    "delivery_need_date": "Delivery Need Date",
    "lfd": "LFD",
    "status": "Status",
    "driver_name": "Driver",
    "truck_assigned": "Truck",
    "chassis": "Chassis",
    "size": "Container Size",
    "steamship_line": "Steamship Line",
    "vessel_name": "Vessel",
    "terminal": "Terminal",
    "pickup_appointment": "Pickup Appointment",
    "delivery_appointment": "Delivery Appointment",
    "empty_return_location": "Empty Return Location",
    "empty_return_date": "Empty Return Date",
    "current_location": "Current Location",
    "eta": "ETA",
    "live_load_status": "Live Load Status",
    "live_unload_status": "Live Unload Status",
    "last_driver_update": "Last Driver Update",
}


def safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value_text = safe_str(value)
    if not value_text:
        return None

    try:
        return int(float(value_text))
    except Exception:
        return None


def existing_load_columns() -> set[str]:
    try:
        df = read_df(
            """
            select column_name
            from information_schema.columns
            where table_name = 'loads'
            """
        )

        if df.empty or "column_name" not in df.columns:
            return _fallback_load_columns()

        return set(df["column_name"].astype(str).tolist())

    except Exception:
        return _fallback_load_columns()


def _fallback_load_columns() -> set[str]:
    return {
        "id",
        "load_id",
        "type",
        "booking_number",
        "reference_number",
        "container_number",
        "customer",
        "port",
        "warehouse",
        "address",
        "delivery_need_date",
        "lfd",
        "status",
        "driver_name",
        "truck_assigned",
        "chassis",
        "size",
        "updated_at",
    }


def _extract_load_id_hint(text: str) -> str:
    match = re.search(
        r"\b(?:load|order)\s*(?:id|#|number)?\s*[:#-]?\s*(\d{2,})\b",
        str(text or ""),
        re.I,
    )
    return match.group(1) if match else ""


def _extract_date_hint(text: str) -> str:
    match = re.search(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", str(text or ""))
    if not match:
        return ""

    parsed = pd.to_datetime(match.group(1), errors="coerce")
    if pd.isna(parsed):
        return ""

    return parsed.strftime("%Y-%m-%d")


def _row_match_text(row: dict, column: str) -> str:
    return safe_str(row.get(column, "")).upper()


def _score_load_match_row(row: dict, search: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    checks = [
        ("booking", "booking_number", 100, "Booking"),
        ("container", "container_number", 98, "Container"),
        ("reference", "reference_number", 90, "Reference"),
        ("load_id", "id", 95, "Load ID"),
        ("load_id", "load_id", 90, "External Load ID"),
        ("vessel", "vessel_name", 65, "Vessel"),
    ]

    for search_key, column, points, label in checks:
        needle = safe_str(search.get(search_key, "")).upper()
        haystack = _row_match_text(row, column)

        if needle and haystack and (needle == haystack or needle in haystack):
            score = max(score, points)
            reasons.append(label)

    customer = safe_str(search.get("customer", "")).lower()
    row_customer = safe_str(row.get("customer", "")).lower()

    date_hint = safe_str(search.get("date", ""))
    row_date = safe_str(row.get("delivery_need_date", ""))
    date_matches = bool(date_hint and date_hint in row_date)

    if customer and len(customer) >= 4 and customer in row_customer:
        score = max(score, 55)
        reasons.append("Customer")

        if date_matches:
            score = max(score, 82)
            reasons.append("Date")

    elif date_matches:
        score = max(score, 45)
        reasons.append("Date")

    return score, reasons


def find_load_match_candidates(
    tokens: dict,
    parsed: dict | None = None,
    subject: str = "",
    body: str = "",
    limit: int = 5,
) -> list[dict]:
    parsed = parsed or {}
    text = f"{subject or ''} {body or ''} {parsed}"

    columns = existing_load_columns()

    select_columns = [
        column
        for column in [
            "id",
            "load_id",
            "booking_number",
            "reference_number",
            "container_number",
            "customer",
            "delivery_need_date",
            "status",
            "driver_name",
            "pickup_appointment",
            "delivery_appointment",
            "vessel_name",
            "updated_at",
        ]
        if column in columns
    ]

    if "id" not in select_columns:
        return []

    search = {
        "booking": safe_str(tokens.get("booking_number") or parsed.get("Booking Number", "")),
        "container": safe_str(tokens.get("container_number") or parsed.get("Container Number", "")),
        "reference": safe_str(tokens.get("reference_number") or parsed.get("Reference Number", "")),
        "load_id": _extract_load_id_hint(text),
        "customer": safe_str(parsed.get("Customer", "")),
        "date": _extract_date_hint(text),
        "vessel": safe_str(parsed.get("Vessel", "") or parsed.get("Vessel Name", "")),
    }

    conditions: list[str] = []
    params: dict[str, Any] = {"limit": max(int(limit) * 4, 20)}

    if search["booking"] and "booking_number" in columns:
        conditions.append("lower(coalesce(booking_number, '')) like lower(:booking_like)")
        params["booking_like"] = f"%{search['booking']}%"

    if search["container"] and "container_number" in columns:
        conditions.append("lower(coalesce(container_number, '')) like lower(:container_like)")
        params["container_like"] = f"%{search['container']}%"

    if search["reference"] and "reference_number" in columns:
        conditions.append("lower(coalesce(reference_number, '')) like lower(:reference_like)")
        params["reference_like"] = f"%{search['reference']}%"

    if search["load_id"]:
        conditions.append("cast(id as text) = :load_id")
        params["load_id"] = search["load_id"]

        if "load_id" in columns:
            conditions.append("lower(coalesce(load_id, '')) = lower(:external_load_id)")
            params["external_load_id"] = search["load_id"]

    if search["customer"] and len(search["customer"]) >= 4 and "customer" in columns:
        conditions.append("lower(coalesce(customer, '')) like lower(:customer_like)")
        params["customer_like"] = f"%{search['customer']}%"

    if search["vessel"] and "vessel_name" in columns:
        conditions.append("lower(coalesce(vessel_name, '')) like lower(:vessel_like)")
        params["vessel_like"] = f"%{search['vessel']}%"

    if not conditions:
        return []

    order_clause = "updated_at desc nulls last, id desc" if "updated_at" in columns else "id desc"

    try:
        match_df = read_df(
            f"""
            select {", ".join(select_columns)}
            from loads
            where {" or ".join(conditions)}
            order by {order_clause}
            limit :limit
            """,
            params,
        )
    except Exception:
        return []

    candidates: list[dict] = []

    for _, row in match_df.iterrows():
        row_dict = row.to_dict()
        score, reasons = _score_load_match_row(row_dict, search)

        if score <= 0:
            continue

        candidates.append(
            {
                "Load ID": int(row_dict["id"]),
                "External Load ID": safe_str(row_dict.get("load_id", "")),
                "Booking Number": safe_str(row_dict.get("booking_number", "")),
                "Container Number": safe_str(row_dict.get("container_number", "")),
                "Reference Number": safe_str(row_dict.get("reference_number", "")),
                "Customer": safe_str(row_dict.get("customer", "")),
                "Status": safe_str(row_dict.get("status", "")),
                "Driver": safe_str(row_dict.get("driver_name", "")),
                "Pickup Appointment": safe_str(row_dict.get("pickup_appointment", "")),
                "Delivery Appointment": safe_str(row_dict.get("delivery_appointment", "")),
                "Vessel": safe_str(row_dict.get("vessel_name", "")),
                "Match Score": int(score),
                "Match Reason": ", ".join(reasons),
            }
        )

    candidates = sorted(candidates, key=lambda item: item["Match Score"], reverse=True)
    return candidates[: int(limit)]


def find_matching_load(
    tokens: dict,
    parsed: dict | None = None,
    subject: str = "",
    body: str = "",
) -> tuple[int | None, int]:
    candidates = find_load_match_candidates(
        tokens,
        parsed=parsed,
        subject=subject,
        body=body,
        limit=5,
    )

    if not candidates:
        return None, 0

    top = candidates[0]
    top_score = int(top.get("Match Score", 0) or 0)
    second_score = int(candidates[1].get("Match Score", 0) or 0) if len(candidates) > 1 else 0

    if top_score >= 90 and top_score - second_score >= 5:
        return int(top["Load ID"]), top_score

    if top_score >= 98:
        return int(top["Load ID"]), top_score

    return None, top_score


def _clean_ai_context_value(value: Any) -> str:
    value_str = safe_str(value)
    if value_str.lower() in {"nan", "nat"}:
        return ""
    return value_str


def _load_context_select_columns() -> list[str]:
    columns = existing_load_columns()
    return [column for column in AI_LOAD_CONTEXT_COLUMNS if column in columns]


def _load_row_to_ai_context(row: dict) -> dict:
    context = {}

    for key, label in AI_LOAD_CONTEXT_LABELS.items():
        if key in row:
            value = _clean_ai_context_value(row.get(key))
            if value:
                context[label] = value

    return context


def _load_document_context(load_id) -> dict:
    load_id = int_or_none(load_id)
    if load_id is None:
        return {}

    try:
        docs_df = read_df(
            """
            select document_type, filename, created_at
            from documents
            where load_id = :load_id
            order by created_at desc
            limit 12
            """,
            {"load_id": load_id},
        )
    except Exception:
        return {}

    if docs_df.empty:
        return {
            "Document Count": "0",
            "POD Available": "No document found",
        }

    doc_types = [
        _clean_ai_context_value(value)
        for value in docs_df.get("document_type", pd.Series(dtype=str)).tolist()
    ]

    doc_names = [
        _clean_ai_context_value(value)
        for value in docs_df.get("filename", pd.Series(dtype=str)).tolist()
    ]

    haystack = " ".join(doc_types + doc_names).lower()

    pod_available = (
        "Yes"
        if ("pod" in haystack or "proof" in haystack or "delivery" in haystack)
        else "No document found"
    )

    return {
        "Document Count": str(len(docs_df)),
        "Document Types": ", ".join([value for value in doc_types if value][:6]),
        "POD Available": pod_available,
    }


def fetch_ai_load_context(load_id) -> dict:
    load_id = int_or_none(load_id)
    if load_id is None:
        return {}

    try:
        columns = _load_context_select_columns()

        if not columns:
            return {}

        load_df = read_df(
            f"""
            select {", ".join(columns)}
            from loads
            where id = :load_id
            limit 1
            """,
            {"load_id": load_id},
        )
    except Exception:
        return {}

    if load_df.empty:
        return {}

    context = _load_row_to_ai_context(load_df.iloc[0].to_dict())
    context.update(_load_document_context(load_id))

    return context


def _candidate_summary_from_context(context: dict) -> dict:
    keep = [
        "Load ID",
        "External Load ID",
        "Booking Number",
        "Reference Number",
        "Container Number",
        "Customer",
        "Status",
        "Pickup / Port",
        "Delivery / Warehouse",
        "Delivery Need Date",
        "LFD",
        "ETA",
        "Current Location",
        "Pickup Appointment",
        "Delivery Appointment",
        "POD Available",
    ]

    return {key: context.get(key, "") for key in keep if context.get(key)}


def find_ai_load_candidates(
    tokens: dict,
    parsed: dict,
    matched_load_id=None,
    limit: int = 5,
) -> list[dict]:
    candidate_ids: list[int] = []

    matched_load_id = int_or_none(matched_load_id)
    if matched_load_id is not None:
        candidate_ids.append(matched_load_id)

    columns = existing_load_columns()

    conditions: list[str] = []
    params: dict[str, Any] = {"limit": int(limit)}

    booking = safe_str(tokens.get("booking_number") or parsed.get("Booking Number", ""))
    container = safe_str(tokens.get("container_number") or parsed.get("Container Number", ""))
    reference = safe_str(tokens.get("reference_number") or parsed.get("Reference Number", ""))
    customer = safe_str(parsed.get("Customer", ""))

    if booking and "booking_number" in columns:
        conditions.append("lower(coalesce(booking_number, '')) like lower(:booking_like)")
        params["booking_like"] = f"%{booking}%"

    if container and "container_number" in columns:
        conditions.append("lower(coalesce(container_number, '')) like lower(:container_like)")
        params["container_like"] = f"%{container}%"

    if reference and "reference_number" in columns:
        conditions.append("lower(coalesce(reference_number, '')) like lower(:reference_like)")
        params["reference_like"] = f"%{reference}%"

    if customer and len(customer) >= 4 and "customer" in columns:
        conditions.append("lower(coalesce(customer, '')) like lower(:customer_like)")
        params["customer_like"] = f"%{customer}%"

    if conditions:
        order_clause = "updated_at desc nulls last" if "updated_at" in columns else "id desc"

        try:
            ids_df = read_df(
                f"""
                select id
                from loads
                where {" or ".join(conditions)}
                order by {order_clause}
                limit :limit
                """,
                params,
            )

            for value in ids_df.get("id", pd.Series(dtype=int)).tolist():
                value_id = int_or_none(value)
                if value_id is not None:
                    candidate_ids.append(value_id)

        except Exception:
            pass

    candidates: list[dict] = []
    seen: set[int] = set()

    for load_id in candidate_ids:
        if load_id in seen:
            continue

        seen.add(load_id)

        context = fetch_ai_load_context(load_id)

        if context:
            candidates.append(_candidate_summary_from_context(context))

        if len(candidates) >= limit:
            break

    return candidates


def build_ai_load_context(classification: dict, parsed: dict) -> tuple[dict, list[dict]]:
    matched_load_id = classification.get("matched_load_id")
    tokens = classification.get("tokens") or {}

    load_context = fetch_ai_load_context(matched_load_id) if matched_load_id is not None else {}

    load_candidates = find_ai_load_candidates(
        tokens,
        parsed,
        matched_load_id=matched_load_id,
    )

    return load_context, load_candidates


def valid_ai_suggested_load_id(ai_suggestion: dict, load_candidates: list[dict]) -> int | None:
    if not ai_suggestion or not ai_suggestion.get("success"):
        return None

    suggested = safe_str(ai_suggestion.get("suggested_load_id", ""))

    if not suggested:
        return None

    valid_ids = {
        safe_str(candidate.get("Load ID", ""))
        for candidate in load_candidates
        if safe_str(candidate.get("Load ID", ""))
    }

    if suggested not in valid_ids:
        return None

    try:
        return int(suggested)
    except Exception:
        return None