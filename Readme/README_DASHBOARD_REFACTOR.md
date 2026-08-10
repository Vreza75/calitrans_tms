# Calitrans Dashboard / Calendar Refactor Patch

Date: 2026-07-03

## Purpose

This patch continues the App.py cleanup after the Dispatch / Active Status and Port Houston refactors.

It moves the Dashboard, Calendar View, and Status Legend rendering out of `app.py`.

## Files included

Copy these files into the matching paths in your project:

```text
app.py
pages_app/dashboard.py
pages_app/calendar_view.py
ui_components/status_legend.py
```

## What changed

- `render_dashboard()` moved from `app.py` to `pages_app/dashboard.py`.
- `render_calendar_view()` moved from `app.py` to `pages_app/calendar_view.py`.
- Status legend sidebar rendering moved from `app.py` to `ui_components/status_legend.py`.
- `app.py` now imports those page/component renderers and routes to them.

## Expected result

`app.py` is reduced from about 2,506 lines to about 1,950 lines.

## Test command

Run from the project root:

```powershell
streamlit run app.py
```

Optional compile check:

```powershell
python -m compileall -q app.py admin_pages.py ai_agents ai_core api pages_app repositories services ui_components utils config.py db_client.py email_ingest.py operations_ai.py smartsheet_client.py validators.py
```

## Next recommended refactor

The next best target is `Orders/Load Management`, including Booking Review and order editing helpers.
Suggested modules:

```text
pages_app/orders_management.py
services/booking_verification_service.py
services/load_update_service.py
```
