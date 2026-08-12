from __future__ import annotations

from dataclasses import dataclass

from db_client import transaction
from repositories import inbox_repo, work_item_repo

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.exceptions import NotFoundError


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    work_item_id: int
    detail: str = ""


def _require_work_item(conn, work_item_id: int) -> dict:
    from sqlalchemy import text

    row = conn.execute(
        text("select id, case_id, review_status from order_intake where id = :id"),
        {"id": int(work_item_id)},
    ).mappings().first()
    if row is None:
        raise NotFoundError(f"Work item {work_item_id} not found.")
    return dict(row)


def close_work_item(work_item_id: int, *, actor: AuthenticatedActor, reason: str = "") -> CommandResult:
    """Mark a work item Closed. Single transaction: the status write and
    its case-history audit row (when the item belongs to a case) commit or
    roll back together.

    Requires Permission.WORK_ITEM_MANAGE before doing anything else - an
    unauthorized actor triggers zero reads/writes (Stage 2 closure pass).
    The real actor identity (actor.actor) is recorded on the case-history
    audit row, not a hardcoded placeholder."""
    require_permission(actor, Permission.WORK_ITEM_MANAGE)

    with transaction() as conn:
        record = _require_work_item(conn, work_item_id)
        work_item_repo.update_review_status(work_item_id, "Closed", conn=conn)

        if record.get("case_id"):
            work_item_repo.insert_case_event(
                case_id=int(record["case_id"]),
                event_type="work_item_closed",
                title=f"Work item #{work_item_id} closed.",
                details=reason,
                actor=actor.actor,
                conn=conn,
            )

    return CommandResult(ok=True, work_item_id=int(work_item_id), detail="Closed")


def link_work_item_to_load(work_item_id: int, load_id: int, *, actor: AuthenticatedActor) -> CommandResult:
    """Attach a work item to an existing load. Single transaction: the
    order_intake link and its case-history audit row commit or roll back
    together (Phase 1 rule: never a load link without the matching audit
    trail, and vice versa).

    Requires Permission.WORK_ITEM_MANAGE before doing anything else - an
    unauthorized actor triggers zero reads/writes (Stage 2 closure pass)."""
    require_permission(actor, Permission.WORK_ITEM_MANAGE)

    with transaction() as conn:
        from sqlalchemy import text

        record = _require_work_item(conn, work_item_id)

        load_row = conn.execute(
            text("select id from loads where id = :id"), {"id": int(load_id)}
        ).first()
        if load_row is None:
            raise NotFoundError(f"Load {load_id} not found.")

        work_item_repo.link_to_load(work_item_id, load_id, conn=conn)

        if record.get("case_id"):
            work_item_repo.insert_case_event(
                case_id=int(record["case_id"]),
                event_type="work_item_linked_to_load",
                title=f"Work item #{work_item_id} linked to load {load_id}.",
                actor=actor.actor,
                conn=conn,
            )

    return CommandResult(ok=True, work_item_id=int(work_item_id), detail=f"Linked to load {load_id}")
