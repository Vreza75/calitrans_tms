from __future__ import annotations

import sys
import pandas as pd
from db_client import DispatchDatabaseClient


def main(csv_path: str) -> None:
    df = pd.read_csv(csv_path)
    client = DispatchDatabaseClient()

    for _, row in df.iterrows():
        client.add_row(row.dropna().to_dict())

    print(f"Imported {len(df)} loads.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python database/import_sample_csv.py path/to/loads.csv")
    main(sys.argv[1])
