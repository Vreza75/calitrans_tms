# Load Repository Module

Files included:

- `repositories/load_repo.py`
- `repositories/__init__.py`

## Purpose

Centralizes load lookup, load matching, AI context, load update, and load communication history.

## Integration

In `services/operations_inbox_service.py`, add:

```python
from repositories import load_repo
```

Then map existing app helper calls gradually:

```python
find_load_match_candidates = load_repo.find_load_match_candidates
save_load_communication = load_repo.save_load_communication
update_load_from_parsed = load_repo.update_load_from_parsed
ai_load_context = load_repo.ai_load_context
```

Keep the old app.py functions until this is fully tested.
