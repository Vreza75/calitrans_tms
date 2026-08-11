"""Phase 5 (application-command authorization): the canonical role ->
business-permission matrix. See application/auth/permissions.py's own
docstring for the two-layer distinction (page visibility vs. this matrix,
the actual command-boundary security enforcement point) and the
rationale behind each role's grants.
"""
from __future__ import annotations

import pytest

from application.auth.models import AuthenticatedActor, Role
from application.auth.permissions import Permission, has_permission, require_permission
from application.exceptions import AuthorizationError


def _actor(role: Role) -> AuthenticatedActor:
    return AuthenticatedActor(actor=f"{role.value}@calitranscorp.com", role=role)


# Final role -> permission matrix, documented per Step 4/13's instruction
# to record the decided values (not the task's own illustrative "?"/N/Y
# placeholders) - True/False for every (role, permission) pair.
EXPECTED_MATRIX = {
    (Role.DISPATCHER, Permission.LOAD_VIEW): True,
    (Role.DISPATCHER, Permission.LOAD_EDIT): True,
    (Role.DISPATCHER, Permission.LOAD_CANCEL): True,
    (Role.DISPATCHER, Permission.LOAD_VERIFY): True,
    (Role.DISPATCHER, Permission.LOAD_READY_TO_DISPATCH): True,
    (Role.DISPATCHER, Permission.DRIVER_MESSAGE_SEND): True,
    (Role.DISPATCHER, Permission.DISPATCH_TRANSITION): True,
    (Role.DISPATCHER, Permission.DISPATCH_COMMUNICATION_LOG): True,
    (Role.DISPATCHER, Permission.DOCUMENT_ATTACH): True,
    (Role.DISPATCHER, Permission.PORT_DATA_APPLY): True,
    (Role.DISPATCHER, Permission.MASTER_DATA_EDIT): False,
    (Role.DISPATCHER, Permission.BILLING_VIEW): False,
    (Role.DISPATCHER, Permission.BILLING_EDIT): False,
    (Role.DISPATCHER, Permission.TASK_CREATE): True,
    (Role.DISPATCHER, Permission.TASK_EDIT): True,
    (Role.DISPATCHER, Permission.USER_ADMIN): False,
    (Role.ACCOUNTING, Permission.LOAD_VIEW): True,
    (Role.ACCOUNTING, Permission.LOAD_EDIT): False,
    (Role.ACCOUNTING, Permission.LOAD_CANCEL): False,
    (Role.ACCOUNTING, Permission.LOAD_VERIFY): False,
    (Role.ACCOUNTING, Permission.LOAD_READY_TO_DISPATCH): False,
    (Role.ACCOUNTING, Permission.DRIVER_MESSAGE_SEND): False,
    (Role.ACCOUNTING, Permission.DISPATCH_TRANSITION): False,
    (Role.ACCOUNTING, Permission.DISPATCH_COMMUNICATION_LOG): False,
    (Role.ACCOUNTING, Permission.DOCUMENT_ATTACH): True,
    (Role.ACCOUNTING, Permission.PORT_DATA_APPLY): False,
    (Role.ACCOUNTING, Permission.MASTER_DATA_EDIT): False,
    (Role.ACCOUNTING, Permission.BILLING_VIEW): True,
    (Role.ACCOUNTING, Permission.BILLING_EDIT): True,
    (Role.ACCOUNTING, Permission.TASK_CREATE): False,
    (Role.ACCOUNTING, Permission.TASK_EDIT): False,
    (Role.ACCOUNTING, Permission.USER_ADMIN): False,
    (Role.MANAGER, Permission.LOAD_VIEW): True,
    (Role.MANAGER, Permission.LOAD_EDIT): True,
    (Role.MANAGER, Permission.LOAD_CANCEL): True,
    (Role.MANAGER, Permission.LOAD_VERIFY): True,
    (Role.MANAGER, Permission.LOAD_READY_TO_DISPATCH): True,
    (Role.MANAGER, Permission.DRIVER_MESSAGE_SEND): True,
    (Role.MANAGER, Permission.DISPATCH_TRANSITION): True,
    (Role.MANAGER, Permission.DISPATCH_COMMUNICATION_LOG): True,
    (Role.MANAGER, Permission.DOCUMENT_ATTACH): True,
    (Role.MANAGER, Permission.PORT_DATA_APPLY): True,
    (Role.MANAGER, Permission.MASTER_DATA_EDIT): True,
    (Role.MANAGER, Permission.BILLING_VIEW): True,
    (Role.MANAGER, Permission.BILLING_EDIT): True,
    (Role.MANAGER, Permission.TASK_CREATE): True,
    (Role.MANAGER, Permission.TASK_EDIT): True,
    (Role.MANAGER, Permission.USER_ADMIN): False,
    (Role.ADMIN, Permission.LOAD_VIEW): True,
    (Role.ADMIN, Permission.LOAD_EDIT): True,
    (Role.ADMIN, Permission.LOAD_CANCEL): True,
    (Role.ADMIN, Permission.LOAD_VERIFY): True,
    (Role.ADMIN, Permission.LOAD_READY_TO_DISPATCH): True,
    (Role.ADMIN, Permission.DRIVER_MESSAGE_SEND): True,
    (Role.ADMIN, Permission.DISPATCH_TRANSITION): True,
    (Role.ADMIN, Permission.DISPATCH_COMMUNICATION_LOG): True,
    (Role.ADMIN, Permission.DOCUMENT_ATTACH): True,
    (Role.ADMIN, Permission.PORT_DATA_APPLY): True,
    (Role.ADMIN, Permission.MASTER_DATA_EDIT): True,
    (Role.ADMIN, Permission.BILLING_VIEW): True,
    (Role.ADMIN, Permission.BILLING_EDIT): True,
    (Role.ADMIN, Permission.TASK_CREATE): True,
    (Role.ADMIN, Permission.TASK_EDIT): True,
    (Role.ADMIN, Permission.USER_ADMIN): True,
}


