# Calitrans TMS - PortPro-Style Workflow Update

This update changes the app from a simple tab-based Smartsheet replacement into a more complete drayage TMS workflow.

## New navigation

- Dashboard
- Orders
- Dispatch Board
- Containers
- PDF Intake
- Documents
- Billing / ProfitTools
- Validation
- Master Data

## Important database update

Run this SQL file in Supabase SQL Editor:

```text
database/portpro_style_migration.sql
```

Run it after your original `database/schema.sql`.

## How to install

1. Copy the updated files into your current project.
2. Run the migration SQL in Supabase.
3. Restart Streamlit:

```powershell
Ctrl+C
py -m streamlit run app.py
```

## Notes

This is a Calitrans-owned PortPro-style workflow, not a copy of PortPro's proprietary software or interface.
