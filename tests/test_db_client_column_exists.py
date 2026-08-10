"""column_exists() lets the ensure_*_schema() functions skip ~50 idempotent
DDL round trips (~26-45s total) once the schema is already applied, instead
of relying only on an st.session_state flag that doesn't reliably survive
across reruns/process restarts - that flag falling through was silently
eating the entire interactive sync time budget on every "cold" attempt.
"""
from db_client import column_exists


def test_known_column_reports_true():
    assert column_exists("order_intake", "id") is True


def test_unknown_column_reports_false():
    assert column_exists("order_intake", "definitely_not_a_real_column_xyz") is False


def test_unknown_table_reports_false():
    assert column_exists("definitely_not_a_real_table_xyz", "id") is False
