import pandas as pd

from services.driver_roster_service import find_driver_in_roster


def _roster(rows):
    return pd.DataFrame(rows)


def test_match_found_returns_dict():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "Victor Reza")
    assert result == {
        "driver_name": "Victor Reza",
        "phone": "555-1234",
        "truck_number": "T-12",
        "status": "Active",
    }


def test_case_insensitive_match():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "victor reza")
    assert result["driver_name"] == "Victor Reza"


def test_no_match_returns_none():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    assert find_driver_in_roster(roster, "Someone Else") is None


def test_empty_roster_returns_none():
    assert find_driver_in_roster(_roster([]), "Victor Reza") is None
    typed_empty = pd.DataFrame(columns=["driver_name", "phone", "truck_number", "status"])
    assert find_driver_in_roster(typed_empty, "Victor Reza") is None


def test_multiple_rows_only_one_matches():
    roster = _roster([
        {"driver_name": "Alice Driver", "phone": "111", "truck_number": "T-1", "status": "Active"},
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
        {"driver_name": "Bob Driver", "phone": "222", "truck_number": "T-2", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "Victor Reza")
    assert result["truck_number"] == "T-12"


def test_blank_or_none_driver_name_returns_none():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    assert find_driver_in_roster(roster, "") is None
    assert find_driver_in_roster(roster, None) is None


def test_none_dataframe_returns_none():
    assert find_driver_in_roster(None, "Victor Reza") is None
