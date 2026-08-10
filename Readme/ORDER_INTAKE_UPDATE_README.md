# Calitrans TMS - Order Intake / Action Queue Update

This update moves PDF and email order intake into the **Orders** section.

## New Orders workflow

Inside **Orders**, you now have:

1. **Active Orders**
   - Current loads
   - Manual load creation
   - Search/filter existing loads

2. **Order Intake / Action Queue**
   - Upload order PDFs
   - Pull recent email orders/PDFs
   - Parse order details
   - Hold items in a review queue
   - Mark items as needing action
   - Create an active load only after review

## Why this is better

Instead of every uploaded PDF or email becoming a load immediately, the system now creates an intake record first.

Recommended flow:

```text
Email/PDF received
→ Order Intake
→ Needs Review
→ Dispatcher reviews parsed data
→ Create Load / Needs Info / Duplicate / Reject
→ Active Orders
→ Dispatch Board
```

## Required database step

Run this file in Supabase SQL Editor:

```text
database/order_intake_migration.sql
```

## Files added/changed

- `app.py`
- `order_intake.py`
- `database/order_intake_migration.sql`
- `ORDER_INTAKE_UPDATE_README.md`

## Restart

```powershell
Ctrl+C
py -m streamlit run app.py
```
