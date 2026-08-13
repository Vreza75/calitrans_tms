"""Phase 7: standalone operational entry point for the worker job
processor (workers/processor.py) plus operator recovery tooling
(repositories/worker_job_repo.py's list/get/retry functions). Not
embedded in Streamlit rendering - run `process` periodically via
cron/Task Scheduler/a future dedicated worker process; run the other
subcommands interactively when investigating a stuck/failed job.

Usage:
    python scripts/process_worker_jobs.py process                          # up to 50 pending jobs, once
    python scripts/process_worker_jobs.py process --max-jobs 200
    python scripts/process_worker_jobs.py process --reclaim-stuck-minutes 5   # force a reclaim threshold
                                                                                # other than the processor's
                                                                                # built-in default before
                                                                                # this run (process_pending
                                                                                # always reclaims stale
                                                                                # 'processing' jobs on its
                                                                                # own - see
                                                                                # workers/processor.py
                                                                                # ::RECLAIM_STALE_AFTER)

    python scripts/process_worker_jobs.py list-pending
    python scripts/process_worker_jobs.py list-failed
    python scripts/process_worker_jobs.py inspect 42
    python scripts/process_worker_jobs.py retry 42                         # requeue one failed job
    python scripts/process_worker_jobs.py retry 42 --reset-attempts        # ...and zero its attempt count
    python scripts/process_worker_jobs.py retry-all --yes                  # bulk requeue every failed job

Every subcommand takes only integers/flags - never a free-text field or
table name - so there is no SQL-injection surface from CLI arguments.

Exit code for `process`: 0 on success (including "nothing to process"),
1 if any job ended this run in the terminal 'failed' state (so
cron/monitoring can alert on it). Exit code for the other subcommands:
0 on success, 1 if the target job/id was not found or not eligible.
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
    from repositories import worker_job_repo
    from workers.processor import process_pending

    if args.reclaim_stuck_minutes is not None:
        with transaction() as conn:
            reclaimed = worker_job_repo.reclaim_stuck_processing(
                conn, older_than=timedelta(minutes=args.reclaim_stuck_minutes)
            )
        print(f"Reclaimed {reclaimed} stuck job(s).")

    results = process_pending(max_jobs=args.max_jobs)
    if not results:
        print("Nothing to process.")
        return 0

    failed = 0
    for result in results:
        print(f"id={result['id']} job_type={result['job_type']} outcome={result['outcome']}")
        if result["outcome"] == "failed":
            failed += 1

    print(f"Processed {len(results)} job(s), {failed} terminally failed.")
    return 1 if failed else 0


def _print_job_row(row: dict) -> None:
    print(
        f"id={row['id']} job_type={row['job_type']} aggregate={row['aggregate_type']}:{row['aggregate_id']} "
        f"status={row['status']} attempts={row['attempt_count']} available_at={row['available_at']} "
        f"created_at={row['created_at']} last_error={row['last_error']!r}"
    )


def _cmd_list(args: argparse.Namespace, status: str) -> int:
    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        rows = worker_job_repo.list_by_status(conn, status, limit=args.limit)

    if not rows:
        print(f"No '{status}' jobs.")
        return 0

    for row in rows:
        _print_job_row(row)
    print(f"{len(rows)} '{status}' job(s).")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        job = worker_job_repo.get_job(conn, args.job_id)

    if job is None:
        print(f"No job with id={args.job_id}.")
        return 1

    _print_job_row(job)
    print(f"completed_at={job['completed_at']} claimed_at={job['claimed_at']} actor={job['actor']}")
    print(f"payload={job['payload']}")
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        requeued = worker_job_repo.retry_job(conn, args.job_id, reset_attempts=args.reset_attempts)

    if not requeued:
        print(f"id={args.job_id} was not in 'failed' status - nothing to do.")
        return 1

    print(f"id={args.job_id} requeued to 'pending'" + (" (attempts reset)" if args.reset_attempts else "") + ".")
    return 0


def _cmd_retry_all(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Pass --yes to confirm requeueing every 'failed' job.")
        return 1

    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        count = worker_job_repo.retry_all_failed(conn, reset_attempts=args.reset_attempts)

    print(f"Requeued {count} job(s) to 'pending'" + (" (attempts reset)" if args.reset_attempts else "") + ".")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process", help="Process up to N pending jobs, once.")
    process_parser.add_argument("--max-jobs", type=int, default=50, help="Maximum jobs to process this run.")
    process_parser.add_argument(
        "--reclaim-stuck-minutes",
        type=int,
        default=None,
        help="Force a reclaim of jobs stuck 'processing' longer than this many minutes, before processing "
        "(process_pending already does this automatically with a built-in default - use this to override it).",
    )
    process_parser.set_defaults(func=_cmd_process)

    list_pending_parser = subparsers.add_parser("list-pending", help="List pending jobs.")
    list_pending_parser.add_argument("--limit", type=int, default=50)
    list_pending_parser.set_defaults(func=lambda a: _cmd_list(a, "pending"))

    list_failed_parser = subparsers.add_parser("list-failed", help="List terminally-failed jobs.")
    list_failed_parser.add_argument("--limit", type=int, default=50)
    list_failed_parser.set_defaults(func=lambda a: _cmd_list(a, "failed"))

    inspect_parser = subparsers.add_parser("inspect", help="Show full detail for one job, including payload.")
    inspect_parser.add_argument("job_id", type=int)
    inspect_parser.set_defaults(func=_cmd_inspect)

    retry_parser = subparsers.add_parser("retry", help="Requeue one failed job.")
    retry_parser.add_argument("job_id", type=int)
    retry_parser.add_argument("--reset-attempts", action="store_true", help="Also zero the job's attempt count.")
    retry_parser.set_defaults(func=_cmd_retry)

    retry_all_parser = subparsers.add_parser("retry-all", help="Bulk-requeue every failed job.")
    retry_all_parser.add_argument("--reset-attempts", action="store_true", help="Also zero each job's attempt count.")
    retry_all_parser.add_argument("--yes", action="store_true", help="Required to confirm the bulk action.")
    retry_all_parser.set_defaults(func=_cmd_retry_all)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
