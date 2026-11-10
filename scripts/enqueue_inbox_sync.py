"""Scheduled producer for the Operations Inbox: enqueues one inbox.sync
worker job for the unattended sync cadence, and reports sanitized sync
health. The standalone, Streamlit-free counterpart of the dispatcher's
"Sync Email Engine" button - it reuses the exact same job type and the
exact same downstream fetch/dedupe/process/retry pipeline
(application/inbox/commands.py::request_scheduled_inbox_sync ->
handle_inbox_sync). It never fetches mail, sends email, creates loads, or
changes assignments itself; it only enqueues the job.

Run `enqueue` periodically (e.g. the process-jobs GitHub Actions workflow,
cron, or Task Scheduler); the existing `scripts/process_worker_jobs.py
process` step then claims and runs the job. Needs only DATABASE_URL - no
mailbox credentials (those belong to the processing step that actually
fetches).

Usage:
    python scripts/enqueue_inbox_sync.py enqueue    # enqueue one inbox.sync (idempotent, overlap-guarded)
    python scripts/enqueue_inbox_sync.py status      # print sanitized inbox.sync health

Exit codes:
    enqueue: 0 when a job was queued OR deliberately skipped (overlap/idempotency);
             1 only on an actual failure (so a scheduler/monitor can alert).
    status:  0 always (informational read).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _cmd_enqueue(_args: argparse.Namespace) -> int:
    from application.inbox.commands import request_scheduled_inbox_sync

    result = request_scheduled_inbox_sync()

    if result.status == "queued":
        print(f"inbox.sync queued (job_id={result.job_id}).")
    elif result.status == "skipped":
        print(f"inbox.sync skipped: {result.reason}")
    else:
        print(f"inbox.sync result: status={result.status!r} job_id={result.job_id} reason={result.reason!r}")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    from application.inbox.queries import get_inbox_sync_health

    health = get_inbox_sync_health()
    print(
        "inbox.sync health: "
        f"pending={health.pending} processing={health.processing} "
        f"completed={health.completed} failed={health.failed}"
    )
    print(f"  last_success_at={health.last_success_at}")
    print(f"  last_duration_seconds={health.last_duration_seconds}")
    print(f"  last_error={health.last_error!r}")
    print(f"  checked_at={health.checked_at}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = subparsers.add_parser("enqueue", help="Enqueue one inbox.sync job (idempotent, overlap-guarded).")
    enqueue_parser.set_defaults(func=_cmd_enqueue)

    status_parser = subparsers.add_parser("status", help="Print sanitized inbox.sync health.")
    status_parser.set_defaults(func=_cmd_status)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - a producer failure must surface a non-zero code, sanitized
        from utils.error_sanitizer import sanitize_exception_message

        print(f"inbox.sync {args.command} failed: {sanitize_exception_message(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
