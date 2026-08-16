from __future__ import annotations

from pathlib import Path
import pandas as pd


PROFITTOOLS_COLUMNS = [
    "Load #",
    "Booking Number",
    "Customer",
    "Warehouse",
    "Delivery Need Date",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Container Number",
    "Reference Number",
]


def export_ready_loads(
    df: pd.DataFrame,
    output_path: str = "exports/profittools_ready_loads.csv",
) -> str:
    if "Status" in df.columns:
        ready = df[df["Status"].astype(str).eq("Ready for ProfitTools")].copy()
    elif "Ready for ProfitTools" in df.columns:
        # Legacy/alternate-shape fallback for callers with no "Status"
        # column - the live caller (pages_app/billing_profittools.py)
        # always has "Status" and never reaches this branch. `== True`
        # is safe here because this column is only ever a clean bool
        # (see tests/test_profittools_export.py); a NaN would need a
        # caller that doesn't exist today, so not hardening for it.
        ready = df[df["Ready for ProfitTools"] == True].copy()
    else:
        ready = df.iloc[0:0].copy()

    available_columns = [col for col in PROFITTOOLS_COLUMNS if col in ready.columns]
    export_df = ready[available_columns]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(path, index=False)

    return str(path)
