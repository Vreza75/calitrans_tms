# repositories/ai_usage_repo.py

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from db_client import execute, read_df


MODEL_PRICING_PER_1M_TOKENS = {
    # Update these values if your OpenAI model pricing changes.
    # These are placeholders for internal cost estimates only.
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def estimate_ai_cost(model: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    pricing = MODEL_PRICING_PER_1M_TOKENS.get(model, MODEL_PRICING_PER_1M_TOKENS.get("gpt-4.1-mini"))
    input_cost = (int(input_tokens or 0) / 1_000_000) * float(pricing.get("input", 0))
    output_cost = (int(output_tokens or 0) / 1_000_000) * float(pricing.get("output", 0))
    return round(input_cost + output_cost, 6)


def ensure_ai_usage_schema() -> None:
    execute(
        """
        create table if not exists ai_usage (
            id bigserial primary key,
            created_at timestamptz not null default now(),
            task text,
            agent_name text,
            model text,
            ok boolean not null default true,
            input_tokens integer not null default 0,
            output_tokens integer not null default 0,
            total_tokens integer not null default 0,
            estimated_cost numeric(12,6) not null default 0,
            latency_seconds numeric(10,3),
            intake_id bigint,
            case_id bigint,
            load_id bigint,
            customer text,
            booking_number text,
            conversation_key text,
            cache_hit boolean not null default false,
            error text,
            metadata jsonb not null default '{}'::jsonb
        )
        """
    )
    execute("create index if not exists idx_ai_usage_created_at on ai_usage(created_at desc)")
    execute("create index if not exists idx_ai_usage_agent_name on ai_usage(agent_name)")
    execute("create index if not exists idx_ai_usage_model on ai_usage(model)")
    execute("create index if not exists idx_ai_usage_customer on ai_usage(customer)")
    execute("create index if not exists idx_ai_usage_intake_id on ai_usage(intake_id)")
    execute("create index if not exists idx_ai_usage_case_id on ai_usage(case_id)")
    execute("create index if not exists idx_ai_usage_load_id on ai_usage(load_id)")


def log_ai_usage_record(
    *,
    task: str,
    agent_name: str = "",
    model: str = "",
    ok: bool = True,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_seconds: float | None = None,
    intake_id=None,
    case_id=None,
    load_id=None,
    customer: str = "",
    booking_number: str = "",
    conversation_key: str = "",
    cache_hit: bool = False,
    error: str = "",
    metadata: dict | None = None,
) -> None:
    ensure_ai_usage_schema()
    total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    estimated_cost = estimate_ai_cost(model, input_tokens, output_tokens)

    execute(
        """
        insert into ai_usage (
            task, agent_name, model, ok,
            input_tokens, output_tokens, total_tokens, estimated_cost,
            latency_seconds, intake_id, case_id, load_id,
            customer, booking_number, conversation_key,
            cache_hit, error, metadata
        )
        values (
            :task, :agent_name, :model, :ok,
            :input_tokens, :output_tokens, :total_tokens, :estimated_cost,
            :latency_seconds, :intake_id, :case_id, :load_id,
            :customer, :booking_number, :conversation_key,
            :cache_hit, :error, cast(:metadata as jsonb)
        )
        """,
        {
            "task": task,
            "agent_name": agent_name or task,
            "model": model,
            "ok": bool(ok),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": total_tokens,
            "estimated_cost": estimated_cost,
            "latency_seconds": latency_seconds,
            "intake_id": intake_id,
            "case_id": case_id,
            "load_id": load_id,
            "customer": customer or None,
            "booking_number": booking_number or None,
            "conversation_key": conversation_key or None,
            "cache_hit": bool(cache_hit),
            "error": error or None,
            "metadata": __import__("json").dumps(metadata or {}, default=str),
        },
    )


def load_ai_usage_summary(start_date: date | None = None, end_date: date | None = None) -> dict:
    ensure_ai_usage_schema()
    params = {"start_date": start_date, "end_date": end_date}
    where = "where (:start_date is null or created_at::date >= :start_date) and (:end_date is null or created_at::date <= :end_date)"

    try:
        df = read_df(
            f"""
            select
                count(*) as calls,
                coalesce(sum(input_tokens), 0) as input_tokens,
                coalesce(sum(output_tokens), 0) as output_tokens,
                coalesce(sum(total_tokens), 0) as total_tokens,
                coalesce(sum(estimated_cost), 0) as estimated_cost,
                coalesce(avg(latency_seconds), 0) as avg_latency_seconds,
                coalesce(sum(case when ok then 1 else 0 end), 0) as successful_calls,
                coalesce(sum(case when cache_hit then 1 else 0 end), 0) as cache_hits
            from ai_usage
            {where}
            """,
            params,
        )
    except Exception:
        return {}

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
        "cache_hits": int(row.get("cache_hits", 0) or 0),
    }


def load_ai_usage_by_agent(start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    ensure_ai_usage_schema()
    return read_df(
        """
        select
            coalesce(agent_name, task, 'unknown') as agent_name,
            coalesce(model, '') as model,
            count(*) as calls,
            coalesce(sum(total_tokens), 0) as total_tokens,
            coalesce(sum(input_tokens), 0) as input_tokens,
            coalesce(sum(output_tokens), 0) as output_tokens,
            coalesce(sum(estimated_cost), 0) as estimated_cost,
            round(coalesce(avg(latency_seconds), 0)::numeric, 3) as avg_latency_seconds,
            coalesce(sum(case when ok then 1 else 0 end), 0) as successful_calls,
            coalesce(sum(case when not ok then 1 else 0 end), 0) as failed_calls
        from ai_usage
        where (:start_date is null or created_at::date >= :start_date)
          and (:end_date is null or created_at::date <= :end_date)
        group by coalesce(agent_name, task, 'unknown'), coalesce(model, '')
        order by estimated_cost desc, calls desc
        """,
        {"start_date": start_date, "end_date": end_date},
    )


def load_ai_usage_recent(limit: int = 100) -> pd.DataFrame:
    ensure_ai_usage_schema()
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
            estimated_cost,
            latency_seconds,
            customer,
            booking_number,
            intake_id,
            case_id,
            load_id,
            cache_hit,
            error
        from ai_usage
        order by created_at desc
        limit :limit
        """,
        {"limit": int(limit)},
    )
