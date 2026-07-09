# ai_core/company_memory.py

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from db_client import execute, read_df


@dataclass
class MemoryResult:
    ok: bool
    memory_type: str
    key: str
    value: Dict[str, Any]
    confidence: float
    source: str
    reason: str


MEMORY_TYPES = [
    "customer_alias",
    "customer_preference",
    "warehouse_alias",
    "warehouse_preference",
    "routing_rule",
    "dispatcher_correction",
    "approved_reply",
    "contact_preference",
    "document_rule",
]


def _safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def _json_dump(value: Any) -> str:
    return json.dumps(value or {}, default=str)


def ensure_company_memory_schema() -> None:
    """Create the company_memory table used by AI agents and dispatcher learning."""

    execute(
        """
        create table if not exists company_memory (
            id bigserial primary key,
            memory_type text not null,
            memory_key text not null,
            memory_value jsonb not null default '{}'::jsonb,
            customer text,
            sender_domain text,
            source text,
            confidence numeric not null default 0.75,
            is_active boolean not null default true,
            usage_count integer not null default 0,
            last_used_at timestamptz,
            created_by text not null default 'system',
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique(memory_type, memory_key)
        )
        """
    )

    execute("create index if not exists idx_company_memory_type on company_memory(memory_type)")
    execute("create index if not exists idx_company_memory_key on company_memory(memory_key)")
    execute("create index if not exists idx_company_memory_customer on company_memory(customer)")
    execute("create index if not exists idx_company_memory_sender_domain on company_memory(sender_domain)")
    execute("create index if not exists idx_company_memory_active on company_memory(is_active)")


def normalize_memory_key(value: str) -> str:
    return " ".join(_safe_str(value).lower().split())


def sender_domain_from_email(sender: str) -> str:
    sender = _safe_str(sender).lower()
    if "<" in sender and ">" in sender:
        sender = sender.split("<", 1)[-1].split(">", 1)[0]
    if "@" not in sender:
        return ""
    return sender.rsplit("@", 1)[-1]


def upsert_company_memory(
    *,
    memory_type: str,
    memory_key: str,
    memory_value: Dict[str, Any],
    customer: str = "",
    sender_domain: str = "",
    source: str = "dispatcher",
    confidence: float = 0.80,
    created_by: str = "dispatcher",
) -> MemoryResult:
    """Create or update a reusable memory item."""

    ensure_company_memory_schema()

    memory_type = _safe_str(memory_type)
    memory_key = normalize_memory_key(memory_key)

    if memory_type not in MEMORY_TYPES:
        memory_type = "dispatcher_correction"

    if not memory_key:
        return asdict(MemoryResult(
            ok=False,
            memory_type=memory_type,
            key="",
            value={},
            confidence=0.0,
            source=source,
            reason="Memory key is blank.",
        ))

    execute(
        """
        insert into company_memory (
            memory_type,
            memory_key,
            memory_value,
            customer,
            sender_domain,
            source,
            confidence,
            created_by
        )
        values (
            :memory_type,
            :memory_key,
            cast(:memory_value as jsonb),
            nullif(:customer, ''),
            nullif(:sender_domain, ''),
            nullif(:source, ''),
            :confidence,
            :created_by
        )
        on conflict (memory_type, memory_key)
        do update set
            memory_value = company_memory.memory_value || excluded.memory_value,
            customer = coalesce(excluded.customer, company_memory.customer),
            sender_domain = coalesce(excluded.sender_domain, company_memory.sender_domain),
            source = coalesce(excluded.source, company_memory.source),
            confidence = greatest(company_memory.confidence, excluded.confidence),
            is_active = true,
            updated_at = now()
        """,
        {
            "memory_type": memory_type,
            "memory_key": memory_key,
            "memory_value": _json_dump(memory_value),
            "customer": _safe_str(customer),
            "sender_domain": _safe_str(sender_domain),
            "source": _safe_str(source),
            "confidence": float(confidence or 0.0),
            "created_by": _safe_str(created_by) or "dispatcher",
        },
    )

    return asdict(MemoryResult(
        ok=True,
        memory_type=memory_type,
        key=memory_key,
        value=memory_value,
        confidence=float(confidence or 0.0),
        source=source,
        reason="Memory saved.",
    ))


