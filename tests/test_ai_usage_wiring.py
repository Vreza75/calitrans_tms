from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
import repositories.ai_usage_repo as ai_usage_repo
from ai_core.usage_logger import log_ai_usage
from db_client import SchemaNotReadyError


@pytest.fixture
def sqlite_ai_usage_db(monkeypatch, tmp_path):
    db_path = tmp_path / "ai_usage_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table ai_usage_log ("
                "id integer primary key autoincrement, created_at text, task text, agent_name text, "
                "model text, ok integer, input_tokens integer default 0, output_tokens integer default 0, "
                "total_tokens integer default 0, estimated_cost_usd real default 0, "
                "latency_seconds real default 0, error text, metadata text)"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_writer_writes_to_the_canonical_table(sqlite_ai_usage_db) -> None:
    log_ai_usage(task="test_task", agent_name="test_agent", model="gpt-4.1-mini", ok=True, input_tokens=10, output_tokens=5)

    df = db_client.read_df("select task, agent_name, model, ok from ai_usage_log")
    assert len(df) == 1
    assert df.iloc[0]["task"] == "test_task"
    assert df.iloc[0]["agent_name"] == "test_agent"


def test_reader_reads_from_the_same_canonical_table(sqlite_ai_usage_db) -> None:
    log_ai_usage(task="reader_task", agent_name="reader_agent", model="gpt-4.1", ok=True, input_tokens=100, output_tokens=50)

    summary = ai_usage_repo.load_ai_usage_summary()
    assert summary["calls"] == 1
    assert summary["input_tokens"] == 100
    assert summary["output_tokens"] == 50

    by_agent = ai_usage_repo.load_ai_usage_by_agent()
    assert len(by_agent) == 1
    assert by_agent.iloc[0]["agent_name"] == "reader_agent"

    recent = ai_usage_repo.load_ai_usage_recent()
    assert len(recent) == 1
    assert recent.iloc[0]["task"] == "reader_task"


def test_reader_and_writer_agree_on_no_ambiguity_across_multiple_writes(sqlite_ai_usage_db) -> None:
    log_ai_usage(task="a", agent_name="agent_a", model="gpt-4.1-mini", ok=True, input_tokens=1, output_tokens=1)
    log_ai_usage(task="b", agent_name="agent_b", model="gpt-4.1-mini", ok=False, input_tokens=2, output_tokens=2, error="boom")

    summary = ai_usage_repo.load_ai_usage_summary()
    assert summary["calls"] == 2
    assert summary["successful_calls"] == 1


def test_missing_schema_fails_cleanly_without_creating_a_table(monkeypatch) -> None:
    """Neither the writer nor the reader may fall back to runtime DDL when
    ai_usage_log doesn't exist yet - both must raise a clear error."""
    url = "sqlite:///:memory:"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with pytest.raises(SchemaNotReadyError):
        ai_usage_repo.load_ai_usage_summary()

    with pytest.raises(SchemaNotReadyError):
        log_ai_usage(task="x", agent_name="x", model="x", ok=True)

    db_client._ENGINE_CACHE.pop(url, None)
