from __future__ import annotations

import pandas as pd

from application.drivers.models import ActiveDriver
from services.driver_roster_service import list_active_drivers as _list_active_drivers_df


def _clean_str(value: object) -> str:
    """A null roster column can arrive as None or as pandas' float NaN
    (e.g. an all-null column loses its string dtype) - `value or ""`
    alone doesn't catch NaN, since NaN is truthy, and str(nan) is the
    literal text "nan". pd.isna() catches both uniformly."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def list_active_drivers() -> list[ActiveDriver]:
    """Active roster drivers for the Dispatch Board's assignment dropdown
    (Phase 10B) - thin wrapper around services.driver_roster_service.
    list_active_drivers(), converting its DataFrame into the application
    layer's own DTO rather than exposing pandas rows past this boundary
    (same pattern as application/loads/queries.py). Read-only: no
    permission check at this boundary, same as list_loads/search_loads -
    the API layer's require_role(*READ_LOADS) is the enforcement point
    for this query, matching every other load-visibility read."""
    df = _list_active_drivers_df()
    return [
        ActiveDriver(
            driver_name=_clean_str(row.get("driver_name")),
            phone=_clean_str(row.get("phone")),
            truck_number=_clean_str(row.get("truck_number")),
        )
        for _, row in df.iterrows()
    ]
