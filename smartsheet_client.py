"""
Compatibility wrapper.

The old app imported DispatchSmartsheetClient. The project now uses PostgreSQL/Supabase
as the system of record, so this module redirects that old name to DispatchDatabaseClient.
"""

from db_client import DispatchDatabaseClient as DispatchSmartsheetClient

SMARTSHEET_CUSTOMER_SHEET_ID = None
SMARTSHEET_DRIVER_SHEET_ID = None
SMARTSHEET_WAREHOUSE_SHEET_ID = None
