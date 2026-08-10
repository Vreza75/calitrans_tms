from __future__ import annotations

from application.auth.models import Role
from application.auth.permissions import (
    NAVIGATION_SECTIONS,
    allowed_admin_diagnostic_labels,
    allowed_top_level_sections,
    is_section_permitted,
)


def test_dispatcher_cannot_see_billing() -> None:
    assert "Billing / ProfitTools" not in allowed_top_level_sections(Role.DISPATCHER)


def test_dispatcher_cannot_see_admin_diagnostics_umbrella() -> None:
    assert "Admin / Diagnostics" not in allowed_top_level_sections(Role.DISPATCHER)


def test_dispatcher_sees_operations_inbox() -> None:
    assert "Operations Inbox" in allowed_top_level_sections(Role.DISPATCHER)


def test_accounting_sees_billing_but_not_dispatch_board() -> None:
    sections = allowed_top_level_sections(Role.ACCOUNTING)
    assert "Billing / ProfitTools" in sections
    assert "Dispatch Board" not in sections
    assert "Operations Inbox" not in sections


def test_admin_and_manager_see_every_top_level_section_including_admin_umbrella() -> None:
    for role in (Role.ADMIN, Role.MANAGER):
        sections = allowed_top_level_sections(role)
        assert "Admin / Diagnostics" in sections
        for real_section in NAVIGATION_SECTIONS:
            if real_section == "Admin / Diagnostics":
                continue
            assert real_section in sections


def test_admin_diagnostic_sub_options_hidden_from_dispatcher_and_accounting() -> None:
    assert allowed_admin_diagnostic_labels(Role.DISPATCHER) == {}
    assert allowed_admin_diagnostic_labels(Role.ACCOUNTING) == {}


def test_admin_diagnostic_sub_options_all_visible_to_admin() -> None:
    labels = allowed_admin_diagnostic_labels(Role.ADMIN)
    assert "Master Data" in labels
    assert "Validation" in labels


def test_is_section_permitted_fails_closed_for_dispatcher_on_billing() -> None:
    assert is_section_permitted(Role.DISPATCHER, "Billing / ProfitTools") is False


def test_is_section_permitted_true_for_permitted_pair() -> None:
    assert is_section_permitted(Role.DISPATCHER, "Dashboard") is True


def test_is_section_permitted_fails_closed_for_nonexistent_section() -> None:
    assert is_section_permitted(Role.ADMIN, "Not A Real Section") is False
