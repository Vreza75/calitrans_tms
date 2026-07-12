# Calitrans AI App Refactor Patch - 2026-07-03

## What this patch fixes

1. Removes a broken import from `app.py`:
   - `render_operations_inbox_page` did not exist in `pages_app/operations_inbox.py`.

2. Adds the new `AI Dispatcher Workspace` page into the Streamlit navigation and route.

3. Restores missing compatibility functions expected by `pages_app/operations_inbox.py`, including:
   - AI agent instances
   - AI load context helpers
   - reply drafting helpers
   - SMTP send helper
   - reply logging
   - load communication logging
   - AI feedback logging
   - conversation status syncing

4. Fixes incomplete service extractions:
   - `services/communication_service.py`
   - `services/operations_case_service.py`
   - `services/operations_reply_service.py`
   - `services/operational_ai_feedback_service.py`
   - `services/operations_email_sync_service.py`

5. Fixes broken UI/helper modules:
   - `ui_components/ops_metric_card.py`
   - `ui_components/operation_case_panel.py`

6. Fixes missing pandas import in:
   - `ai_core/context_builder.py`

## Validation performed

Ran Python compile validation on the main app files and modules:

```bash
python -m compileall -q app.py admin_pages.py ai_agents ai_core api pages_app repositories services ui_components utils config.py db_client.py email_ingest.py operations_ai.py smartsheet_client.py validators.py
```

Also ran a static undefined-global check on Python files outside `.venv`, `.git`, backups, and pycache. No undefined-global issues were found in active code files after this patch.

## Important note

This is a patch overlay, not a full project export. Copy these files over the same paths in your current project. It intentionally does not include `.env`, `.streamlit/secrets.toml`, `.venv`, `.git`, storage attachments, or cached files.

## Recommended next refactor

Next extraction should be the Dispatch Board / Load Workspace section from `app.py`, because it still owns too much UI, status workflow, dispatch action logic, customer email status updates, and load readiness logic.

Recommended target structure:

```text
pages_app/dispatch_board.py
services/dispatch_workflow_service.py
services/customer_status_email_service.py
ui_components/load_workspace_panel.py
ui_components/status_timeline_panel.py
```
