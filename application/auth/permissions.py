# application/auth/permissions.py

from __future__ import annotations

"""Canonical role -> Streamlit-nav-section permission map, PLUS (Phase 5)
the canonical role -> business-permission matrix that authorizes
individual mutating commands. Owned here (framework-neutral) rather than
in ui_components/app_shell.py so the section labels used for filtering
the sidebar and the section labels used for the fail-closed re-check in
pages_app/router.py can never drift out of sync - both read the same
NAVIGATION_SECTIONS/ADMIN_DIAGNOSTIC_OPTIONS source of truth.

Policy defaults (confirm/adjust as a business decision, not a security
one - changing this dict does not weaken the fail-closed mechanism):
  - dispatcher: day-to-day dispatch/ops tooling, no billing, no admin/diagnostics.
  - accounting: billing plus enough load context to work invoices, no dispatch tooling.
  - manager/admin: full access, including Admin/Diagnostics tools. No page-level
    distinction between manager and admin in v1 - the API layer (api/auth.py)
    already has finer-grained per-endpoint role splits for mutations; this
    module also has the finer-grained Permission matrix below for
    individual application commands.

## Two authorization layers, do not confuse them

1. Page/section visibility (NAVIGATION_SECTIONS / ROLE_ALLOWED_SECTIONS /
   is_section_permitted below): controls what a role can *see* -
   Streamlit sidebar filtering plus pages_app/router.py's independent
   re-check. This is UX, not the security boundary - hiding a button or a
   whole page is not enforcement.
2. Business-permission matrix (Permission / ROLE_PERMISSIONS /
   has_permission / require_permission below): controls what a role can
   *do* - the actual security boundary, enforced inside application
   commands (application/loads/commands.py etc.) before any mutation
   happens, independent of which interface (Streamlit, API, a future
   frontend, an AI agent, a worker) invoked the command. A caller cannot
   bypass this by calling a service/repository function directly instead
   of going through the Streamlit page that normally exposes it, because
   the command itself - not the page - is where require_permission() is
   called.
"""

from enum import Enum

from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError

NAVIGATION_SECTIONS = [
    "Operations Inbox",
    "Orders/Load Management",
    "Dispatch Board",
    "Active Status",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Dashboard",
    "Admin / Diagnostics",
]

ADMIN_DIAGNOSTIC_OPTIONS = {
    "Master Data": "Master Data",
    "Email Imports / Diagnostics": "Email Imports",
    "Port Houston Setup / Testing": "Port Houston Integration",
    "Validation": "Validation",
    "AI Dispatcher Workspace (Experimental)": "AI Dispatcher Workspace",
}

_ADMIN_DIAGNOSTIC_SECTIONS = set(ADMIN_DIAGNOSTIC_OPTIONS.values())
_ALL_ROUTED_SECTIONS = (set(NAVIGATION_SECTIONS) - {"Admin / Diagnostics"}) | _ADMIN_DIAGNOSTIC_SECTIONS

ROLE_ALLOWED_SECTIONS: dict[Role, set[str]] = {
    Role.DISPATCHER: {
        "Operations Inbox",
        "Orders/Load Management",
        "Dispatch Board",
        "Active Status",
        "Calendar View",
        "Documents",
        "Dashboard",
    },
    Role.ACCOUNTING: {
        "Billing / ProfitTools",
        "Dashboard",
        "Documents",
        "Orders/Load Management",
    },
    Role.MANAGER: set(_ALL_ROUTED_SECTIONS),
    Role.ADMIN: set(_ALL_ROUTED_SECTIONS),
}


def allowed_top_level_sections(role: Role) -> list[str]:
    """Top-level radio-nav options this role may see, in canonical order.
    'Admin / Diagnostics' is included only if at least one of its
    sub-options is permitted."""
    allowed = ROLE_ALLOWED_SECTIONS.get(role, set())
    sections = [s for s in NAVIGATION_SECTIONS if s != "Admin / Diagnostics" and s in allowed]
    if allowed & _ADMIN_DIAGNOSTIC_SECTIONS:
        sections.append("Admin / Diagnostics")
    return sections


def allowed_admin_diagnostic_labels(role: Role) -> dict[str, str]:
    allowed = ROLE_ALLOWED_SECTIONS.get(role, set())
    return {label: section for label, section in ADMIN_DIAGNOSTIC_OPTIONS.items() if section in allowed}


def is_section_permitted(role: Role, section: str) -> bool:
    """Fail-closed: an unrecognized role or an unrecognized/unpermitted
    section both return False, never True."""
    return section in ROLE_ALLOWED_SECTIONS.get(role, set())


# ---------------------------------------------------------------------------
# Phase 5: business-permission matrix (the actual command-boundary security
# enforcement point - see the module docstring's "Two authorization layers").
# ---------------------------------------------------------------------------


