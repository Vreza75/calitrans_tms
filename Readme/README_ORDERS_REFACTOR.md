# Calitrans Orders / Load Management Refactor

This patch continues the App.py cleanup after the Dashboard / Calendar refactor.

## Main change

Moved Orders / Load Management and Booking Review logic out of `app.py` into:

- `pages_app/orders_management.py`

The main Streamlit router in `app.py` now imports and calls:

```python
from pages_app.orders_management import render_orders_management
```

## What moved out of app.py

- Booking verification required-field checks
- Booking readiness scoring
- Booking review tables
- Booking final review actions
- Order detail editor
- Orders / Load Management page
- Order queue filters for New, Missing Info, Booking Verified, and Cancelled

## Files included

This patch includes the updated `app.py`, the new Orders page, and the refactor modules from the previous patches that `app.py` now depends on. Copy the full patch contents into the project root.

## Validation performed

The patch was compile checked with:

```powershell
python -m compileall -q app.py admin_pages.py ai_agents ai_core api pages_app repositories services ui_components utils config.py db_client.py email_ingest.py operations_ai.py smartsheet_client.py validators.py
```

## Expected result

`app.py` is reduced from about 1,950 lines to about 1,075 lines.

Next recommended cleanup after this patch runs clean:

- Documents page
- Email Imports page
- Billing / ProfitTools page
- Booking Detail page
