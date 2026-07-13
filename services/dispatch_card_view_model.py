from __future__ import annotations

from urllib.parse import quote

import pandas as pd


def _normalize_booking_number(value) -> str:
    return str(value or "").strip().upper()


def _safe_str(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _compute_totals(totals_df: pd.DataFrame) -> dict[tuple[str, str, str], int]:
    """(normalized_booking, customer, move_type) -> total container count
    across every status in the unfiltered dataset. Rows with no booking
    number are excluded — they're never grouped, so they have no
    meaningful "total" beyond themselves."""
    if totals_df.empty:
        return {}
    work = totals_df.copy()
    work["_booking_key"] = work.get("Booking Number", "").apply(_normalize_booking_number)
    work = work[work["_booking_key"] != ""]
    if work.empty:
        return {}
    counts: dict[tuple[str, str, str], int] = {}
    for key, group in work.groupby(["_booking_key", "Customer", "Dispatch Move Type"]):
        counts[key] = len(group)
    return counts


def _card_from_group(booking_key: str, customer: str, move_type: str, status: str, group: pd.DataFrame, total: int) -> dict:
    row_ids = [int(v) for v in group["_row_id"].tolist()]
    display_booking = _safe_str(group.iloc[0].get("Booking Number", "")) or "Booking Pending"
    need_dates = pd.to_datetime(group.get("Delivery Need Date", ""), errors="coerce").dropna()
    lfds = pd.to_datetime(group.get("LFD", ""), errors="coerce").dropna()
    drivers = group["Driver Name"].astype(str).str.strip()
    unassigned_mask = drivers.isin(["", "None", "nan", "Unassigned"])
    exception_count = group["Exceptions"].apply(
        lambda value: len([item for item in _safe_str(value).split(",") if item.strip()])
    ).sum()

    return {
        "group_id": f"{booking_key}|{customer}|{move_type}|{status}",
        "booking_number": display_booking,
        "customer": customer,
        "move_type": move_type,
        "canonical_status": status,
        "row_ids": row_ids,
        "visible_container_count": len(group),
        "total_container_count": total,
        "earliest_need_date": need_dates.min().strftime("%Y-%m-%d") if not need_dates.empty else "",
        "earliest_lfd": lfds.min().strftime("%Y-%m-%d") if not lfds.empty else "",
        "unassigned_count": int(unassigned_mask.sum()),
        "driver_count": int(drivers[~unassigned_mask].nunique()),
        "exception_count": int(exception_count),
        "workspace_url": f"?booking={quote(display_booking)}",
    }


def build_booking_card_view_models(scoped_df: pd.DataFrame, totals_df: pd.DataFrame) -> list[dict]:
    """Group scoped_df's rows into booking-level card view models.

    scoped_df is the already-filtered set of loads to actually display.
    totals_df is the full unfiltered active-dispatch dataset, used only to
    compute "X of Y total containers" so applying a filter never changes
    what "Y" means for a booking that still has containers outside the
    current filter.
    """
    if scoped_df.empty:
        return []

    work = scoped_df.copy()
    work["_booking_key"] = work.get("Booking Number", "").apply(_normalize_booking_number)
    totals = _compute_totals(totals_df)

    cards: list[dict] = []

    with_booking = work[work["_booking_key"] != ""]
    without_booking = work[work["_booking_key"] == ""]

    for (booking_key, customer, move_type, status), group in with_booking.groupby(
        ["_booking_key", "Customer", "Dispatch Move Type", "Status"]
    ):
        total = totals.get((booking_key, customer, move_type), len(group))
        cards.append(_card_from_group(booking_key, customer, move_type, status, group, total))

    # Rows with no booking number are never grouped together, even with
    # each other — one card per row, falling back to Load ID.
    for _, row in without_booking.iterrows():
        single = pd.DataFrame([row])
        fallback_label = _safe_str(row.get("Load ID", "")) or f"Row {int(row.get('_row_id', 0))}"
        card = _card_from_group("", row.get("Customer", ""), row.get("Dispatch Move Type", ""), row.get("Status", ""), single, 1)
        card["booking_number"] = f"Order Reference: {fallback_label}" if fallback_label else "No Booking Number"
        card["group_id"] = f"no-booking|{int(row.get('_row_id', 0))}"
        cards.append(card)

    return cards
