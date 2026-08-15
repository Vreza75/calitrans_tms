"""Phase 9: standalone operational entry point for the domain-event
publisher (services/realtime_publisher.py) plus operator recovery
tooling (repositories/domain_event_repo.py's list/get/retry functions).
Not embedded in Streamlit rendering - run `process` periodically
(same schedule as scripts/process_outbox.py / process_worker_jobs.py -
see .github/workflows/process-jobs.yml); run the other subcommands
interactively when investigating a stuck/failed event.

Usage:
    python scripts/process_realtime_events.py process                       # up to 100 pending events, once
    python scripts/process_realtime_events.py process --max-events 200
    python scripts/process_realtime_events.py process --reclaim-stuck-minutes 15

    python scripts/process_realtime_events.py list-pending
    python scripts/process_realtime_events.py list-failed
    python scripts/process_realtime_events.py inspect 42
    python scripts/process_realtime_events.py retry 42
    python scripts/process_realtime_events.py retry 42 --reset-attempts
    python scripts/process_realtime_events.py retry-all-failed --yes
    python scripts/process_realtime_events.py status                        # queue depth per status

    python scripts/process_realtime_events.py emit-test load 999           # dev-only: enqueue+publish one
                                                                              # test event, requires
                                                                              # REALTIME_ENABLED=false (no-op
                                                                              # publisher) or an explicit
                                                                              # --i-know-this-hits-a-real-channel
                                                                              # flag - never silently
                                                                              # broadcasts a test event to a
                                                                              # real production channel.

Every subcommand takes only integers/flags - never a free-text field or
table name - so there is no SQL-injection surface from CLI arguments.

Exit code for `process`: 0 on success (including "nothing to process"),
1 if any event ended this run in the terminal 'failed' state. Exit code
for the other subcommands: 0 on success, 1 if the target event/id was not
found or not eligible.
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
    from repositories import domain_event_repo
    from services.realtime_publisher import process_pending

    if args.reclaim_stuck_minutes is not None:
        with transaction() as conn:
            reclaimed = domain_event_repo.reclaim_stuck_processing(
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
        f"version={row.get('version')} status={row['status']} attempts={row['attempt_count']} "
        f"available_at={row['available_at']} created_at={row['created_at']} last_error={row['last_error']!r}"
    )


def _cmd_list(args: argparse.Namespace, status: str) -> int:
    from db_client import transaction
    from repositories import domain_event_repo

    with transaction() as conn:
        rows = domain_event_repo.list_by_status(conn, status, limit=args.limit)

    if not rows:
        print(f"No '{status}' events.")
        return 0

    for row in rows:
        _print_event_row(row)
    print(f"{len(rows)} '{status}' event(s).")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import domain_event_repo

    with transaction() as conn:
        event = domain_event_repo.get_event(conn, args.event_id)

    if event is None:
        print(f"No event with id={args.event_id}.")
        return 1

    _print_event_row(event)
    print(f"published_at={event['published_at']} claimed_at={event['claimed_at']} actor={event['actor']}")
    print(f"payload={event['payload']}")
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import domain_event_repo

    with transaction() as conn:
        requeued = domain_event_repo.retry_event(conn, args.event_id, reset_attempts=args.reset_attempts)

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
    from repositories import domain_event_repo

    with transaction() as conn:
        count = domain_event_repo.retry_all_failed(conn, reset_attempts=args.reset_attempts)

    print(f"Requeued {count} event(s) to 'pending'" + (" (attempts reset)" if args.reset_attempts else "") + ".")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import domain_event_repo

    with transaction() as conn:
        counts = domain_event_repo.count_by_status(conn)

    for status in ("pending", "processing", "published", "failed"):
        print(f"{status}: {counts.get(status, 0)}")
    return 0


def _cmd_emit_test(args: argparse.Namespace) -> int:
    """Dev-only: enqueue and immediately publish one test event. Refuses
    to run against a real Supabase channel unless the operator explicitly
    passes --i-know-this-hits-a-real-channel, so an accidental run never
    broadcasts test noise to a production client (STEP 18: "optionally
    publish test event in dev only")."""
    from config import get_secret

    realtime_enabled = (get_secret("REALTIME_ENABLED", "false") or "false").strip().lower() in {"1", "true", "yes"}
    if realtime_enabled and not args.i_know_this_hits_a_real_channel:
        print(
            "REALTIME_ENABLED is true (a real Supabase channel would receive this test event). "
            "Pass --i-know-this-hits-a-real-channel to confirm, or unset REALTIME_ENABLED for a no-op dry run."
        )
        return 1

    from db_client import transaction
    from realtime.events import publish_event
    from services.realtime_publisher import process_one

    import time

    idempotency_key = f"test:{args.aggregate_type}:{args.aggregate_id}:{int(time.time())}"

    with transaction() as conn:
        publish_event(
            conn=conn,
            event_type="test.ping",
            aggregate_type=args.aggregate_type,
            aggregate_id=args.aggregate_id,
            idempotency_key=idempotency_key,
        )

    result = process_one()
    print(f"Enqueued and processed: {result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process", help="Process up to N pending events, once.")
    process_parser.add_argument("--max-events", type=int, default=100)
    process_parser.add_argument(
        "--reclaim-stuck-minutes",
        type=int,
        default=None,
        help="Force a reclaim of events stuck 'processing' longer than this many minutes, before processing.",
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
    retry_parser.add_argument("--reset-attempts", action="store_true")
    retry_parser.set_defaults(func=_cmd_retry)

    retry_all_parser = subparsers.add_parser("retry-all-failed", help="Bulk-requeue every failed event.")
    retry_all_parser.add_argument("--reset-attempts", action="store_true")
    retry_all_parser.add_argument("--yes", action="store_true", help="Required to confirm the bulk action.")
    retry_all_parser.set_defaults(func=_cmd_retry_all_failed)

    status_parser = subparsers.add_parser("status", help="Queue depth per status.")
    status_parser.set_defaults(func=_cmd_status)

    emit_test_parser = subparsers.add_parser("emit-test", help="Dev-only: enqueue and publish one test event.")
    emit_test_parser.add_argument("aggregate_type")
    emit_test_parser.add_argument("aggregate_id")
    emit_test_parser.add_argument("--i-know-this-hits-a-real-channel", action="store_true")
    emit_test_parser.set_defaults(func=_cmd_emit_test)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
