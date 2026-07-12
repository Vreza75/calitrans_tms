"""
Compatibility wrapper for imports that expect database.db_client.

The main database client lives at project root in db_client.py.
"""

from db_client import DispatchDatabaseClient, execute, read_df

__all__ = ["DispatchDatabaseClient", "execute", "read_df"]