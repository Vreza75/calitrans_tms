# repositories/load_repo.py

from __future__ import annotations

from typing import Any

import pandas as pd

from db_client import execute, read_df


LOAD_MATCH_COLUMNS = [
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
    "updated_at",
]


LOAD_UPDATE_FIELD_MAP = {
    "TYPE": "type",
    "Booking Number": "booking_number",
    "Reference Number": "reference_number",
    "Container Number": "container_number",
    "Customer": "customer",
    "Port": "port",
    "Warehouse": "warehouse",
    "Address": "address",
    "Delivery Need Date": "delivery_need_date",
    "Document Cutoff": "document_cutoff",
    "LFD": "lfd",
    "Size": "size",
    "Dispatcher Notes": "dispatcher_notes",
}


def safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def int_or_none(value):
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
        return set(df["column_name"].astype(str).tolist())
    except Exception:
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


def load_select_columns() -> list[str]:
    existing = existing_load_columns()
    return [column for column in LOAD_MATCH_COLUMNS if column in existing]


def load_by_id(load_id) -> dict:
    load_id = int_or_none(load_id)
    if load_id is None:
        return {}

    columns = load_select_columns()
    if not columns:
        return {}

    try:
        df = read_df(
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

    return df.iloc[0].to_dict() if not df.empty else {}


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
    *,
    booking: str = "",
    container: str = "",
    reference: str = "",
    customer: str = "",
    date_hint: str = "",
    vessel: str = "",
    load_id: str = "",
    limit: int = 5,
) -> list[dict]:
    existing_columns = existing_load_columns()
    select_columns = [column for column in load_select_columns() if column in existing_columns]

    if "id" not in select_columns:
        return []

    search = {
        "booking": safe_str(booking),
        "container": safe_str(container),
        "reference": safe_str(reference),
        "customer": safe_str(customer),
        "date": safe_str(date_hint),
        "vessel": safe_str(vessel),
        "load_id": safe_str(load_id),
    }

    conditions: list[str] = []
    params: dict[str, Any] = {"limit": max(int(limit) * 4, 20)}

    if search["booking"] and "booking_number" in existing_columns:
        conditions.append("lower(coalesce(booking_number, '')) like lower(:booking_like)")
        params["booking_like"] = f"%{search['booking']}%"

    if search["container"] and "container_number" in existing_columns:
        conditions.append("lower(coalesce(container_number, '')) like lower(:container_like)")
        params["container_like"] = f"%{search['container']}%"

    if search["reference"] and "reference_number" in existing_columns:
        conditions.append("lower(coalesce(reference_number, '')) like lower(:reference_like)")
        params["reference_like"] = f"%{search['reference']}%"

    if search["load_id"]:
        conditions.append("cast(id as text) = :load_id")
        params["load_id"] = search["load_id"]
        if "load_id" in existing_columns:
            conditions.append("lower(coalesce(load_id, '')) = lower(:external_load_id)")
            params["external_load_id"] = search["load_id"]

    if search["customer"] and len(search["customer"]) >= 4 and "customer" in existing_columns:
        conditions.append("lower(coalesce(customer, '')) like lower(:customer_like)")
        params["customer_like"] = f"%{search['customer']}%"

    if search["vessel"] and "vessel_name" in existing_columns:
        conditions.append("lower(coalesce(vessel_name, '')) like lower(:vessel_like)")
        params["vessel_like"] = f"%{search['vessel']}%"

    if not conditions:
        return []

    order_clause = "updated_at desc nulls last, id desc" if "updated_at" in existing_columns else "id desc"

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


def best_load_match(**kwargs) -> tuple[int | None, int]:
    candidates = find_load_match_candidates(**kwargs)
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


def save_load_communication(
    *,
    load_id,
    intake_id=None,
    case_id=None,
    conversation_key: str = "",
    communication_type: str = "Customer Request",
    subject: str = "",
    sender: str = "",
    body: str = "",
    direction: str = "inbound",
) -> None:
    execute(
        """
        insert into load_communications (
            load_id,
            intake_id,
            case_id,
            conversation_key,
            communication_type,
            direction,
            subject,
            sender,
            message_body
        )
        values (
            :load_id,
            :intake_id,
            :case_id,
            :conversation_key,
            :communication_type,
            :direction,
            :subject,
            :sender,
            :message_body
        )
        """,
        {
            "load_id": int_or_none(load_id),
            "intake_id": int_or_none(intake_id),
            "case_id": int_or_none(case_id),
            "conversation_key": conversation_key or None,
            "communication_type": communication_type,
            "direction": direction,
            "subject": subject,
            "sender": sender,
            "message_body": body,
        },
    )


def update_load_from_parsed(
    *,
    load_id,
    parsed: dict,
    fill_blank_only: bool = True,
) -> dict:
    load_id = int_or_none(load_id)
    if load_id is None:
        return {}

    existing = load_by_id(load_id)
    if not existing:
        return {}

    available_columns = existing_load_columns()
    updates: dict[str, Any] = {}

    for source_field, db_column in LOAD_UPDATE_FIELD_MAP.items():
        if db_column not in available_columns:
            continue

        incoming = safe_str((parsed or {}).get(source_field, ""))
        if not incoming:
            continue

        current = safe_str(existing.get(db_column, ""))
        if fill_blank_only and current:
            continue

        updates[db_column] = incoming

    if not updates:
        return {}

    set_clause = ",\n            ".join([f"{column} = :{column}" for column in updates])
    params = {**updates, "load_id": load_id}

    execute(
        f"""
        update loads
        set {set_clause},
            updated_at = now()
        where id = :load_id
        """,
        params,
    )

    return updates


def ai_load_context(load_id) -> dict:
    row = load_by_id(load_id)
    if not row:
        return {}

    labels = {
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

    context = {}
    for key, label in labels.items():
        value = safe_str(row.get(key, ""))
        if value:
            context[label] = value

    return context
