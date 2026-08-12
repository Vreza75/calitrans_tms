"""Stage 2 closure pass: command-boundary enforcement for
api/routers/work_items.py's mutations (draft edit, create-load,
update-load, close, link-load). Same pattern as
tests/test_phase5b_command_authorization.py - every DB/service side
effect is mocked; assertions are on WHETHER a mutation was attempted,
not on real database state. These commands are the actual security
boundary - api/routers/work_items.py's coarse require_role(*MUTATE_OPERATIONS)
dependency is defense-in-depth, not the authoritative check, so every
test here calls the application command directly, bypassing FastAPI
entirely, to prove the boundary holds independent of the router.
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
MANAGER = AuthenticatedActor(actor="manager@calitranscorp.com", role=Role.MANAGER)
ADMIN = AuthenticatedActor(actor="admin@calitranscorp.com", role=Role.ADMIN)
ACCOUNTING = AuthenticatedActor(actor="accountant@calitranscorp.com", role=Role.ACCOUNTING)


# ---------------------------------------------------------------------------
# application/order_drafts/commands.py::update_order_draft
# ---------------------------------------------------------------------------


def test_accounting_cannot_update_order_draft_zero_mutation():
    from application.order_drafts.commands import update_order_draft

    with mock.patch("repositories.work_item_repo.update_order_draft_fields") as update_fields:
        with pytest.raises(AuthorizationError):
            update_order_draft("conv-1", {"Customer": "Acme"}, actor=ACCOUNTING)
    update_fields.assert_not_called()


@pytest.mark.parametrize("actor", [DISPATCHER, MANAGER, ADMIN], ids=lambda a: a.role.value)
def test_permitted_roles_can_update_order_draft(actor):
    from application.order_drafts.commands import update_order_draft

    with mock.patch("repositories.work_item_repo.update_order_draft_fields", return_value=True) as update_fields:
        result = update_order_draft("conv-1", {"booking_number": "BK1"}, actor=actor)
    assert result.ok is True
    update_fields.assert_called_once()


def test_update_order_draft_missing_actor_fails_closed():
    from application.order_drafts.commands import update_order_draft

    with mock.patch("repositories.work_item_repo.update_order_draft_fields") as update_fields:
        with pytest.raises(TypeError):
            update_order_draft("conv-1", {"Customer": "Acme"})  # type: ignore[call-arg]
    update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# application/loads/commands.py::create_load_from_work_item /
# update_load_from_work_item
# ---------------------------------------------------------------------------


def test_accounting_cannot_create_load_from_work_item_zero_mutation():
    from application.loads.commands import create_load_from_work_item

    with mock.patch("services.operations_inbox_service.create_load_from_inbox_item") as create_call:
        with pytest.raises(AuthorizationError):
            create_load_from_work_item(1, {"Booking Number": "BK1"}, actor=ACCOUNTING)
    create_call.assert_not_called()


@pytest.mark.parametrize("actor", [DISPATCHER, MANAGER, ADMIN], ids=lambda a: a.role.value)
def test_permitted_roles_can_create_load_from_work_item(actor):
    from application.loads.commands import create_load_from_work_item

    with mock.patch(
        "services.operations_inbox_service.create_load_from_inbox_item",
        return_value={"load_id": 42, "review_status": "Ready"},
    ) as create_call:
        result = create_load_from_work_item(1, {"Booking Number": "BK1"}, actor=actor)
    assert result.ok is True
    create_call.assert_called_once()


def test_create_load_from_work_item_missing_actor_fails_closed():
    from application.loads.commands import create_load_from_work_item

    with mock.patch("services.operations_inbox_service.create_load_from_inbox_item") as create_call:
        with pytest.raises(TypeError):
            create_load_from_work_item(1, {"Booking Number": "BK1"})  # type: ignore[call-arg]
    create_call.assert_not_called()


def test_accounting_cannot_update_load_from_work_item_zero_mutation():
    from application.loads.commands import update_load_from_work_item

    with mock.patch("services.operations_inbox_service.update_load_from_inbox_item") as update_call:
        with pytest.raises(AuthorizationError):
            update_load_from_work_item(1, 42, {"Customer": "Acme"}, actor=ACCOUNTING)
    update_call.assert_not_called()


@pytest.mark.parametrize("actor", [DISPATCHER, MANAGER, ADMIN], ids=lambda a: a.role.value)
def test_permitted_roles_can_update_load_from_work_item(actor):
    from application.loads.commands import update_load_from_work_item

    with mock.patch(
        "services.operations_inbox_service.update_load_from_inbox_item",
        return_value={"load_id": 42, "updated_fields": ["Customer"], "skipped_fields": []},
    ) as update_call:
        result = update_load_from_work_item(1, 42, {"Customer": "Acme"}, actor=actor)
    assert result.ok is True
    update_call.assert_called_once()


# ---------------------------------------------------------------------------
# application/work_items/commands.py::close_work_item / link_work_item_to_load
# ---------------------------------------------------------------------------


def test_accounting_cannot_close_work_item_zero_mutation():
    from application.work_items import commands as work_item_commands

    with mock.patch("application.work_items.commands.transaction") as transaction:
        with pytest.raises(AuthorizationError):
            work_item_commands.close_work_item(1, actor=ACCOUNTING, reason="test")
    transaction.assert_not_called()


def test_dispatcher_can_close_work_item_and_real_actor_is_recorded():
    from application.work_items import commands as work_item_commands

    conn = mock.MagicMock()
    with mock.patch("application.work_items.commands.transaction") as transaction:
        transaction.return_value.__enter__.return_value = conn
        with mock.patch.object(
            work_item_commands, "_require_work_item", return_value={"id": 1, "case_id": 55}
        ):
            with mock.patch("repositories.work_item_repo.update_review_status") as update_status:
                with mock.patch("repositories.work_item_repo.insert_case_event") as insert_event:
                    result = work_item_commands.close_work_item(1, actor=DISPATCHER, reason="handled")
    assert result.ok is True
    update_status.assert_called_once()
    insert_event.assert_called_once()
    assert insert_event.call_args.kwargs["actor"] == DISPATCHER.actor


def test_close_work_item_missing_actor_fails_closed():
    from application.work_items import commands as work_item_commands

    with mock.patch("application.work_items.commands.transaction") as transaction:
        with pytest.raises(TypeError):
            work_item_commands.close_work_item(1, reason="test")  # type: ignore[call-arg]
    transaction.assert_not_called()


def test_accounting_cannot_link_work_item_to_load_zero_mutation():
    from application.work_items import commands as work_item_commands

    with mock.patch("application.work_items.commands.transaction") as transaction:
        with pytest.raises(AuthorizationError):
            work_item_commands.link_work_item_to_load(1, load_id=100, actor=ACCOUNTING)
    transaction.assert_not_called()


@pytest.mark.parametrize("actor", [DISPATCHER, MANAGER, ADMIN], ids=lambda a: a.role.value)
def test_permitted_roles_can_link_work_item_to_load(actor):
    from application.work_items import commands as work_item_commands

    conn = mock.MagicMock()
    with mock.patch("application.work_items.commands.transaction") as transaction:
        transaction.return_value.__enter__.return_value = conn
        with mock.patch.object(
            work_item_commands, "_require_work_item", return_value={"id": 1, "case_id": None}
        ):
            with mock.patch.object(conn, "execute") as execute:
                execute.return_value.first.return_value = (100,)
                with mock.patch("repositories.work_item_repo.link_to_load") as link_call:
                    result = work_item_commands.link_work_item_to_load(1, load_id=100, actor=actor)
    assert result.ok is True
    link_call.assert_called_once()
