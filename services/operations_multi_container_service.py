# services/operations_multi_container_service.py

from __future__ import annotations

from db_client import execute, read_df
from services.operations_inbox_service import create_load_from_inbox_item, safe_str


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pending_container_sequences(container_qty: int, containers_created: int) -> list[int]:
    """Sequence numbers that still need a placeholder container load.

    Pure function, no DB access. Callers must already have validated
    container_qty to a sane range (e.g. 1-10) — this function does not
    reject or clamp an out-of-range value, it just walks it.
    """
    if container_qty <= containers_created:
        return []
    return list(range(containers_created + 1, container_qty + 1))


def create_container_work_orders(
    *,
    intake_id: int,
    draft: dict,
    conversation_key: str,
    subject: str,
    sender: str,
    container_qty: int,
    containers_created: int,
) -> dict:
    """
    Create the remaining placeholder container work orders for a multi-container
    booking draft.

    Idempotent: a sequence that already has a load for this parent_booking_key
    is skipped, so calling this twice never creates duplicates. That's also
    enforced at the DB level by a unique index on
    loads(parent_booking_key, container_sequence) — this in-app check just
    avoids an unnecessary failed insert on the common path.

    Actual container numbers are intentionally left blank; they're assigned
    later once the carrier issues them.
    """
    draft = draft if isinstance(draft, dict) else {}
    created_load_ids: list[int] = []
    errors: list[str] = []

    for sequence in pending_container_sequences(container_qty, containers_created):
        try:
            existing_df = read_df(
                """
                select id
                from public.loads
                where parent_booking_key = :parent_booking_key
                  and container_sequence = :container_sequence
                limit 1
                """,
                {"parent_booking_key": conversation_key, "container_sequence": sequence},
            )
        except Exception:
            existing_df = None

        if existing_df is not None and not existing_df.empty:
            continue

        child_notes = (
            f"Created from multi-container booking draft {conversation_key}. "
            f"Container {sequence} of {container_qty}."
        )

        try:
            result = create_load_from_inbox_item(
                int(intake_id),
                {
                    "TYPE": safe_str(draft.get("service_flow", "")) or "Export",
                    "Booking Number": safe_str(draft.get("booking_number", "")),
                    "Reference Number": safe_str(draft.get("reference_number", "")),
                    "Customer": safe_str(draft.get("customer", "")),
                    "Container Number": "",
                    "Port": (
                        safe_str(draft.get("full_return_terminal", ""))
                        or safe_str(draft.get("port_of_loading", ""))
                    ),
                    "Warehouse": (
                        safe_str(draft.get("destination_warehouse", ""))
                        or safe_str(draft.get("empty_pickup_location", ""))
                    ),
                    "Address": safe_str(draft.get("delivery_address", "")),
                    "Document Cutoff": safe_str(draft.get("document_cutoff", "")),
                    "Delivery Need Date": safe_str(draft.get("delivery_need_date", "")),
                    "LFD": safe_str(draft.get("lfd", "")),
                    "Size": safe_str(draft.get("container_size", "")),
                    "Status": "New",
                    "Dispatcher Notes": child_notes,
                    "Commodity": safe_str(draft.get("commodity", "")),
                },
                conversation_key=conversation_key,
                request_type="New Booking",
                subject=subject,
                sender=sender,
                body=(
                    f"Multi-container booking placeholder. "
                    f"Container {sequence} of {container_qty}."
                ),
                case_id=None,
                case_priority="Normal",
            )

            result = result if isinstance(result, dict) else {}
            load_id = result.get("load_id")

            if not load_id:
                errors.append(f"Container {sequence}: no load ID was returned.")
                continue

            load_id = int(load_id)
            created_load_ids.append(load_id)

            execute(
                """
                update public.loads
                set parent_booking_key = :parent_booking_key,
                    container_sequence = :container_sequence,
                    container_total = :container_total,
                    is_placeholder_container = true,
                    commodity = :commodity,
                    empty_pickup_location = :empty_pickup_location,
                    full_return_terminal = :full_return_terminal,
                    port_of_loading = :port_of_loading,
                    port_of_discharge = :port_of_discharge,
                    vessel_name = :vessel_name,
                    steamship_line = :steamship_line
                where id = :load_id
                """,
                {
                    "load_id": load_id,
                    "parent_booking_key": conversation_key,
                    "container_sequence": sequence,
                    "container_total": container_qty,
                    "commodity": safe_str(draft.get("commodity", "")),
                    "empty_pickup_location": safe_str(draft.get("empty_pickup_location", "")),
                    "full_return_terminal": safe_str(draft.get("full_return_terminal", "")),
                    "port_of_loading": safe_str(draft.get("port_of_loading", "")),
                    "port_of_discharge": safe_str(draft.get("port_of_discharge", "")),
                    "vessel_name": safe_str(draft.get("vessel_name", "")),
                    "steamship_line": safe_str(draft.get("steamship_line", "")),
                },
            )
        except Exception as exc:
            errors.append(f"Container {sequence}: {exc}")

    try:
        count_df = read_df(
            """
            select count(*)::int as created_count
            from public.loads
            where parent_booking_key = :parent_booking_key
            """,
            {"parent_booking_key": conversation_key},
        )
        actual_created_count = (
            _safe_int(count_df.iloc[0].get("created_count"), containers_created + len(created_load_ids))
            if count_df is not None and not count_df.empty
            else containers_created + len(created_load_ids)
        )

        execute(
            """
            update public.order_intake_drafts
            set containers_created = :created_count,
                draft_status = case
                    when :created_count >= coalesce(container_qty, 1)
                    then 'Container Work Orders Created'
                    else 'Container Work Orders Partially Created'
                end,
                request_stage = case
                    when :created_count >= coalesce(container_qty, 1)
                    then 'Container Work Orders Created'
                    else 'Container Work Orders Partially Created'
                end,
                updated_at = now()
            where conversation_key = :conversation_key
            """,
            {"conversation_key": conversation_key, "created_count": actual_created_count},
        )
    except Exception as exc:
        errors.append(f"Could not update draft count: {exc}")
        actual_created_count = containers_created + len(created_load_ids)

    return {
        "created_load_ids": created_load_ids,
        "errors": errors,
        "containers_created": actual_created_count,
    }
