"""Phase 5B (B12): narrow regression guard against authorization bypasses
silently reappearing - a Streamlit page calling a high-risk mutation
method directly again instead of going through an application command.

Not a blanket AST rule (too many false positives - application/*/
commands.py modules and db_client.py itself legitimately call these
methods). Instead: scan pages_app/*.py and admin_pages.py for direct
calls to the specific high-risk methods migrated in Phase 5/5B, and fail
if a call site exists that isn't in the documented ALLOWED_DIRECT_CALLS
inventory below. Adding a new entry to that inventory is a deliberate,
reviewable act - not something that happens by accident.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_HIGH_RISK_PATTERN = re.compile(
    r"\.update_row_fields\(|\.add_row\(|\.attach_file_to_row\(|\bapply_transition\(|\bsend_sms\("
)

_SCAN_FILES = sorted((REPO_ROOT / "pages_app").glob("*.py")) + [REPO_ROOT / "admin_pages.py"]

# (relative_path, 1-indexed line number) -> reason. Every entry here is a
# call site NOT protected by an application-command permission check -
# keep this list short and treat any addition as a real security review,
# not a rubber stamp.
ALLOWED_DIRECT_CALLS: dict[tuple[str, int], str] = {
    ("pages_app/documents.py", 102): "render_pdf_intake() is confirmed dead code - zero live callers anywhere in the repo (Phase 5B mutation inventory); not reachable from the live app.",
    ("pages_app/documents.py", 117): "render_pdf_intake() is confirmed dead code - see above.",
    ("pages_app/port_houston_integration.py", 478): "Gated by an explicit require_permission(principal, Permission.PORT_DATA_APPLY) call on the immediately preceding line inside the same try block - see _render_load_port_houston_panel's Sync Port Data handler.",
    ("pages_app/port_houston_integration.py", 598): "Gated by an explicit require_permission(principal, Permission.PORT_DATA_APPLY) call on the immediately preceding line inside the same try block - see the Save PIN / Appointment To Load handler.",
}

# orders_management.py's dead functions carry 9 call sites - listed as a
# single wildcard entry (file-level) rather than 9 individual line
# numbers, since they're all the same confirmed-unreachable functions
# (_render_booking_verification_actions, render_booking_review) and
# re-verifying reachability per-line adds no signal. If either function
# ever gains a real caller, this allowlist must be replaced with real
# authorization, not just widened.
_DEAD_CODE_FILES = {"pages_app/orders_management.py"}


def _find_violations() -> list[tuple[str, int, str]]:
    violations = []
    for path in _SCAN_FILES:
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not _HIGH_RISK_PATTERN.search(line):
                continue
            if rel_path in _DEAD_CODE_FILES:
                continue
            if (rel_path, line_number) in ALLOWED_DIRECT_CALLS:
                continue
            violations.append((rel_path, line_number, line.strip()))
    return violations


def test_no_unreviewed_direct_high_risk_mutation_calls():
    violations = _find_violations()
    assert not violations, (
        "Direct high-risk mutation call(s) found outside the application-command "
        "layer, with no ALLOWED_DIRECT_CALLS entry explaining why. Either route "
        "the call through an application/*/commands.py function that calls "
        "require_permission() first, or add a reviewed entry to "
        "ALLOWED_DIRECT_CALLS in this file with a real justification. "
        "Violations:\n" + "\n".join(f"  {p}:{n}: {l}" for p, n, l in violations)
    )


def test_dead_code_files_are_still_actually_unreachable():
    """If this ever fails, _render_booking_verification_actions/
    render_booking_review gained a real caller and the ALLOWED_DIRECT_CALLS
    wildcard above is no longer valid - those functions need real
    authorization, not a dead-code exemption."""
    import subprocess

    for func_name in ["_render_booking_verification_actions", "render_booking_review"]:
        result = subprocess.run(
            ["git", "grep", "-n", func_name, "--", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        call_sites = [
            line for line in result.stdout.splitlines()
            if "pages_app/orders_management.py" not in line and "storage/app.py" not in line
        ]
        assert not call_sites, f"{func_name} now has a live caller outside orders_management.py/storage/app.py: {call_sites}"
