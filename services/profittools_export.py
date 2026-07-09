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
        ready = df[df["Ready for ProfitTools"] == True].copy()
    else:
        ready = df.iloc[0:0].copy()

    available_columns = [col for col in PROFITTOOLS_COLUMNS if col in ready.columns]
    export_df = ready[available_columns]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(path, index=False)

    return str(path)
