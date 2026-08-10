"""Operations Inbox certification CLI.

    python scripts/run_inbox_case.py CASE-000

Loads a fixture from tests/fixtures/operations_inbox/<CASE_ID>/, prepares a
disposable scratch database (schema migration + full table reset), runs the
fixture email through the real intake pipeline twice (once clean, once as an
immediate duplicate), captures actual.json, and prints the required
certification evidence: field diff, every accuracy measurement, duplicate
result, and pass/fail.

Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, disposable
PostgreSQL database. Never reads or falls back to the app's configured
DATABASE_URL - see tests/integration/operations_inbox/harness.py for the
safety gate. Never connects to production.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.integration.operations_inbox.harness import (
    CertificationSafetyError,
    MissingScratchDatabaseError,
    run_case,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_id", help="Fixture case id, e.g. CASE-000")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Scratch database URL. Defaults to INBOX_CERTIFICATION_DATABASE_URL.",
    )
    args = parser.parse_args()

    try:
        report = run_case(args.case_id, database_url=args.database_url)
    except (MissingScratchDatabaseError, CertificationSafetyError) as exc:
        print(f"REFUSED: {exc}")
        return 2
    except FileNotFoundError as exc:
        print(f"FIXTURE ERROR: {exc}")
        return 2

    comparison = report.comparison

    print(f"=== Operations Inbox Certification: {report.case_id} ===")
    print(f"Row count (first run):  {report.row_count_first_run}")
    print(f"Row count (after rerun): {report.row_count_after_rerun}")
    print(f"Duplicate-protection result: {report.duplicate_protection}")
    print()
    print("Accuracy:")
    for key in (
        "classification_accuracy",
        "service_flow_accuracy",
        "critical_field_accuracy",
        "all_field_accuracy",
        "container_count_accuracy",
        "container_number_accuracy",
        "existing_load_match_accuracy",
    ):
        print(f"  {key}: {comparison[key]:.1f}%")
    print(f"  queue_resolution: {comparison['queue_resolution']}")
    print(f"  exact_record_pass: {comparison['exact_record_pass']}")
    print()
    if comparison["diffs"]:
        print("Field differences (expected vs actual):")
        print(json.dumps(comparison["diffs"], indent=2, default=str))
    else:
        print("No field differences.")

    passed = (
        comparison["exact_record_pass"]
        and report.duplicate_protection == "PASS"
    )
    print()
    print("RESULT:", "PASSED" if passed else "NOT PASSED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
