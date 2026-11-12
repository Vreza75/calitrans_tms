# services/attachment_retention.py

from __future__ import annotations

"""Reference-aware retention for durable attachment objects (PR #19 Stage 2 /
Task A blocker 5).

Framework-neutral: no streamlit. Deleting a durable object is an explicit,
operator-driven action (retention/purge) - NEVER implicit during processing. And
it must be reference-aware: an object still referenced by an active record
(order_intake.parsed_data, or a non-terminal worker_jobs payload) must not be
deleted, or a later preview/download/reparse would fail to resolve it.

Retention policy itself (how long objects live) is configured as a bucket
lifecycle/TTL policy in Supabase - see docs/architecture/DURABLE_ATTACHMENTS.md.
This module provides the safe deletion primitive that policy drives.
"""

import logging

logger = logging.getLogger(__name__)


def object_is_referenced(storage_key: str) -> bool:
    """True if any active record still references this object key: an
    order_intake row's parsed_data, or a worker_jobs row that is NOT terminally
    completed/failed (a pending/processing job may still need to resolve it).
    Read-only."""
    key = str(storage_key or "")
    if not key:
        return True  # unknown key - treat as referenced (never delete blindly)

    from db_client import read_df

    pattern = f"%{key}%"
    intake = read_df(
        "select 1 from order_intake where cast(parsed_data as text) like :pat limit 1",
        {"pat": pattern},
    )
    if not intake.empty:
        return True

    jobs = read_df(
        "select 1 from worker_jobs "
        "where status not in ('completed', 'failed') and cast(payload as text) like :pat limit 1",
        {"pat": pattern},
    )
    return not jobs.empty


def purge_object_if_unreferenced(storage_key: str, *, storage=None) -> bool:
    """Delete a durable object ONLY if no active record references it. Returns
    True if it was deleted, False if it was kept (still referenced) or storage is
    unavailable. Safe to call repeatedly."""
    key = str(storage_key or "")
    if not key:
        return False

    if storage is None:
        from services.object_storage import get_object_storage

        storage = get_object_storage()
    if storage is None:
        return False

    if object_is_referenced(key):
        logger.info("retention: keeping still-referenced object")
        return False

    storage.delete(key)
    logger.info("retention: purged unreferenced object")
    return True
