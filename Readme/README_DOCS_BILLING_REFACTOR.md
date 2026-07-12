# Calitrans Docs / Billing / Booking Detail Refactor Patch

Date: 2026-07-03

This patch continues the `app.py` cleanup after the Orders / Load Management refactor.

## What moved out of `app.py`

The following sections were extracted into page modules:

- Billing / ProfitTools
- Documents
- Email Imports
- Booking Detail query-param workspace
- Legacy PDF intake helper

## New files

```text
pages_app/billing_profittools.py
pages_app/documents.py
pages_app/booking_detail.py
pages_app/email_imports.py
```

## Updated files

```text
app.py
```

This patch is cumulative and also includes the previous refactor files needed by the current app navigation:

```text
pages_app/active_status.py
pages_app/calendar_view.py
pages_app/dashboard.py
pages_app/dispatch_board.py
pages_app/orders_management.py
pages_app/port_houston_integration.py
services/customer_status_email_service.py
services/dispatch_data_service.py
services/dispatch_workflow_service.py
services/operations_attachment_service.py
services/operations_case_service.py
ui_components/status_legend.py
```

## Result

`app.py` was reduced from about 1,075 lines to about 554 lines.

The remaining app file now mainly handles:

- environment loading
- global styling
- data loading and normalization
- sidebar navigation
- route dispatching

## Validation

Compile check used:

```powershell
python -m compileall -q app.py admin_pages.py ai_agents ai_core api pages_app repositories services ui_components utils config.py db_client.py email_ingest.py operations_ai.py smartsheet_client.py validators.py
```

## Copy instructions

Copy the contents of this patch into your project root and overwrite existing files when prompted.

Then run:

```powershell
streamlit run app.py
```
