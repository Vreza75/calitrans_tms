import pandas as pd

from services.dispatch_workflow_service import build_search_blob


def test_none_dataframe_returns_empty_series():
    result = build_search_blob(None, ["Customer"])
    assert isinstance(result, pd.Series)
    assert result.empty


def test_empty_dataframe_returns_series_not_dataframe():
    # This is the exact shape that crashed the Dispatch Board: an empty
    # completed_df with valid searchable columns caused pandas' .agg(join)
    # to return a DataFrame instead of a Series, so .str.lower() raised
    # AttributeError: 'DataFrame' object has no attribute 'str'.
    df = pd.DataFrame(columns=["Booking Number", "Customer", "Driver Name"])
    result = build_search_blob(df, ["Booking Number", "Customer", "Driver Name"])
    assert isinstance(result, pd.Series)
    assert result.empty
    assert list(result.index) == list(df.index)


def test_no_requested_columns_returns_blank_per_row():
    df = pd.DataFrame({"Booking Number": ["MAEU1001001"]})
    result = build_search_blob(df, [])
    assert list(result) == [""]


def test_requested_columns_missing_returns_blank_per_row():
    df = pd.DataFrame({"Booking Number": ["MAEU1001001"]})
    result = build_search_blob(df, ["Customer", "Driver Name"])
    assert list(result) == [""]


def test_one_valid_column():
    df = pd.DataFrame({"Customer": ["Colombia Fresh Imports"]})
    result = build_search_blob(df, ["Customer"])
    assert result.iloc[0] == "colombia fresh imports"


def test_multiple_valid_columns_joined():
    df = pd.DataFrame({
        "Booking Number": ["MAEU1001001"],
        "Customer": ["Colombia Fresh Imports"],
        "Driver Name": ["Victor Reza"],
    })
    result = build_search_blob(df, ["Booking Number", "Customer", "Driver Name"])
    assert result.iloc[0] == "maeu1001001 colombia fresh imports victor reza"


def test_null_values_become_empty_strings():
    df = pd.DataFrame({"Customer": [None], "Driver Name": ["Victor Reza"]})
    result = build_search_blob(df, ["Customer", "Driver Name"])
    assert result.iloc[0] == "victor reza"


def test_numeric_and_date_values_stringified():
    df = pd.DataFrame({
        "Booking Number": [1001001],
        "Delivery Date": [pd.Timestamp("2026-07-15")],
    })
    result = build_search_blob(df, ["Booking Number", "Delivery Date"])
    assert "1001001" in result.iloc[0]
    assert "2026-07-15" in result.iloc[0]


def test_duplicate_requested_columns_deduped():
    df = pd.DataFrame({"Customer": ["Colombia Fresh Imports"]})
    result = build_search_blob(df, ["Customer", "Customer"])
    assert result.iloc[0] == "colombia fresh imports"


def test_duplicate_dataframe_column_labels_do_not_crash():
    df = pd.DataFrame([["a", "b"]], columns=["Customer", "Customer"])
    result = build_search_blob(df, ["Customer"])
    assert isinstance(result, pd.Series)
    assert result.iloc[0]


def test_special_search_characters_are_literal_via_contains():
    df = pd.DataFrame({"Container Number": ["ABC[123]"]})
    blob = build_search_blob(df, ["Container Number"])
    assert blob.str.contains("abc[123]", regex=False, na=False).iloc[0]


def test_case_insensitive_matching():
    df = pd.DataFrame({"Driver Name": ["Victor Reza"]})
    blob = build_search_blob(df, ["Driver Name"])
    assert blob.str.contains("VICTOR REZA".casefold(), regex=False, na=False).iloc[0]


def test_search_query_whitespace_is_trimmed_per_field():
    df = pd.DataFrame({"Customer": ["  Colombia Fresh  "]})
    result = build_search_blob(df, ["Customer"])
    assert result.iloc[0] == "colombia fresh"


def test_active_dataframe_with_results():
    df = pd.DataFrame({"Driver Name": ["Victor Reza", "Someone Else"]})
    blob = build_search_blob(df, ["Driver Name"])
    matches = df[blob.str.contains("victor reza", regex=False, na=False)]
    assert len(matches) == 1


def test_completed_dataframe_with_no_results_does_not_crash():
    df = pd.DataFrame({"Driver Name": ["Someone Else"]})
    blob = build_search_blob(df, ["Driver Name"])
    matches = df[blob.str.contains("victor reza", regex=False, na=False)]
    assert matches.empty


def test_completed_dataframe_with_a_result():
    df = pd.DataFrame({"Driver Name": ["Victor Reza"], "Booking Number": ["MAEU1001001"]})
    blob = build_search_blob(df, ["Driver Name", "Booking Number"])
    matches = df[blob.str.contains("maeu1001001", regex=False, na=False)]
    assert len(matches) == 1
