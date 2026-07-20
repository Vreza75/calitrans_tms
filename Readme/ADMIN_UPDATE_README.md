# Add Customer, Driver, Carrier, and Warehouse Admin Screens

This update adds a new Streamlit tab:

```text
⚙️ Master Data
```

Inside that tab you can manage:

- Customers
- Warehouses and addresses
- Carriers
- Drivers

## Files changed

- `app.py`
- `admin_pages.py`

## How to use

1. Replace your current `app.py` with the included updated `app.py`.
2. Add `admin_pages.py` to the same folder as `app.py`.
3. Restart Streamlit:

```powershell
Ctrl+C
py -m streamlit run app.py
```

## Note

This uses the same Supabase/PostgreSQL tables you already created with `database/schema.sql`.
