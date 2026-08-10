# application/auth/permissions.py

from __future__ import annotations

"""Canonical role -> Streamlit-nav-section permission map. Owned here
(framework-neutral) rather than in ui_components/app_shell.py so the
section labels used for filtering the sidebar and the section labels used
for the fail-closed re-check in pages_app/router.py can never drift out of
sync - both read the same NAVIGATION_SECTIONS/ADMIN_DIAGNOSTIC_OPTIONS
source of truth.

Policy defaults (confirm/adjust as a business decision, not a security
one - changing this dict does not weaken the fail-closed mechanism):
  - dispatcher: day-to-day dispatch/ops tooling, no billing, no admin/diagnostics.
  - accounting: billing plus enough load context to work invoices, no dispatch tooling.
  - manager/admin: full access, including Admin/Diagnostics tools. No page-level
    distinction between manager and admin in v1 - the API layer (api/auth.py)
    already has finer-grained per-endpoint role splits for mutations; this
    module only gates Streamlit page *visibility*, not individual actions.
"""

from application.auth.models import Role

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
