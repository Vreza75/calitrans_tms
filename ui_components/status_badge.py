from __future__ import annotations

from html import escape

from services.dispatch_workflow_service import STATUS_COLORS

_DEFAULT_BADGE_COLOR = "#E5E7EB"


def render_status_badge(status: str) -> str:
    """Return pill-styled HTML for a load/case status.

    Color comes from services.dispatch_workflow_service.STATUS_COLORS — this
    function only supplies consistent pill structure (padding, radius,
    weight, border), not the color-to-status mapping. Unknown statuses fall
    back to a neutral gray rather than failing.
    """
    label = str(status or "").strip()
    if not label:
        return ""
    color = STATUS_COLORS.get(label, _DEFAULT_BADGE_COLOR)
    return (
        f'<span class="ct-status-badge" style="background:{escape(color)};">'
        f"{escape(label)}</span>"
    )
