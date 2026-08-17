from __future__ import annotations

import pandas as pd

import services.operations_inbox_service as operations_inbox_service


def test_returns_empty_dataframe_when_no_strong_key_provided() -> None:
    result = operations_inbox_service.load_operations_business_conversation_timeline(
        conversation_key="contact",
        booking_number="",
        container_number="",
        reference_number="",
    )

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_query_applies_the_operations_email_source_filter(monkeypatch) -> None:
    """Regression test - a duplicate definition of this function once
    shadowed the canonical one and dropped this filter, which would let
    non-operations-email order_intake rows leak into a booking's
    conversation timeline (the exact "broad unrelated histories" the
    docstring and .claude/rules/operations-inbox.md say to avoid)."""

    captured = {}

    def fake_read_df(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return pd.DataFrame(columns=[
            "id", "source_received_at", "created_at", "email_direction",
            "conversation_status", "review_status", "source_sender",
            "source_subject", "raw_text", "message_preview", "conversation_key",
        ])

    monkeypatch.setattr(operations_inbox_service, "read_df", fake_read_df)

    operations_inbox_service.load_operations_business_conversation_timeline(
        booking_number="BKG123456",
        limit=50,
    )

    assert "sql" in captured, "read_df should be called when a strong key is present"
    assert operations_inbox_service.operations_email_source_filter() in captured["sql"]

    select_list = captured["sql"].split("from order_intake", 1)[0]
    assert "parsed_data" not in select_list, (
        "timeline is preview-only by design - full parsed_data belongs to "
        "load_operations_intake_message(), not the multi-row timeline "
        "(parsed_data::text is expected in the WHERE search clause, just "
        "not as a selected column)"
    )
    assert "message_preview" in select_list


def test_query_failure_returns_empty_dataframe_instead_of_raising(monkeypatch) -> None:
    def failing_read_df(sql, params):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(operations_inbox_service, "read_df", failing_read_df)

    result = operations_inbox_service.load_operations_business_conversation_timeline(
        booking_number="BKG123456",
    )

    assert isinstance(result, pd.DataFrame)
    assert result.empty
