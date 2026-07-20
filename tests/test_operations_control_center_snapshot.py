"""_render_selected_operations_work_item runs in a separate top-level
function (opened inside an st.dialog), not inside render_operations_inbox
where inbox_df lives - it must never reference inbox_df directly. These
pure helpers compute the small counts the Control Center Snapshot panel
needs while inbox_df is still in scope in the parent page, so they can be
passed through as plain dicts/ints instead.
"""
import pandas as pd

from pages_app.operations_inbox import (
    build_control_level_counts,
    build_dispatcher_queue_counts,
    count_critical_priority,
)


def test_build_control_level_counts_from_valid_dataframe():
    df = pd.DataFrame({"control_level": ["Level 1 - Operational Cases", "Level 1 - Operational Cases", "Needs Review"]})
    assert build_control_level_counts(df) == {"Level 1 - Operational Cases": 2, "Needs Review": 1}


def test_build_control_level_counts_missing_column_returns_empty_dict():
    df = pd.DataFrame({"other_column": [1, 2, 3]})
    assert build_control_level_counts(df) == {}


def test_build_control_level_counts_none_dataframe_returns_empty_dict():
    assert build_control_level_counts(None) == {}


def test_build_control_level_counts_ignores_null_values():
    df = pd.DataFrame({"control_level": ["Needs Review", None, "Needs Review"]})
    assert build_control_level_counts(df) == {"Needs Review": 2}


def test_build_dispatcher_queue_counts_from_valid_dataframe():
    df = pd.DataFrame({"dispatcher_queue": ["New Orders", "Billing", "New Orders"]})
    assert build_dispatcher_queue_counts(df) == {"New Orders": 2, "Billing": 1}


def test_build_dispatcher_queue_counts_missing_column_returns_empty_dict():
    assert build_dispatcher_queue_counts(pd.DataFrame({"x": [1]})) == {}


def test_count_critical_priority_counts_matching_rows():
    df = pd.DataFrame({"priority_label": ["Critical", "Normal", "Critical"]})
    assert count_critical_priority(df) == 2


def test_count_critical_priority_missing_column_returns_zero():
    assert count_critical_priority(pd.DataFrame({"x": [1]})) == 0


def test_count_critical_priority_none_dataframe_returns_zero():
    assert count_critical_priority(None) == 0
