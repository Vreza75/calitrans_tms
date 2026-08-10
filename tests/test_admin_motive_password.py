from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_SOURCE = (ROOT / "pages_app" / "admin.py").read_text(encoding="utf-8")


def test_motive_password_input_field_is_not_rendered() -> None:
    assert 'text_input("Motive Password"' not in ADMIN_SOURCE
    assert 'type="password"' not in ADMIN_SOURCE


def test_driver_save_does_not_write_motive_password() -> None:
    upsert_block = ADMIN_SOURCE[
        ADMIN_SOURCE.index("def _upsert_driver") : ADMIN_SOURCE.index("def render_customers_admin")
    ]
    sql_block = upsert_block[upsert_block.index('execute(\n        """') :]
    # The bound-parameter placeholder is the only way this SQL could write
    # a value - its absence proves no motive_password write happens,
    # independent of the explanatory comment above the function (which
    # legitimately mentions the column name).
    assert ":motive_password" not in sql_block
    assert "motive_password = " not in sql_block


def test_driver_form_submission_payload_does_not_include_motive_password() -> None:
    form_block = ADMIN_SOURCE[
        ADMIN_SOURCE.index("def render_drivers_admin") :
    ]
    submit_call = form_block[form_block.index("_upsert_driver(") :]
    submit_dict = submit_call[: submit_call.index(")\n") + 1]
    assert '"motive_password"' not in submit_dict


def test_motive_column_is_not_dropped_from_the_schema() -> None:
    """Phase 1 stops writing to the column but must not drop it (existing
    values need a manual rotation, not silent data loss)."""
    migrations_dir = ROOT / "database"
    drop_statements = []
    for path in migrations_dir.glob("*.sql"):
        text = path.read_text(encoding="utf-8").lower()
        if "drop column" in text and "motive_password" in text:
            drop_statements.append(path.name)
    assert drop_statements == []


def test_existing_motive_password_values_are_never_displayed() -> None:
    """The admin list view may show whether a legacy value exists (wrapped
    in coalesce(...) = '' as a Yes/No indicator), but must never select
    the raw column as a displayed value."""
    import re

    drivers_query_start = ADMIN_SOURCE.index("def render_drivers_admin")
    drivers_query_end = ADMIN_SOURCE.index("st.dataframe(drivers", drivers_query_start)
    query_block = ADMIN_SOURCE[drivers_query_start:drivers_query_end]

    for match in re.finditer(r".{0,20}motive_password", query_block):
        assert "coalesce(d." in match.group(0), f"raw motive_password reference: {match.group(0)!r}"
