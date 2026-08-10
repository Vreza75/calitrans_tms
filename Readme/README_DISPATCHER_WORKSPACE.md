# Dispatcher Workspace Foundation

This package adds a new AI Dispatcher Workspace layer on top of the existing Operations Inbox.

## Files

- `pages_app/dispatcher_workspace.py`
- `services/dispatcher_workspace_service.py`
- `ui_components/work_queue_panel.py`
- `ui_components/dispatcher_action_panel.py`

## Integration

1. Copy files into the matching folders.
2. Make sure these modules already exist:
   - `repositories/inbox_repo.py`
   - `repositories/case_repo.py`
   - `repositories/load_repo.py`
   - `repositories/task_repo.py`
3. Add to `NAVIGATION_SECTIONS` in `app.py`:
   - `AI Dispatcher Workspace`
4. Add route inside `main()`:

```python
elif section == "AI Dispatcher Workspace":
    from pages_app.dispatcher_workspace import render_dispatcher_workspace
    render_dispatcher_workspace()
```

## Purpose

The Dispatcher Workspace does not replace Operations Inbox. It summarizes daily work and gives the dispatcher a faster command view.