def test_matrix_covers_every_role_permission_pair():
    """Guards the test itself against silently going stale - every
    (Role, Permission) combination must have an explicit expectation, and
    every Permission must appear for every Role exactly once."""
    all_pairs = {(role, permission) for role in Role for permission in Permission}
    assert set(EXPECTED_MATRIX.keys()) == all_pairs


@pytest.mark.parametrize("role_permission", list(EXPECTED_MATRIX.keys()), ids=lambda rp: f"{rp[0].value}:{rp[1].value}")
def test_role_permission_matrix(role_permission):
    role, permission = role_permission
    expected = EXPECTED_MATRIX[(role, permission)]
    assert has_permission(_actor(role), permission) is expected


def test_admin_has_every_permission_via_normal_lookup_not_a_bypass():
    """Admin passes through the same has_permission() lookup as any other
    role - not a `if role == ADMIN: return True` shortcut anywhere."""
    admin = _actor(Role.ADMIN)
    for permission in Permission:
        assert has_permission(admin, permission) is True


def test_require_permission_raises_authorization_error_when_denied():
    accountant = _actor(Role.ACCOUNTING)
    with pytest.raises(AuthorizationError, match="load:cancel"):
        require_permission(accountant, Permission.LOAD_CANCEL)


def test_require_permission_is_silent_when_granted():
    dispatcher = _actor(Role.DISPATCHER)
    require_permission(dispatcher, Permission.LOAD_CANCEL)  # must not raise


def test_has_permission_fails_closed_for_unrecognized_role():
    """A role missing from ROLE_PERMISSIONS (defensive - can't happen via
    the Role enum today, but the lookup itself must fail closed) grants
    zero permissions, not all of them."""
    from dataclasses import replace

    fake_actor = replace(_actor(Role.ADMIN), role="not-a-real-role")
    assert has_permission(fake_actor, Permission.LOAD_VIEW) is False


def test_authorization_error_message_names_role_and_permission():
    accountant = _actor(Role.ACCOUNTING)
    try:
        require_permission(accountant, Permission.DRIVER_MESSAGE_SEND)
    except AuthorizationError as exc:
        assert "accounting" in str(exc)
        assert "driver_message:send" in str(exc)
    else:
        pytest.fail("expected AuthorizationError")
