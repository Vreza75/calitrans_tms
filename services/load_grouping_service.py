from __future__ import annotations

import pandas as pd


def group_loads_by_booking(
    df: pd.DataFrame,
    *,
    require_same_status: bool = False,
) -> pd.DataFrame:
    """Collapse rows sharing a non-null parent_booking_key into one summary
    row per booking.

    Adds two columns to the result:
      - "Containers": "N containers" when a group has more than one row,
        "" for an ungrouped (single-row) booking.
      - "_grouped_row_ids": list[int] of the _row_id values folded into that
        summary row.

    Rows with an empty/missing parent_booking_key are never grouped with
    each other, even if several happen to share that same empty value —
    each such row is its own group of one.

    If require_same_status is True, a group only collapses when every row
    in it shares the same "Status" value; otherwise its rows are returned
    individually, exactly as if parent_booking_key were empty for them.
    """
    if df.empty:
        result = df.copy()
        result["Containers"] = pd.Series(dtype="object")
        result["_grouped_row_ids"] = pd.Series(dtype="object")
        return result

    working = df.copy()
    working["_parent_booking_key_clean"] = working.get(
        "parent_booking_key", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip()

    summary_rows = []

    group_key = working["_parent_booking_key_clean"].where(
        working["_parent_booking_key_clean"] != "", other=working.index.astype(str)
    )

    for _, group in working.groupby(group_key):
        if len(group) > 1 and require_same_status:
            statuses = group.get("Status", pd.Series(dtype="object")).astype(str).str.strip()
            if statuses.nunique() > 1:
                for _, row in group.iterrows():
                    single = row.drop(labels=["_parent_booking_key_clean"]).to_dict()
                    single["Containers"] = ""
                    single["_grouped_row_ids"] = [int(row["_row_id"])]
                    summary_rows.append(single)
                continue

        first = group.iloc[0].drop(labels=["_parent_booking_key_clean"]).to_dict()
        row_ids = [int(value) for value in group["_row_id"].tolist()]
        first["Containers"] = f"{len(group)} containers" if len(group) > 1 else ""
        first["_grouped_row_ids"] = row_ids
        summary_rows.append(first)

    return pd.DataFrame(summary_rows).reset_index(drop=True)
