"""One-time + repeatable cleanup of legacy base64 attachment bodies (PR #19
Stage 2 / Task A blocker 4).

Before Stage 2, attachment bytes were base64-embedded into worker_jobs.payload
and flowed into order_intake.parsed_data. This scrubs those bodies out:

  - worker_jobs: only rows in terminal status 'completed' (NEVER pending/
    processing - an in-flight retry may still need the base64 fallback).
  - order_intake.parsed_data: the persisted attachment metadata, keeping
    references/checksums (storage_key, content_sha256) and dropping the bytes.

Read-modify-write, idempotent, bounded per run, --dry-run supported. Safe to run
repeatedly. Never touches a non-completed job, so it cannot break active retries.

Usage:
    python scripts/scrub_attachment_base64.py --batch 500
    python scripts/scrub_attachment_base64.py --batch 500 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_BYTE_KEYS = ("_content_b64", "content")
_ATTACHMENT_LIST_KEYS = ("_operations_attachments", "_operations_pdf_attachments")


def _strip_attachment_list(items) -> bool:
    changed = False
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                for key in _BYTE_KEYS:
                    if key in item:
                        del item[key]
                        changed = True
    return changed


def _scrub_parsed_data(parsed: dict) -> bool:
    changed = False
    for key in _ATTACHMENT_LIST_KEYS:
        if _strip_attachment_list(parsed.get(key)):
            changed = True
    return changed


def _scrub_job_payload(payload: dict) -> bool:
    changed = _strip_attachment_list(payload.get("pre_saved_attachments"))
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("attachments"), list):
        del message["attachments"]
        changed = True
    return changed


def _coerce(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except Exception:
            return None
    return None


def scrub(*, batch: int, dry_run: bool) -> dict:
    from db_client import execute, get_engine, get_secret, read_df

    # PostgreSQL columns are jsonb (needs a text->jsonb cast on write); SQLite
    # (tests) has no jsonb type and casting to it coerces the JSON to 0, so bind
    # text directly there.
    dialect = getattr(getattr(get_engine(get_secret("DATABASE_URL")), "dialect", None), "name", "")
    jsonb = (lambda p: f"cast({p} as jsonb)") if dialect == "postgresql" else (lambda p: p)

    totals = {"jobs_scanned": 0, "jobs_scrubbed": 0, "intake_scanned": 0, "intake_scrubbed": 0}

    jobs = read_df(
        "select id, payload from worker_jobs "
        "where status = 'completed' and cast(payload as text) like '%content_b64%' "
        "order by id limit :limit",
        {"limit": batch},
    )
    for _, row in jobs.iterrows():
        totals["jobs_scanned"] += 1
        payload = _coerce(row["payload"])
        if payload is None or not _scrub_job_payload(payload):
            continue
        totals["jobs_scrubbed"] += 1
        if not dry_run:
            execute(
                f"update worker_jobs set payload = {jsonb(':payload')} where id = :id",
                {"payload": json.dumps(payload, default=str), "id": int(row["id"])},
            )

    intake = read_df(
        "select id, parsed_data from order_intake "
        "where cast(parsed_data as text) like '%content_b64%' "
        "order by id limit :limit",
        {"limit": batch},
    )
    for _, row in intake.iterrows():
        totals["intake_scanned"] += 1
        parsed = _coerce(row["parsed_data"])
        if parsed is None or not _scrub_parsed_data(parsed):
            continue
        totals["intake_scrubbed"] += 1
        if not dry_run:
            execute(
                f"update order_intake set parsed_data = {jsonb(':parsed')} where id = :id",
                {"parsed": json.dumps(parsed, default=str), "id": int(row["id"])},
            )

    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", type=int, default=500, help="Max rows to scan per table per run.")
    parser.add_argument("--dry-run", action="store_true", help="Count what would be scrubbed; no writes.")
    args = parser.parse_args()
    if args.batch <= 0:
        print("--batch must be positive.", file=sys.stderr)
        return 2

    totals = scrub(batch=args.batch, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "scrubbed"
    print(
        f"base64 scrub {mode}: jobs_scanned={totals['jobs_scanned']} jobs_scrubbed={totals['jobs_scrubbed']} "
        f"intake_scanned={totals['intake_scanned']} intake_scrubbed={totals['intake_scrubbed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
