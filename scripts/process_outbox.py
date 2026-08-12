"""Phase 6: standalone operational entry point for the transactional
outbox processor (services/outbox_processor.py) plus operator recovery
tooling (repositories/outbox_repo.py's list/get/retry functions). Not
embedded in Streamlit rendering - run `process` periodically via
cron/Task Scheduler/a future worker; run the other subcommands
interactively when investigating a stuck/failed event.

Usage:
    python scripts/process_outbox.py process                          # up to 50 pending events, once
    python scripts/process_outbox.py process --max-events 200
    python scripts/process_outbox.py process --reclaim-stuck-minutes 15   # force a reclaim threshold
                                                                            # other than the processor's
                                                                            # built-in default before
                                                                            # this run (process_pending
                                                                            # always reclaims stale
                                                                            # 'processing' events on its
                                                                            # own - see
                                                                            # services/outbox_processor.py
                                                                            # ::RECLAIM_STALE_AFTER)

    python scripts/process_outbox.py list-pending
    python scripts/process_outbox.py list-failed
    python scripts/process_outbox.py inspect 42
    python scripts/process_outbox.py retry 42                         # requeue one failed event
    python scripts/process_outbox.py retry 42 --reset-attempts        # ...and zero its attempt count
    python scripts/process_outbox.py retry-all-failed --yes           # bulk requeue every failed event

Every subcommand takes only integers/flags - never a free-text field or
table name - so there is no SQL-injection surface from CLI arguments.

Exit code for `process`: 0 on success (including "nothing to process"),
1 if any event ended this run in the terminal 'failed' state (so
cron/monitoring can alert on it). Exit code for the other subcommands:
0 on success, 1 if the target event/id was not found or not eligible.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _cmd_process(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import outbox_repo
    from services.outbox_processor import process_pending

    if args.reclaim_stuck_minutes is not None:
        with transaction() as conn:
            reclaimed = outbox_repo.reclaim_stuck_processing(
                conn, older_than=timedelta(minutes=args.reclaim_stuck_minutes)
            )
        print(f"Reclaimed {reclaimed} stuck event(s).")

    results = process_pending(max_events=args.max_events)
    if not results:
        print("Nothing to process.")
        return 0

    failed = 0
    for result in results:
        print(f"id={result['id']} event_type={result['event_type']} outcome={result['outcome']}")
        if result["outcome"] == "failed":
            failed += 1

    print(f"Processed {len(results)} event(s), {failed} terminally failed.")
    return 1 if failed else 0


def _print_event_row(row: dict) -> None:
    print(
        f"id={row['id']} event_type={row['event_type']} aggregate={row['aggregate_type']}:{row['aggregate_id']} "
        f"status={row['status']} attempts={row['attempt_count']} available_at={row['available_at']} "
        f"created_at={row['created_at']} last_error={row['last_error']!r}"
    )


def _cmd_list(args: argparse.Namespace, status: str) -> int:
    from db_client import transaction
    from repositories import outbox_repo

    with transaction() as conn:
        rows = outbox_repo.list_by_status(conn, status, limit=args.limit)

    if not rows:
        print(f"No '{status}' events.")
        return 0

    for row in rows:
        _print_event_row(row)
    print(f"{len(rows)} '{status}' event(s).")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import outbox_repo

    with transaction() as conn:
        event = outbox_repo.get_event(conn, args.event_id)

    if event is None:
        print(f"No event with id={args.event_id}.")
        return 1

    _print_event_row(event)
    print(f"processed_at={event['processed_at']} claimed_at={event['claimed_at']} actor={event['actor']}")
    print(f"payload={event['payload']}")
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import outbox_repo

    with transaction() as conn:
        requeued = outbox_repo.retry_event(conn, args.event_id, reset_attempts=args.reset_attempts)

    if not requeued:
        print(f"id={args.event_id} was not in 'failed' status - nothing to do.")
        return 1

    print(f"id={args.event_id} requeued to 'pending'" + (" (attempts reset)" if args.reset_attempts else "") + ".")
    return 0


def _cmd_retry_all_failed(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Pass --yes to confirm requeueing every 'failed' event.")
        return 1

    from db_client import transaction
    from repositories import outbox_repo

    with transaction() as conn:
        count = outbox_repo.retry_all_failed(conn, reset_attempts=args.reset_attempts)

    print(f"Requeued {count} event(s) to 'pending'" + (" (attempts reset)" if args.reset_attempts else "") + ".")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process", help="Process up to N pending events, once.")
    process_parser.add_argument("--max-events", type=int, default=50, help="Maximum events to process this run.")
    process_parser.add_argument(
        "--reclaim-stuck-minutes",
        type=int,
        default=None,
        help="Force a reclaim of events stuck 'processing' longer than this many minutes, before processing "
        "(process_pending already does this automatically with a built-in default - use this to override it).",
    )
    process_parser.set_defaults(func=_cmd_process)

    list_pending_parser = subparsers.add_parser("list-pending", help="List pending events.")
    list_pending_parser.add_argument("--limit", type=int, default=50)
    list_pending_parser.set_defaults(func=lambda a: _cmd_list(a, "pending"))

    list_failed_parser = subparsers.add_parser("list-failed", help="List terminally-failed events.")
    list_failed_parser.add_argument("--limit", type=int, default=50)
    list_failed_parser.set_defaults(func=lambda a: _cmd_list(a, "failed"))

    inspect_parser = subparsers.add_parser("inspect", help="Show full detail for one event, including payload.")
    inspect_parser.add_argument("event_id", type=int)
    inspect_parser.set_defaults(func=_cmd_inspect)

    retry_parser = subparsers.add_parser("retry", help="Requeue one failed event.")
    retry_parser.add_argument("event_id", type=int)
    retry_parser.add_argument("--reset-attempts", action="store_true", help="Also zero the event's attempt count.")
    retry_parser.set_defaults(func=_cmd_retry)

    retry_all_parser = subparsers.add_parser("retry-all-failed", help="Bulk-requeue every failed event.")
    retry_all_parser.add_argument("--reset-attempts", action="store_true", help="Also zero each event's attempt count.")
    retry_all_parser.add_argument("--yes", action="store_true", help="Required to confirm the bulk action.")
    retry_all_parser.set_defaults(func=_cmd_retry_all_failed)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
