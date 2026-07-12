# Calitrans Port Houston Refactor Patch

This patch continues the App.py cleanup after the Dispatch / Active Status refactor.

## What changed

- Moved the Port Houston Integration workspace out of `app.py`.
- Created `pages_app/port_houston_integration.py`.
- Exposed `render_port_houston_integration(df)` for the main navigation route.
- Exposed `render_load_port_houston_panel(selected_load, readiness)` so Dispatch Board and Active Status can still show the embedded Port Sync panel.
- Kept the current behavior for:
  - Port Houston setup / credential check
  - Load sync
  - Live lookup
  - Appointment payload builder
  - Subscription tools
  - Drayage mapping
  - Port sync logging
  - PIN / appointment save flow

## Files to copy

Copy these files into the same folder paths in your project:

```text
app.py
pages_app/port_houston_integration.py
```

This patch assumes the prior Dispatch / Active Status refactor patch has already been copied in.

## Validation performed

The patch was syntax checked with:

```powershell
python -m compileall -q app.py admin_pages.py ai_agents ai_core api pages_app repositories services ui_components utils config.py db_client.py email_ingest.py operations_ai.py smartsheet_client.py validators.py
```

## Expected result

`app.py` should drop from about 3,592 lines to about 2,506 lines.

## Next recommended cleanup

After this runs clean, the next safest extraction is Dashboard / KPI / Calendar UI:

```text
pages_app/dashboard.py
pages_app/calendar_view.py
services/dashboard_service.py
ui_components/load_card.py
```
