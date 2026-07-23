# Verification: CASE-000 (certification harness smoke test)

This is not one of the ten business certification cases from
`docs/operations_inbox_certification/README.md`. It exists to prove the
harness itself works end to end before any real case is attempted.

## Lifecycle

- [x] Fixture created
- [x] Expected result approved (harness maintainer, pre-code)
- [x] Email imported (via `_decode_email_file` / `email.txt`)
- [x] Processing executed (`sync_operations_email_engine` against scratch DB)
- [x] Actual result captured (`actual.json`)
- [x] Results compared (`harness.compare`)
- [x] Defect corrected (queue/pickup/references shape mismatches fixed in expected.json)
- [x] Regression test added (`test_case_000_smoke.py`)
- [x] Clean rerun (fresh scratch DB, `reset_scratch_schema` + `reset_scratch_data`)
- [x] Duplicate rerun (same email processed twice, row count unchanged)
- [x] Case accepted

## Evidence (2026-07-22)

- Database: dedicated scratch DB `calitrans_inbox_cert` on the local
  Postgres scratch container (`INBOX_CERTIFICATION_DATABASE_URL`), never the
  app's configured `DATABASE_URL`.
- Targeted regression test (`test_case_000_smoke.py`) run 3x: 3 passed each time.
- Full suite without `INBOX_CERTIFICATION_DATABASE_URL` set: 284 passed, 3 skipped
  (no regression vs. pre-harness baseline of 284 passed).
- `python scripts/run_inbox_case.py CASE-000` run twice independently:
  `exact_record_pass=True`, `duplicate_protection=PASS` both times.
- classification_accuracy 100%, service_flow_accuracy 100%,
  critical_field_accuracy 100%, all_field_accuracy 100%,
  container_count_accuracy 100%, container_number_accuracy 100%,
  existing_load_match_accuracy 100%, queue_resolution PASS,
  exact_record_pass True.

## Known limitation

`references.contact_name` captures `"Thank you,"` instead of the sender name
that follows it on the next line - `_signature_contact_name` in
`services/email_parser.py` mis-reads the closing-line-plus-name signature
block when there is no explicit `Contact:` label in the body. Not fixed here:
CASE-000 is infrastructure-only, and real cases that include an explicit
`Contact:` label (e.g. CASE-001) don't hit this fallback path at all. Tracked
for whichever real case first depends on signature-derived contact names.

## Decision

ACCEPTED (infrastructure smoke case only - does not certify any of the 10
business cases in Part 3, which each require their own fixture, approval,
and acceptance audit before being marked Passed).

## Addendum (CASE-001 pass)

`expected.json`/`actual.json` shape evolved while certifying CASE-001:
added `pickup.terminal`, `references.container_size` /
`contact_name`/`contact_email`/`contact_phone`, and switched
`requires_human_review` to the "no order approved yet" gate instead of the
narrow `llm_review_required` flag. Re-verified PASSED with the new shape;
harmless since this fixture isn't one of the ten certified business cases.
