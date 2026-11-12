# services/inbox_backfill_policy.py

from __future__ import annotations

"""Deterministic live-vs-historical classification for inbox messages
(PR #19 Stage 2 / Task B).

Framework-neutral: no streamlit, no DB - imported by both
workers/inbox_handlers.py (the process-message job handler) and
scripts/backfill_inbox.py. Depends only on `config` and `pandas`.

WHY THIS EXISTS. Live sync and historical backfill enqueue the SAME
`inbox.process_message:<message-id>` worker job (one processing pipeline, not
two). repositories.worker_job_repo.enqueue_job uses
`ON CONFLICT (idempotency_key) DO UPDATE` whose update is a deliberate no-op -
it never rewrites an existing row's payload. So if classification is driven by
a producer-stamped `backfill: True` payload flag, whichever producer enqueues a
given message FIRST permanently freezes that message's classification. A
backfill run covering recent dates could win that race for a still-actionable
live email and bury it as Historical, out of every active dispatch queue.

THE RULE. Historical-vs-live is an intrinsic property of the message, not of
which producer won the enqueue race: a message is historical iff its own
received date is strictly before a configured live cutoff
(INBOX_LIVE_CUTOFF). Evaluated at PROCESS time, this is identical regardless of
enqueue order, so first-producer ordering can no longer determine the outcome.

Fallbacks, both order-independent for the realistic case:
  - No cutoff configured -> defer to the producer flag (legacy behavior, for
    operators who have not set a go-live cutoff yet).
  - Cutoff configured but the message's date is unparseable (a degenerate edge;
    real email always carries a Date header) -> defer to the producer flag.
"""

from datetime import datetime
from typing import Any

import pandas as pd

from config import get_secret

INBOX_LIVE_CUTOFF_SETTING = "INBOX_LIVE_CUTOFF"

# Sentinel so callers can pass cutoff=None to mean "no cutoff" explicitly,
# distinct from "not supplied, read from config".
_UNSET = object()


def _to_utc_timestamp(value: Any) -> pd.Timestamp | None:
    """Normalize any date/datetime/ISO-string to a tz-aware UTC pandas
    Timestamp so a tz-aware message `received_at` and a possibly tz-naive
    configured cutoff compare correctly. Returns None when unparseable."""
    if value in (None, ""):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if ts is None or pd.isna(ts):
        return None
    return ts


def inbox_live_cutoff() -> pd.Timestamp | None:
    """The configured live cutoff as a tz-aware UTC Timestamp, or None when
    INBOX_LIVE_CUTOFF is unset/blank/unparseable. Read fresh (not cached at
    import) so configuration changes take effect without a restart. A date-only
    value (e.g. '2026-06-01') means midnight UTC on that day: mail strictly
    before it is historical, mail at or after it is live."""
    return _to_utc_timestamp(get_secret(INBOX_LIVE_CUTOFF_SETTING))


def _message_received_timestamp(message: dict) -> pd.Timestamp | None:
    received = message.get("received_at") or message.get("date")
    return _to_utc_timestamp(received)


def message_is_historical(
    message: dict,
    *,
    backfill_flag: bool = False,
    cutoff: Any = _UNSET,
) -> bool:
    """Return True if `message` is historical (must be inserted terminal and
    kept out of active dispatch workflows), False if it is live.

    Deterministic and order-independent for any dated message once a cutoff is
    configured: historical iff `received_at < cutoff`, the producer flag
    ignored. The flag is consulted only in the two fallbacks documented in the
    module docstring (no cutoff, or an undated message)."""
    cutoff_ts = inbox_live_cutoff() if cutoff is _UNSET else _to_utc_timestamp(cutoff)
    if cutoff_ts is None:
        return bool(backfill_flag)  # no cutoff -> legacy flag behavior

    received_ts = _message_received_timestamp(message)
    if received_ts is None:
        return bool(backfill_flag)  # undated -> can't date it, trust the lane

    return received_ts < cutoff_ts