def search_company_memory(
    *,
    query: str = "",
    customer: str = "",
    sender: str = "",
    memory_types: Optional[List[str]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Retrieve relevant company memories for AI context."""

    ensure_company_memory_schema()

    query_key = normalize_memory_key(query)
    customer_key = _safe_str(customer)
    sender_domain = sender_domain_from_email(sender)
    memory_types = memory_types or MEMORY_TYPES

    conditions = ["is_active = true"]
    params: Dict[str, Any] = {"limit": int(limit)}

    if memory_types:
        conditions.append("memory_type = any(:memory_types)")
        params["memory_types"] = memory_types

    search_conditions = []
    if query_key:
        search_conditions.append("memory_key ilike :query_like")
        search_conditions.append("memory_value::text ilike :query_like")
        params["query_like"] = f"%{query_key}%"

    if customer_key:
        search_conditions.append("customer ilike :customer_like")
        params["customer_like"] = f"%{customer_key}%"

    if sender_domain:
        search_conditions.append("sender_domain = :sender_domain")
        params["sender_domain"] = sender_domain

    if search_conditions:
        conditions.append("(" + " or ".join(search_conditions) + ")")

    try:
        df = read_df(
            f"""
            select
                id,
                memory_type,
                memory_key,
                memory_value,
                customer,
                sender_domain,
                source,
                confidence,
                usage_count,
                last_used_at,
                created_at,
                updated_at
            from company_memory
            where {' and '.join(conditions)}
            order by confidence desc, usage_count desc, updated_at desc
            limit :limit
            """,
            params,
        )
    except Exception:
        return []

    if df.empty:
        return []

    ids = [int(value) for value in df["id"].dropna().tolist()]
    if ids:
        try:
            execute(
                """
                update company_memory
                set usage_count = usage_count + 1,
                    last_used_at = now()
                where id = any(:ids)
                """,
                {"ids": ids},
            )
        except Exception:
            pass

    return df.to_dict(orient="records")


def build_company_memory_context(
    *,
    customer: str = "",
    sender: str = "",
    subject: str = "",
    body: str = "",
    parsed: Optional[Dict[str, Any]] = None,
    limit: int = 8,
) -> Dict[str, Any]:
    """Return a compact context package for agents."""

    parsed = parsed or {}
    search_text = " ".join([
        _safe_str(customer),
        _safe_str(subject),
        _safe_str(parsed.get("Customer", "")),
        _safe_str(parsed.get("Warehouse", "")),
        _safe_str(parsed.get("Port", "")),
    ])

    memories = search_company_memory(
        query=search_text,
        customer=customer or parsed.get("Customer", ""),
        sender=sender,
        limit=limit,
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in memories:
        grouped.setdefault(item.get("memory_type", "other"), []).append(item)

    return {
        "customer": customer or parsed.get("Customer", ""),
        "sender_domain": sender_domain_from_email(sender),
        "memory_count": len(memories),
        "memories": grouped,
    }


def save_dispatcher_correction(
    *,
    correction_key: str,
    corrected_fields: Dict[str, Any],
    customer: str = "",
    sender: str = "",
    reason: str = "",
    intake_id: Optional[int] = None,
    load_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Save a dispatcher correction so future AI runs can use it."""

    value = {
        "corrected_fields": corrected_fields,
        "reason": reason,
        "intake_id": intake_id,
        "load_id": load_id,
    }

    return upsert_company_memory(
        memory_type="dispatcher_correction",
        memory_key=correction_key,
        memory_value=value,
        customer=customer,
        sender_domain=sender_domain_from_email(sender),
        source="dispatcher_correction",
        confidence=0.90,
        created_by="dispatcher",
    )


def save_approved_reply(
    *,
    customer: str,
    request_type: str,
    reply_body: str,
    language: str = "English",
    tone: str = "Professional",
) -> Dict[str, Any]:
    """Save a dispatcher-approved reply as a reusable example."""

    key = f"{customer} {request_type} {language} {tone}"

    return upsert_company_memory(
        memory_type="approved_reply",
        memory_key=key,
        memory_value={
            "customer": customer,
            "request_type": request_type,
            "language": language,
            "tone": tone,
            "reply_body": reply_body,
        },
        customer=customer,
        source="approved_reply",
        confidence=0.85,
        created_by="dispatcher",
    )


def deactivate_company_memory(memory_id: int) -> None:
    execute(
        """
        update company_memory
        set is_active = false,
            updated_at = now()
        where id = :memory_id
        """,
        {"memory_id": int(memory_id)},
    )
