import pandas as pd

from services.load_grouping_service import group_loads_by_booking


def _row(row_id, booking, parent_booking_key, status="New", customer="Acme"):
    return {
        "_row_id": row_id,
        "Booking Number": booking,
        "parent_booking_key": parent_booking_key,
        "Status": status,
        "Customer": customer,
    }


def test_four_containers_same_booking_collapse_to_one_row():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800"),
        _row(2, "RICGX1235800", "RICGX1235800"),
        _row(3, "RICGX1235800", "RICGX1235800"),
        _row(4, "RICGX1235800", "RICGX1235800"),
    ])
    result = group_loads_by_booking(df)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "4 containers"
    assert sorted(result.iloc[0]["_grouped_row_ids"]) == [1, 2, 3, 4]
    assert result.iloc[0]["Customer"] == "Acme"


def test_single_container_booking_passes_through_unchanged():
    df = pd.DataFrame([_row(1, "ABC123", "")])
    result = group_loads_by_booking(df)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == ""
    assert result.iloc[0]["_grouped_row_ids"] == [1]


def test_rows_with_no_parent_booking_key_are_never_grouped_together():
    df = pd.DataFrame([
        _row(1, "ABC123", ""),
        _row(2, "DEF456", ""),
    ])
    result = group_loads_by_booking(df)
    assert len(result) == 2


def test_mixed_status_group_does_not_collapse_when_require_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="Dispatched"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=True)
    assert len(result) == 2
    assert set(result["_row_id"]) == {1, 2}


def test_mixed_status_group_collapses_when_not_requiring_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="Dispatched"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=False)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "2 containers"


def test_same_status_group_collapses_when_require_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="New"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=True)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "2 containers"


def test_empty_dataframe_returns_empty_dataframe():
    df = pd.DataFrame(columns=["_row_id", "Booking Number", "parent_booking_key", "Status", "Customer"])
    result = group_loads_by_booking(df)
    assert result.empty
