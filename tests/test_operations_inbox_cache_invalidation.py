from __future__ import annotations

import streamlit as st

import services.operations_inbox_service as ops


def test_refresh_data_clears_only_the_four_operations_inbox_caches(monkeypatch) -> None:
    cleared = []

    def _tracker(name):
        def _clear():
            cleared.append(name)

        return _clear

    monkeypatch.setattr(ops.load_operations_inbox_df, "clear", _tracker("load_operations_inbox_df"))
    monkeypatch.setattr(ops.load_operations_inbox_record, "clear", _tracker("load_operations_inbox_record"))
    monkeypatch.setattr(
        ops.load_operations_conversation_summary_df, "clear", _tracker("load_operations_conversation_summary_df")
    )
    monkeypatch.setattr(
        ops.load_operations_conversation_timeline, "clear", _tracker("load_operations_conversation_timeline")
    )

    def _global_clear_should_not_be_called():
        raise AssertionError("refresh_data() must not call the global st.cache_data.clear()")

    monkeypatch.setattr(st.cache_data, "clear", _global_clear_should_not_be_called)

    ops.refresh_data()

    assert set(cleared) == {
        "load_operations_inbox_df",
        "load_operations_inbox_record",
        "load_operations_conversation_summary_df",
        "load_operations_conversation_timeline",
    }


def test_no_remaining_global_cache_clear_calls_in_operations_inbox_service() -> None:
    """Every st.cache_data.clear() call site in this file should have been
    routed through refresh_data() during the Phase 1 cache-targeting
    cleanup - the only literal occurrence left should be the one inside
    refresh_data()'s own docstring/comment, not a live call."""
    from pathlib import Path

    source = Path(ops.__file__).read_text(encoding="utf-8")
    live_calls = [line for line in source.splitlines() if line.strip() == "st.cache_data.clear()"]
    assert live_calls == []
