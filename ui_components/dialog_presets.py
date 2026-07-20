# ui_components/dialog_presets.py

from __future__ import annotations

# Click-based size presets for st.dialog popups, keyed by the label shown on
# the radio control inside the popup. Deliberately click-based rather than
# native CSS drag-resize: dragging the popup's edge conflicted with the
# dialog's own click-outside/dismiss handling and made it disappear mid-drag.
# Shared across the Dispatch Board booking workspace and the Operations
# Inbox work-item popup so both grow the same way if the presets change.
DIALOG_SIZE_PRESETS: dict[str, tuple[str, str]] = {
    "Compact": ("50vw", "60vh"),
    "Medium": ("70vw", "75vh"),
    "Large": ("85vw", "85vh"),
    "Full Screen": ("96vw", "94vh"),
}
