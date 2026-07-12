# Case Summary Panel Module

## Files

Copy these into your project:

```text
ui_components/__init__.py
ui_components/case_summary_panel.py
```

## Import

In `pages_app/operations_inbox.py`:

```python
from ui_components.case_summary_panel import render_case_summary_panel, render_case_empty_state
```

## Usage

Replace scattered case header metrics with:

```python
render_case_summary_panel(
    operations_case=operations_case,
    record=record.to_dict() if hasattr(record, "to_dict") else record,
    parsed=parsed,
    tokens=tokens,
    matched_load_id=matched_load_id,
    timeline_df=None,
    expanded=True,
)
```

If no case exists:

```python
render_case_empty_state()
```

## Purpose

This turns case data into a dispatcher-friendly panel:

- Case number
- Status
- Owner
- Priority
- SLA status
- Customer
- Booking / container / reference
- Linked load
- Next action
- Recent timeline

This component is UI-only. It does not write to the database.
