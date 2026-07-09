# ai_core/usage_logger.py

from __future__ import annotations

from typing import Any, Dict, Optional

from db_client import execute


MODEL_PRICING_PER_1M_TOKENS = {
    # Update later if model pricing changes
    "gpt-4.1-mini": {
        "input": 0.40,
        "output": 1.60,
    },
    "gpt-4.1": {
        "input": 2.00,
        "output": 8.00,
    },
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_PER_1M_TOKENS.get(
        model,
        {"input": 0.0, "output": 0.0},
    )

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 6)


def ensure_ai_usage_table() -> None:
    execute(
        """
        create table if not exists ai_usage_log (
            id bigserial primary key,
            created_at timestamptz not null default now(),
            task text,
            agent_name text,
            model text,
            ok boolean,
            input_tokens integer default 0,
            output_tokens integer default 0,
            total_tokens integer default 0,
            estimated_cost_usd numeric(12, 6) default 0,
            latency_seconds numeric(12, 3) default 0,
            error text,
            metadata jsonb not null default '{}'::jsonb
        )
        """
    )


def log_ai_usage(
    *,
    task: str,
    agent_name: Optional[str],
    model: str,
    ok: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_seconds: float = 0,
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    ensure_ai_usage_table()

    total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    cost = estimate_cost_usd(
        model=model,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
    )

    execute(
        """
        insert into ai_usage_log (
            task,
            agent_name,
            model,
            ok,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_usd,
            latency_seconds,
            error,
            metadata
        )
        values (
            :task,
            :agent_name,
            :model,
            :ok,
            :input_tokens,
            :output_tokens,
            :total_tokens,
            :estimated_cost_usd,
            :latency_seconds,
            :error,
            cast(:metadata as jsonb)
        )
        """,
        {
            "task": task,
            "agent_name": agent_name,
            "model": model,
            "ok": ok,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "latency_seconds": float(latency_seconds or 0),
            "error": error or "",
            "metadata": __import__("json").dumps(metadata or {}, default=str),
        },
    )