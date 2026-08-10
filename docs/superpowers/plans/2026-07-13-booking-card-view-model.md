# Booking Card View Model Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure, tested booking-card grouping/view-model layer for the swimlane board redesign — no UI changes in this plan. This is Phase 2 of the larger booking-rollup swimlane request; the smallest safe patch per the request's own instruction to proceed after review.

**Architecture:** New `services/dispatch_card_view_model.py`, pure function, no Streamlit/DB imports, consuming an already-filtered/scoped dataframe (the same shape `pages_app/dispatch_board.py` already builds) plus an unfiltered reference dataframe for computing accurate "X of Y total containers" figures independent of the active filter set.

**Tech Stack:** Python, pandas.

## Global Constraints

- Group key is `(normalized_booking_number, customer, move_type, status)` — never group across different customers or move types even if booking numbers coincide, and never group across different statuses (a split booking produces one card per status bucket, each showing "N of Total containers").
- Loads with no booking number are never grouped with each other — each is its own card, falling back to Load ID (then row ID) as the display identifier.
- "Total container count" for a card is computed from the full unfiltered active-dispatch dataset, not the currently-filtered view — so applying a filter never silently changes what "4 total" means for a booking that still has containers outside the filter.
- Does not touch `services/load_grouping_service.py` (used by the current row board) — this is a new, separate module for the swimlane redesign; consolidating them is a Phase 6+ cleanup decision, not done here.

---

### Task 1: Booking card view model with tests

**Files:**
- Create: `services/dispatch_card_view_model.py`
- Test: `tests/test_dispatch_card_view_model.py`

**Interfaces:**
- Produces: `build_booking_card_view_models(scoped_df: pd.DataFrame, totals_df: pd.DataFrame) -> list[dict]`. Each dict has keys: `group_id`, `booking_number`, `customer`, `move_type`, `canonical_status`, `row_ids` (list[int]), `visible_container_count`, `total_container_count`, `earliest_need_date`, `earliest_lfd`, `unassigned_count`, `driver_count`, `exception_count`, `workspace_url`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_card_view_model.py`:
```python
import pandas as pd

from services.dispatch_card_view_model import build_booking_card_view_models


def _row(row_id, booking, customer, move_type, status, driver="", need_date="", lfd="", exceptions=""):
    return {
        "_row_id": row_id,
        "Booking Number": booking,
        "Load ID": f"LOAD-{row_id}",
        "Customer": customer,
        "Dispatch Move Type": move_type,
        "Status": status,
        "Driver Name": driver,
        "Delivery Need Date": need_date,
        "LFD": lfd,
        "Exceptions": exceptions,
    }


def test_same_booking_customer_type_status_groups_into_one_card():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 1
    assert cards[0]["visible_container_count"] == 4
    assert cards[0]["total_container_count"] == 4
    assert sorted(cards[0]["row_ids"]) == [1, 2, 3, 4]


def test_same_booking_split_across_statuses_produces_separate_cards():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "En Route to Pickup"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "At Pickup"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 3
    by_status = {c["canonical_status"]: c for c in cards}
    assert by_status["Ready to Dispatch"]["visible_container_count"] == 2
    assert by_status["Ready to Dispatch"]["total_container_count"] == 4
    assert by_status["En Route to Pickup"]["visible_container_count"] == 1
    assert by_status["En Route to Pickup"]["total_container_count"] == 4
    assert by_status["At Pickup"]["total_container_count"] == 4


def test_same_booking_number_different_customer_does_not_group():
    df = pd.DataFrame([
        _row(1, "MAEU-1111111", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-1111111", "Customer B", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2


def test_same_booking_number_different_move_type_does_not_group():
    df = pd.DataFrame([
        _row(1, "MAEU-1111111", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-1111111", "Customer A", "Export", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2


def test_missing_booking_numbers_never_group_together():
    df = pd.DataFrame([
        _row(1, "", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "", "Customer A", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2
    assert all(card["total_container_count"] == 1 for card in cards)


def test_booking_number_normalization_trims_and_uppercases_for_grouping_only():
    df = pd.DataFrame([
        _row(1, " maeu-5560789 ", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 1
    # display value preserves the original casing/whitespace of the first row encountered
    assert cards[0]["booking_number"].strip().upper() == "MAEU-5560789"


def test_totals_reflect_unfiltered_dataset_not_the_scoped_view():
    full_df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "En Route to Pickup"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "At Pickup"),
    ])
    scoped_df = full_df[full_df["_row_id"].isin([1, 2])]  # simulate an active filter narrowing the view
    cards = build_booking_card_view_models(scoped_df, full_df)
    assert len(cards) == 1
    assert cards[0]["visible_container_count"] == 2
    assert cards[0]["total_container_count"] == 4


def test_unassigned_and_driver_counts():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver=""),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver="Alex"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver="Sam"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver=""),
    ])
    cards = build_booking_card_view_models(df, df)
    assert cards[0]["unassigned_count"] == 2
    assert cards[0]["driver_count"] == 2


def test_exception_count_sums_across_group():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", exceptions="Late appointment"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", exceptions=""),
    ])
    cards = build_booking_card_view_models(df, df)
    assert cards[0]["exception_count"] == 1


def test_workspace_url_contains_the_booking_number():
    df = pd.DataFrame([_row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch")])
    cards = build_booking_card_view_models(df, df)
    assert "MAEU-5560789" in cards[0]["workspace_url"]
    assert cards[0]["workspace_url"].startswith("?booking=")


def test_empty_dataframe_returns_no_cards():
    df = pd.DataFrame(columns=["_row_id", "Booking Number", "Load ID", "Customer", "Dispatch Move Type", "Status", "Driver Name", "Delivery Need Date", "LFD", "Exceptions"])
    assert build_booking_card_view_models(df, df) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_card_view_model.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/dispatch_card_view_model.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_card_view_model.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_card_view_model.py tests/test_dispatch_card_view_model.py
git commit -m "Add booking card view-model grouping for the swimlane board redesign"
```

---

### Task 2: Full verification

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all prior tests plus this plan's ~11 new tests pass.

This plan produces no UI change — the swimlane renderer (Phase 3), click-to-workspace wiring (Phase 4), and multi-container tabs (Phase 5) are separate follow-up plans, scoped after this view-model layer is reviewed.
