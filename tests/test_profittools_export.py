import pandas as pd

from services.profittools_export import export_ready_loads


def test_export_ready_loads_filters_by_status_column(tmp_path):
    df = pd.DataFrame(
        [
            {"Load #": "LD-1", "Customer": "Acme", "Status": "Ready for ProfitTools"},
            {"Load #": "LD-2", "Customer": "Beta", "Status": "In Transit"},
        ]
    )
    output_path = tmp_path / "export.csv"

    result_path = export_ready_loads(df, output_path=str(output_path))

    written = pd.read_csv(result_path)
    assert list(written["Load #"]) == ["LD-1"]
    assert list(written.columns) == ["Load #", "Customer"]


def test_export_ready_loads_falls_back_to_boolean_column(tmp_path):
    df = pd.DataFrame(
        [
            {"Load #": "LD-1", "Customer": "Acme", "Ready for ProfitTools": True},
            {"Load #": "LD-2", "Customer": "Beta", "Ready for ProfitTools": False},
        ]
    )
    output_path = tmp_path / "export.csv"

    result_path = export_ready_loads(df, output_path=str(output_path))

    written = pd.read_csv(result_path)
    assert list(written["Load #"]) == ["LD-1"]


def test_export_ready_loads_writes_empty_file_when_no_status_columns_present(tmp_path):
    df = pd.DataFrame([{"Load #": "1", "Customer": "Acme"}])
    output_path = tmp_path / "export.csv"

    result_path = export_ready_loads(df, output_path=str(output_path))

    written = pd.read_csv(result_path)
    assert written.empty


def test_export_ready_loads_only_includes_known_profittools_columns(tmp_path):
    df = pd.DataFrame(
        [
            {
                "Load #": "LD-1",
                "Status": "Ready for ProfitTools",
                "Customer": "Acme",
                "Some Internal Field": "hidden",
            }
        ]
    )
    output_path = tmp_path / "export.csv"

    result_path = export_ready_loads(df, output_path=str(output_path))

    written = pd.read_csv(result_path)
    assert "Some Internal Field" not in written.columns
