# AI Review Panel Module

## Files

Place these in your project:

```text
ui_components/
  __init__.py
  ai_review_panel.py
```

## Purpose

This replaces raw AI JSON with a dispatcher-friendly review panel:

- AI Summary
- Intent
- Language
- Confidence
- Department Owner
- Recommended Action
- Load Match
- Proposed Updates
- Draft Reply
- Warnings
- Raw JSON available in an expander

## Integration

In `pages_app/operations_inbox.py`, add:

```python
from ui_components.ai_review_panel import render_ai_review_panel
```

Then replace the old raw JSON agent display block:

```python
st.markdown("### AI Agent Review")
# old Run AI Agents Now block
# old agent_keys JSON loop
```

with:

```python
action_state = render_ai_review_panel(
    parsed=parsed,
    intake_id=int(record.get("id", 0)),
    show_raw_json=False,
)
```

For now, keep your existing "Run AI Agents Now" block below it or wire it into `on_run_agents` later.

## Suggested Next Improvement

After this panel works, move the actual `Run AI Agents Now` logic from `operations_inbox.py` into:

```text
services/ai_review_service.py
```

Then pass it into the panel as `on_run_agents`.
