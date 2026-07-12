# Dispatcher Action Panel

## Files

- `ui_components/dispatcher_action_panel.py`

## Purpose

This component gives dispatchers one clean panel to review an AI recommendation and choose the next action.

It is meant to replace scattered buttons and raw JSON in `operations_inbox.py` / `dispatcher_workspace.py`.

## Integration

Import:

```python
from ui_components.dispatcher_action_panel import render_dispatcher_action_panel
```

Use inside a selected case/email review section:

```python
action_payload = render_dispatcher_action_panel(
    selected_id=int(selected_id),
    record=record.to_dict() if hasattr(record, "to_dict") else record,
    parsed=parsed,
    operations_case=operations_case,
    matched_load_id=matched_load_id,
)

if action_payload:
    st.json(action_payload)
    # Later: send to dispatcher_workspace_service.apply_dispatcher_action(action_payload)
```

## Next step

Create `services/dispatcher_action_service.py` to apply each action:

- Approve AI Recommendation
- Edit Before Applying
- Reject Recommendation
- Create Follow-up Task
- Attach to Load
- Update Load Fields
- Draft Reply
- Mark Waiting Customer
- Close / No Action
