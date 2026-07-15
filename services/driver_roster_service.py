from __future__ import annotations

import pandas as pd

from db_client import read_df


def find_driver_in_roster(drivers_df: pd.DataFrame | None, driver_name: str) -> dict | None:
    """Case-insensitive lookup of a driver's roster record by name.

    Pure function — takes the roster DataFrame as an argument so it can be
    unit tested without a database. Returns the first matching row as a
    dict, or None if the roster is empty, the name is blank, or no row
    matches.
    """
    name = str(driver_name or "").strip()
    if not name or drivers_df is None or drivers_df.empty:
        return None

    if "driver_name" not in drivers_df.columns:
        return None

    matches = drivers_df[
        drivers_df["driver_name"].astype(str).str.strip().str.casefold() == name.casefold()
    ]
    if matches.empty:
        return None

    return matches.iloc[0].to_dict()


def list_active_drivers() -> pd.DataFrame:
    """Active roster drivers available for Ready to Dispatch assignment.

    Thin I/O wrapper around the `drivers` table — not unit tested directly
    (no test database in this environment); verified with a manual
    read-only smoke check (Task 2, Step 2).
    """
    return read_df(
        "select driver_name, phone, truck_number from drivers "
        "where status = 'Active' order by driver_name"
    )
