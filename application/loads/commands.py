from __future__ import annotations

from typing import Any

from application.exceptions import CommandFailedError, NotFoundError, ValidationError
from application.loads.models import CreateLoadResult, TransitionResult, UpdateLoadResult


def transition_load(
    load_id: int,
    new_status: str,
    *,
    note: str = "",
    driver: str | None = None,
    truck: str | None = None,
    override: bool = False,
    override_reason: str = "",
) -> TransitionResult:
    """Status transition + optional driver/truck assignment.

    Delegates to services.dispatch_transition_service.apply_transition,
    which is the one function allowed to change loads.status and, as of
    Phase 1, runs its assignment/status/closeout writes and audit rows in
    a single db_client.transaction() (see
    tests/test_dispatch_transition_service.py for the atomicity tests)."""
    from services.dispatch_transition_service import apply_transition

    result = apply_transition(
        load_id,
        new_status,
        note=note,
        driver=driver,
        truck=truck,
        override=override,
        override_reason=override_reason,
    )
    return TransitionResult(
        ok=bool(result.get("ok")),
        reason=str(result.get("reason") or ""),
        status=str(result.get("status") or ""),
        closeout_stage=str(result.get("closeout_stage") or ""),
    )


def create_load_from_work_item(work_item_id: int, approved_fields: dict[str, Any], **kwargs: Any) -> CreateLoadResult:
    """Create a load from a reviewed Operations Inbox draft.

    KNOWN LIMITATION (documented, not silently accepted): this delegates to
    services.operations_inbox_service.create_load_from_inbox_item, which
    itself makes several separate, non-atomic writes (create the load,
    insert its document row, update order_intake twice under different
    column sets, save the communication row, update the case). Phase 1
    intentionally reuses this canonical, certification-tested workflow
    rather than re-implementing multi-container/duplicate-protection
    business rules here - making the whole chain a single transaction is
    scoped to Phase 2 (see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md,
    "Known limitations").

    Lazy-imported: services.operations_inbox_service imports streamlit at
    module top, so importing it happens inside this function body rather
    than at application/ module load time.
    """
    from services.operations_inbox_service import create_load_from_inbox_item

    if not approved_fields.get("Booking Number") and not approved_fields.get("Reference Number"):
        raise ValidationError("Booking Number or Reference Number is required to create a load.")

    try:
        result = create_load_from_inbox_item(int(work_item_id), approved_fields, **kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced as a structured application error
        raise CommandFailedError(str(exc)) from exc

    return CreateLoadResult(
        ok=True,
        load_id=result.get("load_id"),
        review_status=str(result.get("review_status") or ""),
    )


def update_load_from_work_item(
    work_item_id: int,
    load_id: int,
    approved_fields: dict[str, Any],
    **kwargs: Any,
) -> UpdateLoadResult:
    """Apply an existing-load update from an Operations Inbox work item.

    Same known limitation as create_load_from_work_item: delegates to
    services.operations_inbox_service.update_load_from_inbox_item, which
    is not yet single-transaction atomic. See docs/architecture/
    BACKEND_BOUNDARY_PHASE_1.md."""
    from services.operations_inbox_service import update_load_from_inbox_item

    try:
        result = update_load_from_inbox_item(int(work_item_id), int(load_id), approved_fields, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise CommandFailedError(str(exc)) from exc

    if result.get("error"):
        raise NotFoundError(str(result["error"]))

    return UpdateLoadResult(
        ok=True,
        load_id=result.get("load_id"),
        updated_fields=list(result.get("updated_fields") or []),
        skipped_fields=list(result.get("skipped_fields") or []),
    )
