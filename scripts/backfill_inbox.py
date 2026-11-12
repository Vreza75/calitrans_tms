"""Historical Operations Inbox backfill.

Fetches older mail by date window per mailbox (IMAP SINCE/BEFORE) and enqueues
the same `inbox.process_message` jobs the live sync does, reusing the exact
dedupe (services.operations_inbox_service.operations_email_already_imported)
and the exact enqueue (same job_type / idempotency_key / payload shape) as
workers/inbox_handlers.py::_fetch_and_enqueue_inbox_messages - there is one
processing pipeline, not a second one.

It NEVER creates or updates loads, sends email, or sends SMS. It only enqueues
`inbox.process_message` jobs; those parse + persist an order_intake row and
stop there. Load creation is a separate, human-reviewed, learning-phase-gated
step (services.operations_inbox_service.create_load_from_inbox_item), which
this script never calls.

Historical-vs-live classification is NOT decided by this script: it stamps the
enqueued job with `backfill: True` only as a fallback hint. The authoritative
decision happens at process time in workers/inbox_handlers.py from each
message's own received date vs INBOX_LIVE_CUTOFF (services.inbox_backfill_policy),
so enqueue order between this backfill and the live sync cannot change the
outcome. If a window overlaps live (post-cutoff) dates, those messages are
correctly processed as live work, not buried as historical.

Usage:
    python scripts/backfill_inbox.py --since 2026-01-01 --until 2026-02-01 --batch 100
    python scripts/backfill_inbox.py --since 2026-01-01 --until 2026-02-01 --batch 100 --dry-run

Window:
    --since / --until are YYYY-MM-DD, both inclusive. --until inclusive is
    implemented as IMAP `BEFORE <until + 1 day>` (IMAP BEFORE is exclusive).

Bounded + resumable:
    Each run enqueues at most --batch NEW (not-already-imported) messages PER
    MAILBOX, newest-first within the window. Re-run the same window until a run
    reports 0 queued: already-imported messages are skipped by dedupe, and the
    `inbox.process_message:<provider-message-id>` idempotency key makes any
    re-enqueue a no-op, so re-running is always safe and advances through
    older messages as earlier ones become imported. If one mailbox holds more
    than FETCH_CAP messages in the window, narrow the date window.

--dry-run only COUNTS what would be enqueued (no attachment bytes fetched, no
jobs enqueued, no writes).

Exit code: 0 on success (including "nothing to do"); 1 if any mailbox errored.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Per-mailbox safety cap on how many messages one run pulls from the window.
# The window is re-scanned newest-first each run and dedupe advances the
# frontier; a window with more than this many messages in one mailbox should
# be split into smaller date ranges.
FETCH_CAP = 1000

SYSTEM_ACTOR_INBOX_BACKFILL = "system:inbox-backfill"


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _imap_date(value: date) -> str:
    return value.strftime("%d-%b-%Y")  # e.g. 01-Jan-2026


def _build_search_query(since: date, until: date) -> str:
    # SINCE is inclusive; BEFORE is exclusive -> BEFORE until+1 makes --until inclusive.
    return f"SINCE {_imap_date(since)} BEFORE {_imap_date(until + timedelta(days=1))}"


def run_backfill(*, since: date, until: date, batch: int, dry_run: bool) -> dict:
    from services import email_client
    from services.operations_inbox_service import (
        _email_sync_unique_message_id,
        _save_operations_email_attachments,
        ensure_operations_email_sync_schema,
        load_existing_operations_email_lookup,
        operations_email_already_imported,
        safe_str,
    )

    ensure_operations_email_sync_schema()

    accounts = email_client._operations_email_accounts()
    search_query = _build_search_query(since, until)
    existing_lookup = load_existing_operations_email_lookup(limit=max(3000, FETCH_CAP))

    totals = {"fetched": 0, "skipped": 0, "queued": 0, "already_queued": 0, "errors": 0, "per_mailbox": []}

    for account in accounts:
        email_address = account.get("email")
        alias = email_client.operations_mailbox_alias(account.get("mailbox") or email_address)
        mb = {
            "alias": alias,
            "login": "failed",
            "error_type": "",
            "found": 0,
            "skipped": 0,
            "queued": 0,
            "already_queued": 0,
        }

        if not email_address or not account.get("password"):
            mb["error_type"] = "MissingCredentials"
            totals["errors"] += 1
            totals["per_mailbox"].append(mb)
            _log_mailbox(mb)
            continue

        try:
            messages = (
                email_client._fetch_recent_emails(
                    limit=FETCH_CAP,
                    scan_window=FETCH_CAP,
                    search_query=search_query,
                    mailbox=email_client.get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
                    direction="inbound",
                    require_terms=False,
                    fallback_search_to_all=False,  # an empty window must stay empty, never fall back to ALL
                    email_address=email_address,
                    email_password=account.get("password"),
                    include_attachments=(not dry_run),
                )
                or []
            )
            mb["login"] = "ok"
            mb["found"] = len(messages)
            totals["fetched"] += len(messages)
        except Exception as exc:  # noqa: BLE001 - one mailbox's failure must not abort the others
            mb["error_type"] = type(exc).__name__
            totals["errors"] += 1
            totals["per_mailbox"].append(mb)
            _log_mailbox(mb)
            continue

        for message in messages:
            if mb["queued"] >= batch:
                break  # bounded per mailbox per run

            message_id = _email_sync_unique_message_id(message)
            subject = safe_str(message.get("subject")) or "(no subject)"
            sender = safe_str(message.get("from")) or safe_str(message.get("sender")) or "(unknown sender)"
            received_at = safe_str(message.get("received_at")) or None

            if operations_email_already_imported(
                message_id, subject=subject, sender=sender, received_at=received_at, lookup=existing_lookup
            ):
                mb["skipped"] += 1
                totals["skipped"] += 1
                continue

            from db_client import transaction
            from repositories.worker_job_repo import enqueue_job, job_exists_for_idempotency_key

            from workers.inbox_handlers import _embed_attachment_content

            idempotency_key = f"inbox.process_message:{message_id}"

            if dry_run:
                mb["queued"] += 1  # would enqueue a new job
                totals["queued"] += 1
                continue

            try:
                pre_saved = _save_operations_email_attachments(message, message_id)
            except Exception:
                pre_saved = []
            pre_saved = _embed_attachment_content(pre_saved, message)
            job_message = {key: value for key, value in message.items() if key != "attachments"}

            with transaction() as conn:
                # Distinguish a genuinely NEW job from an idempotency-key conflict
                # (a job this message already has from an earlier run / the live
                # sync): enqueue_job's ON CONFLICT would otherwise silently
                # resolve to the existing row and be miscounted as newly queued.
                if job_exists_for_idempotency_key(conn, idempotency_key):
                    mb["already_queued"] += 1
                    totals["already_queued"] += 1
                else:
                    enqueue_job(
                        conn=conn,
                        job_type="inbox.process_message",
                        aggregate_type="email_message",
                        aggregate_id=message_id,
                        payload={
                            "message": job_message,
                            "pre_saved_attachments": pre_saved,
                            "backfill": True,
                        },
                        idempotency_key=idempotency_key,
                        actor=SYSTEM_ACTOR_INBOX_BACKFILL,
                    )
                    mb["queued"] += 1
                    totals["queued"] += 1
            existing_lookup.setdefault("by_message_id", {})[message_id.lower()] = {"source_message_id": message_id}

        # Explicit progress accounting for this run of this mailbox.
        mb["remaining"] = max(0, mb["found"] - mb["skipped"] - mb["queued"] - mb["already_queued"])
        # The fetch window is capped at FETCH_CAP messages; if it filled, older
        # messages in the date range were not even seen this run. Surface it
        # loudly so the operator narrows the window rather than silently
        # stalling (a durable UID cursor is the longer-term fix - see docstring).
        mb["window_truncated"] = mb["found"] >= FETCH_CAP
        if mb["window_truncated"]:
            print(
                f"backfill WARNING mailbox={mb['alias']} window hit FETCH_CAP={FETCH_CAP}; "
                f"narrow --since/--until to reach older messages."
            )
        totals["per_mailbox"].append(mb)
        _log_mailbox(mb)

    totals["remaining"] = sum(m.get("remaining", 0) for m in totals["per_mailbox"])
    return totals


def _log_mailbox(mb: dict) -> None:
    """One sanitized line per mailbox - alias, login state, error type only,
    and integer counts. No addresses, subjects, bodies, or passwords."""
    print(
        f"backfill mailbox={mb['alias']} login={mb['login']} error_type={mb['error_type'] or '-'} "
        f"found={mb['found']} imported_skipped={mb['skipped']} "
        f"jobs_queued={mb['queued']} already_queued={mb.get('already_queued', 0)} "
        f"remaining={mb.get('remaining', 0)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--until", required=True, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--batch", required=True, type=int, help="Max NEW messages to enqueue per mailbox this run.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only count what would be enqueued; no attachment fetch, no enqueue."
    )
    args = parser.parse_args()

    try:
        since = _parse_date(args.since)
        until = _parse_date(args.until)
    except ValueError:
        print("--since and --until must be YYYY-MM-DD dates.", file=sys.stderr)
        return 2
    if since > until:
        print("--since must be on or before --until.", file=sys.stderr)
        return 2
    if args.batch <= 0:
        print("--batch must be a positive integer.", file=sys.stderr)
        return 2

    totals = run_backfill(since=since, until=until, batch=args.batch, dry_run=args.dry_run)

    mode = "DRY-RUN (no jobs enqueued)" if args.dry_run else "enqueued"
    print(
        f"backfill {args.since}..{args.until} {mode}: "
        f"fetched={totals['fetched']} skipped={totals['skipped']} "
        f"{'would_queue' if args.dry_run else 'queued'}={totals['queued']} "
        f"already_queued={totals['already_queued']} remaining={totals.get('remaining', 0)} "
        f"mailbox_errors={totals['errors']}"
    )
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
