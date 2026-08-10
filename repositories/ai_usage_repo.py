# repositories/ai_usage_repo.py

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from db_client import read_df, require_schema_ready

# ai_usage_log (database/ai_usage_log_migration.sql, written by
# ai_core/usage_logger.py::log_ai_usage() on every real LLM call) is the
# single canonical AI-usage table. This module previously also defined a
# similarly-shaped but distinct `ai_usage` table with its own
# log_ai_usage_record() writer that had zero callers anywhere in the
# repository - nothing ever wrote to it, so this dashboard always showed
# "no usage records found" regardless of real AI activity. Repointed to
# the real table instead of maintaining two schemas; `ai_usage`'s extra
# columns (intake_id, case_id, load_id, customer, booking_number,
# conversation_key, cache_hit) were never populated by anything and are
# dropped rather than carried forward as always-empty columns.


def _ensure_ai_usage_log_ready() -> None:
    require_schema_ready("ai_usage_log", "task", migration_hint="database/ai_usage_log_migration.sql")


def load_ai_usage_summary(start_date: date | None = None, end_date: date | None = None) -> dict[str, Any]:
    _ensure_ai_usage_log_ready()
    params = {"start_date": start_date, "end_date": end_date}
    where = "where (:start_date is null or date(created_at) >= :start_date) and (:end_date is null or date(created_at) <= :end_date)"

    df = read_df(
        f"""
        select
            count(*) as calls,
            coalesce(sum(input_tokens), 0) as input_tokens,
            coalesce(sum(output_tokens), 0) as output_tokens,
            coalesce(sum(total_tokens), 0) as total_tokens,
            coalesce(sum(estimated_cost_usd), 0) as estimated_cost,
            coalesce(avg(latency_seconds), 0) as avg_latency_seconds,
            coalesce(sum(case when ok then 1 else 0 end), 0) as successful_calls
        from ai_usage_log
        {where}
        """,
        params,
    )

    if df.empty:
        return {}

    row = df.iloc[0].to_dict()
    return {
        "calls": int(row.get("calls", 0) or 0),
        "input_tokens": int(row.get("input_tokens", 0) or 0),
        "output_tokens": int(row.get("output_tokens", 0) or 0),
        "total_tokens": int(row.get("total_tokens", 0) or 0),
        "estimated_cost": float(row.get("estimated_cost", 0) or 0),
        "avg_latency_seconds": float(row.get("avg_latency_seconds", 0) or 0),
        "successful_calls": int(row.get("successful_calls", 0) or 0),
    }


def load_ai_usage_by_agent(start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    _ensure_ai_usage_log_ready()
    df = read_df(
        """
        select
            coalesce(agent_name, task, 'unknown') as agent_name,
            coalesce(model, '') as model,
            count(*) as calls,
            coalesce(sum(total_tokens), 0) as total_tokens,
            coalesce(sum(input_tokens), 0) as input_tokens,
            coalesce(sum(output_tokens), 0) as output_tokens,
            coalesce(sum(estimated_cost_usd), 0) as estimated_cost,
            coalesce(avg(latency_seconds), 0) as avg_latency_seconds,
            coalesce(sum(case when ok then 1 else 0 end), 0) as successful_calls,
            coalesce(sum(case when not ok then 1 else 0 end), 0) as failed_calls
        from ai_usage_log
        where (:start_date is null or date(created_at) >= :start_date)
          and (:end_date is null or date(created_at) <= :end_date)
        group by coalesce(agent_name, task, 'unknown'), coalesce(model, '')
        order by estimated_cost desc, calls desc
        """,
        {"start_date": start_date, "end_date": end_date},
    )
    # Rounded in Python, not SQL - `round(x::numeric, n)` is PostgreSQL-only
    # syntax with no portable equivalent, and this is purely a display
    # concern, not something that needs to happen in the database.
    if not df.empty:
        df["avg_latency_seconds"] = df["avg_latency_seconds"].astype(float).round(3)
    return df


def load_ai_usage_recent(limit: int = 100) -> pd.DataFrame:
    _ensure_ai_usage_log_ready()
    return read_df(
        """
        select
            created_at,
            task,
            agent_name,
            model,
            ok,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_usd as estimated_cost,
            latency_seconds,
            error
        from ai_usage_log
        order by created_at desc
        limit :limit
        """,
        {"limit": int(limit)},
    )
