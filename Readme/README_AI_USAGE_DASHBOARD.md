# AI Usage Dashboard Module

## Files

- `repositories/ai_usage_repo.py`
- `pages_app/ai_usage_dashboard.py`

## Purpose

Tracks AI/API usage by agent, model, token count, estimated cost, latency, failures, cache hits, and recent calls.

## Integration

1. Copy `repositories/ai_usage_repo.py` into your project.
2. Copy `pages_app/ai_usage_dashboard.py` into your project.
3. In `app.py`, add a navigation item:

```python
"AI Usage"
```

4. In your routing section:

```python
elif section == "AI Usage":
    from pages_app.ai_usage_dashboard import render_ai_usage_dashboard
    render_ai_usage_dashboard()
```

5. Update `ai_core/usage_logger.py` so it calls:

```python
from repositories.ai_usage_repo import log_ai_usage_record
```

Then inside your existing `log_ai_usage(...)` function, call `log_ai_usage_record(...)` with the same fields.

## Notes

Pricing values are internal estimates and should be updated if model prices change.
