# services/inbox_backfill_policy.py

from __future__ import annotations

"""Deterministic, flag-independent live-vs-historical classification for inbox
messages (PR #19 Stage 2 / Task B + P1 remediation).

Framework-neutral: no streamlit, no DB - imported by workers/inbox_handlers.py
(the process-message job handler) and scripts/backfill_inbox.py. Depends only on
`config` and `pandas`.

WHY THIS EXISTS. Live sync and historical backfill enqueue the SAME
`inbox.process_message:<message-id>` worker job. enqueue_job's
`ON CONFLICT (idempotency_key) DO UPDATE` never rewrites an existing row's
payload, so a producer-stamped `backfill` flag would let whichever producer
enqueued a message first permanently freeze its classification - a backfill of
recent mail could win that race and bury a live, still-actionable email.

THE RULE (producer flag is NEVER consulted). Classification is an intrinsic
property of the message: its own received date vs a configured live cutoff
(INBOX_LIVE_CUTOFF). A message is HISTORICAL iff its received date is strictly
before the cutoff; LIVE iff at or after it. When the inputs are not trustworthy
(no valid cutoff, or an unparseable received date) the result is INDETERMINATE,
handled explicitly by the caller (never guessed from a producer flag).

STABILITY ACROSS RETRIES / CUTOFF CHANGES. The authoritative decision is the
one PERSISTED on the order_intake row at first insert. _insert_operations_email_
record_row uses `ON CONFLICT (source_message_id) DO NOTHING`, so a later retry -
even one that recomputes a different classification because INBOX_LIVE_CUTOFF was
changed in between - cannot overwrite the persisted review_status. The decision
is made once and stands. POLICY_VERSION is stamped alongside it for audit and
future re-classification migrations.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import get_secret

INBOX_LIVE_CUTOFF_SETTING = "INBOX_LIVE_CUTOFF"

# Bump when the classification rule changes in a way that would re-decide
# existing messages; persisted alongside each decision so a future migration can
# tell which rule classified a row.
POLICY_VERSION = 2

LIVE = "live"
HISTORICAL = "historical"
INDETERMINATE = "indeterminate"

# Sentinel so callers can pass cutoff=None to mean "no cutoff" explicitly,
# distinct from "not supplied, read from config".
_UNSET = object()


@dataclass(frozen=True)
class ClassificationDecision:
    """The flag-independent classification of one message. `status` is LIVE,
    HISTORICAL, or INDETERMINATE; `reason` is a short machine token for logs and
    audit; `policy_version` records which rule produced it."""

    status: str
    reason: str
    policy_version: int = POLICY_VERSION

    @property
    def is_historical(self) -> bool:
        return self.status == HISTORICAL

    @property
    def is_indeterminate(self) -> bool:
        return self.status == INDETERMINATE


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


def classify_message(message: dict, *, cutoff: Any = _UNSET) -> ClassificationDecision:
    """Classify `message` as LIVE / HISTORICAL / INDETERMINATE purely from its
    own received date vs the live cutoff. The producer's backfill flag is never
    an input. INDETERMINATE when a trustworthy decision cannot be made (no valid
    cutoff, or an unparseable received date) - the caller decides what to do with
    that, explicitly, rather than falling back to the race-prone flag."""
    cutoff_ts = inbox_live_cutoff() if cutoff is _UNSET else _to_utc_timestamp(cutoff)
    if cutoff_ts is None:
        return ClassificationDecision(INDETERMINATE, "no_cutoff")

    received_ts = _message_received_timestamp(message)
    if received_ts is None:
        return ClassificationDecision(INDETERMINATE, "unparseable_received_date")

    if received_ts < cutoff_ts:
        return ClassificationDecision(HISTORICAL, "received_before_cutoff")
    return ClassificationDecision(LIVE, "received_at_or_after_cutoff")


def cutoff_is_configured() -> bool:
    """True iff a valid INBOX_LIVE_CUTOFF is configured. Used by the backfill
    producer to REFUSE to run without one - without a cutoff, historical mail
    cannot be distinguished from live, and enqueuing it would (via the
    indeterminate->live safe default) flood the active dispatch queue."""
    return inbox_live_cutoff() is not None
