from __future__ import annotations

from repositories import work_item_repo as repo


def test_normalize_sort_rejects_unknown_column_and_falls_back_to_default() -> None:
    sort_by, direction = repo.normalize_sort("not_a_real_column", "asc")
    assert sort_by == repo.DEFAULT_SORT_BY
    assert direction == "asc"


def test_normalize_sort_rejects_unknown_direction_and_falls_back_to_default() -> None:
    sort_by, direction = repo.normalize_sort("customer", "sideways")
    assert sort_by == "customer"
    assert direction == repo.DEFAULT_SORT_DIRECTION


def test_normalize_sort_accepts_known_column_and_direction() -> None:
    sort_by, direction = repo.normalize_sort("confidence", "asc")
    assert sort_by == "confidence"
    assert direction == "asc"


def test_normalize_page_size_rejects_arbitrary_values() -> None:
    assert repo.normalize_page_size(17) == repo.DEFAULT_PAGE_SIZE
    assert repo.normalize_page_size(None) == repo.DEFAULT_PAGE_SIZE


def test_normalize_page_size_accepts_allowed_values() -> None:
    for size in repo.ALLOWED_PAGE_SIZES:
        assert repo.normalize_page_size(size) == size


def test_build_work_item_filters_binds_every_value_never_interpolates() -> None:
    where_sql, params = repo.build_work_item_filters(
        queue="New Orders",
        customer="O'Brien Logistics",
        search="RICGX1235800",
        only_open=False,
    )
    assert "O'Brien" not in where_sql
    assert params["customer"] == "%O'Brien Logistics%"
    assert params["queue"] == "New Orders"
    assert params["search"] == "%RICGX1235800%"
    assert ":customer" in where_sql
    assert ":queue" in where_sql
    assert ":search" in where_sql


def test_build_work_item_filters_with_no_filters_is_permissive_true() -> None:
    where_sql, params = repo.build_work_item_filters(only_open=False)
    assert where_sql == "true"
    assert params == {}


def test_build_work_item_filters_only_open_uses_the_shared_inbox_repo_clause() -> None:
    from repositories.inbox_repo import inbox_review_where_clause

    where_sql, _ = repo.build_work_item_filters(only_open=True)
    expected_fragment = inbox_review_where_clause()[len("where "):]
    assert expected_fragment in where_sql


def test_sortable_columns_are_whitelisted_sql_expressions_not_user_input() -> None:
    # Every value must be a fixed SQL expression referencing oi.<column> or
    # a parsed_data JSON path - never raw user input reflected back.
    for expression in work_item_repo_sortable_values():
        assert "oi." in expression or "parsed_data" in expression


def work_item_repo_sortable_values():
    return repo.SORTABLE_COLUMNS.values()


def test_draft_editable_columns_excludes_system_and_identity_columns() -> None:
    forbidden = {"id", "conversation_key", "created_at", "updated_at", "created_load_id"}
    assert forbidden.isdisjoint(repo.DRAFT_EDITABLE_COLUMNS)