class Permission(str, Enum):
    """A capability, not a page. Named after the business operation it
    guards (repository/domain terminology - loads, driver messages,
    billing, tasks, user administration), not after a Streamlit section or
    button label - a permission must mean the same thing regardless of
    which interface is asking."""

    LOAD_VIEW = "load:view"
    LOAD_EDIT = "load:edit"
    LOAD_CANCEL = "load:cancel"
    LOAD_VERIFY = "load:verify"
    LOAD_READY_TO_DISPATCH = "load:ready_to_dispatch"
    DRIVER_MESSAGE_SEND = "driver_message:send"

    BILLING_VIEW = "billing:view"
    BILLING_EDIT = "billing:edit"

    TASK_CREATE = "task:create"
    TASK_EDIT = "task:edit"

    USER_ADMIN = "user:admin"


_ALL_PERMISSIONS = frozenset(Permission)

# Decisions and rationale (business policy, not security mechanism - see
# module docstring; changing this dict does not weaken require_permission's
# fail-closed behavior for an unknown role/permission):
#
# - DISPATCHER gets every LOAD_*/DRIVER_MESSAGE_SEND/TASK_* capability.
#   Cancelling a bad booking is explicitly part of this role's own page
#   today - pages_app/orders_management.py's own caption reads "...resolve
#   missing information, mark bookings verified, or cancel bad orders
#   before dispatch work begins" - so LOAD_CANCEL is a documented, existing
#   day-to-day dispatcher task, not a guess. No billing, no user admin -
#   matches this role's existing page-visibility scope (no
#   "Billing / ProfitTools", no "Admin / Diagnostics").
# - ACCOUNTING gets LOAD_VIEW (the load context this role's own page-access
#   docstring says it needs "to work invoices") plus BILLING_VIEW/EDIT.
#   Deliberately NOT granted LOAD_EDIT/LOAD_CANCEL/LOAD_VERIFY/
#   LOAD_READY_TO_DISPATCH/DRIVER_MESSAGE_SEND/TASK_* - nothing in this
#   repository establishes accounting should perform operational dispatch
#   mutations, and the more restrictive mapping is the documented default
#   until a real business requirement says otherwise (this is exactly the
#   gap the Phase 4 audit found: accounting could reach these buttons
#   purely because they lived on the same page it needs for invoice
#   context, not because any rule granted the capability).
# - MANAGER gets every operational capability DISPATCHER has, plus
#   BILLING_VIEW/EDIT (this role's page-visibility already includes
#   "Billing / ProfitTools", so gating the equivalent action-level
#   capability behind the same role is consistent, not a new grant).
#   Deliberately NOT granted USER_ADMIN - nothing in this repository
#   establishes manager should provision/deactivate login accounts (that
#   has only ever been done via scripts/create_admin_user.py, an
#   operator-run script, not any in-app manager action); the more
#   restrictive mapping is the documented default until a real business
#   requirement says otherwise.
# - ADMIN gets every permission that exists. This is still enforced
#   through the normal has_permission()/require_permission() lookup below,
#   not a role == ADMIN shortcut anywhere in this module or its callers -
#   admin is not equated with an authentication/authorization bypass.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.DISPATCHER: frozenset(
        {
            Permission.LOAD_VIEW,
            Permission.LOAD_EDIT,
            Permission.LOAD_CANCEL,
            Permission.LOAD_VERIFY,
            Permission.LOAD_READY_TO_DISPATCH,
            Permission.DRIVER_MESSAGE_SEND,
            Permission.TASK_CREATE,
            Permission.TASK_EDIT,
        }
    ),
    Role.ACCOUNTING: frozenset(
        {
            Permission.LOAD_VIEW,
            Permission.BILLING_VIEW,
            Permission.BILLING_EDIT,
        }
    ),
    Role.MANAGER: frozenset(
        {
            Permission.LOAD_VIEW,
            Permission.LOAD_EDIT,
            Permission.LOAD_CANCEL,
            Permission.LOAD_VERIFY,
            Permission.LOAD_READY_TO_DISPATCH,
            Permission.DRIVER_MESSAGE_SEND,
            Permission.BILLING_VIEW,
            Permission.BILLING_EDIT,
            Permission.TASK_CREATE,
            Permission.TASK_EDIT,
        }
    ),
    Role.ADMIN: _ALL_PERMISSIONS,
}


def has_permission(actor: AuthenticatedActor, permission: Permission) -> bool:
    """Pure predicate, no exception. Fail-closed: an actor with an
    unrecognized role has zero permissions, never all of them."""
    return permission in ROLE_PERMISSIONS.get(actor.role, frozenset())


def require_permission(actor: AuthenticatedActor, permission: Permission) -> None:
    """Raise application.exceptions.AuthorizationError if `actor` lacks
    `permission`. This is the actual command-boundary enforcement call -
    application commands call this before any mutation, not Streamlit
    pages calling it as a UI convenience. No streamlit import, no HTTP
    response shaping - callers (Streamlit pages, FastAPI routers) each
    translate AuthorizationError into their own UI/HTTP behavior."""
    if not has_permission(actor, permission):
        raise AuthorizationError(
            f"Role '{actor.role.value}' does not have permission '{permission.value}'."
        )
