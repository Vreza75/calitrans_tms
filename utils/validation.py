from __future__ import annotations

import pandas as pd


def validate_dispatch_rows(df: pd.DataFrame) -> pd.DataFrame:
    # Return rows that need dispatcher attention.
    issues = []

    for _, row in df.iterrows():
        row_issues = []

        if not row.get("Status"):
            row_issues.append("Missing status")
        if row.get("Status") in ["Assigned", "Dispatched", "In Transit"] and not row.get("Driver"):
            row_issues.append("Driver required for assigned/dispatched load")
        if row.get("Ready for ProfitTools") and not row.get("Load #"):
            row_issues.append("Load # required before ProfitTools export")

        if row_issues:
            issues.append({
                "_row_id": row.get("_row_id"),
                "Load #": row.get("Load #"),
                "Customer": row.get("Customer"),
                "Issues": "; ".join(row_issues),
            })

    return pd.DataFrame(issues)
