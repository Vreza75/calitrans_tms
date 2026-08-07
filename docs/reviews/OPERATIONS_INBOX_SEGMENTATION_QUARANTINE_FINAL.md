# Operations Inbox — Segmentation Quarantine Final Correction

Date: 2026-08-06

Starting branch: `fix/operations-inbox-label-block-boundary-correction` @
`69b22f1715f8e81617a479acf17d3c45214e388a`.
Working branch: `fix/operations-inbox-segmentation-quarantine-final`.
Backup branch: `backup/pre-segmentation-quarantine-final-20260806`.

## H1 (blank-line variant) — reproduction

```
Equipment: 40HC

From: Old Customer
Sent: Monday
To: Operations
Subject: Cancellation

Please cancel booking ABC123.
```

Before this pass: the blank-line bridge inside `_scan_label_block` only
ever checked whether the line immediately after a blank was
envelope-*shaped* (`peek_kind == "envelope"`), never whether the run
starting there was itself a coherent, independent envelope. An
already-operational block (`Equipment: 40HC`) therefore silently absorbed
the entire trailing reply envelope and old cancellation body into one
"operational" block, `scope_type` became `new_message`, and the old
cancellation text reached the classifier as active content.

## Blank-line connectivity correction

`_scan_label_block`'s blank-handling now adds one condition: once the
current run has already produced operational evidence
(`has_operational_label`), a blank line only bridges into what follows
when that following run, scanned independently
(`_scan_label_block(lines, peek).kind`), is **not** itself a coherent
envelope. A blank followed by a run that is only envelope-shaped so far
but not yet coherent (`kind in {"none", "operational"}`) still bridges,
preserving existing ambiguity-preserving behavior.

`_find_block_start`'s backward walk previously used a purely permissive
"any label-shaped line, blank-tolerant" rule with no awareness of this new
forward restriction, which would have re-attached the operational line to
the envelope from the other direction and defeated the fix. It now
crosses a blank backward only when a forward scan starting at the
preceding line (`_scan_label_block(lines, back)`) would itself bridge
across that same blank to reach (or pass) the candidate index — reusing
`_scan_label_block` as the single, direction-agnostic source of truth for
block connectivity rather than maintaining two independently-drifting
rules.

## Envelope blank-line regression results

- A still-forming envelope with an internal blank line (no operational
  evidence yet, e.g. `From: Customer\n\nSent: Monday\nTo: Operations\n
  Subject: Rate Request`) is unaffected — still one coherent envelope.
- `Equipment: 40HC\n\nPickup Date: August 10` (two operational sections,
  no envelope) — both remain active, unaffected.
- `From: Houston\nTo: Dallas\n\nEquipment: 40HC` — all active, unaffected.

## Operational blank-line results

All 5 Codex-specified variants (Equipment-first, Booking-Number-first,
Origin/Destination-first, Spanish, no-`Sent` envelope) correctly separate
into `scope_type == "reply"` with the leading operational content as
`classification_text` and the trailing envelope+old-body as
`quoted_text`. Verified directly and in
`tests/test_segmentation_quarantine_final.py`.

## H2 — quarantine reproduction (exact Codex fixture)

```python
_prepare_operations_email_record({
    "subject": "Fwd: X", "from": "customer@example.com", "direction": "inbound",
    "body": (
        "FYI\n\n-----Original Message-----\n"
        "From: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: X\n\n"
        "From: B <b@example.com>\nSent: Tue\nTo: Ops\nSubject: Y\n\n"
        "Booking Number: ABC123\nPlease cancel this booking.\n"
    ),
})
```

## Unsafe pre-fix triage output (measured directly, before any code change)

```
request_type:      Booking Update
confidence_score:  78
work_queue:        Existing Loads
should_open_case:  True
tokens:            {"booking_number": "NUMBER", "reference_number": "NUMBER", ...}
```

`latest_body` was already correctly empty (prior pass's M1 fix), but
`parsed["_segmentation_collapsed_raw_body"]` (the audit-only raw body,
added to `parsed` for display) was still passed whole into `parsed` for
classification/triage, and roughly a dozen call sites across
`operations_inbox_service.py`, `operations_email_triage_service.py`, and
`operations_case_service.py` built a scan blob as `f"...{parsed}"` or
`str(parsed)` / `json.dumps(parsed)` — Python's dict repr/JSON dump
includes every key *and* value, so the audit text re-entered
classification through `parsed` even with `body` itself safely empty.

Independently, the synthetic `"NUMBER"` token was **not** the audit body —
it came from the parser's own field key. `str({"Booking Number": ""})`
contains the literal substring `"Booking Number"`, and
`\b(?:booking)...(?:number)?...([A-Z0-9]{5,})\b` (case-insensitive) can
match the word **"Number"** itself as the captured value once the optional
`number` literal is skipped by backtracking — a field's own key name can
satisfy the very regex meant to extract a value from free text, with or
without any collapse at all.

## Trusted classification projection

No new parallel "safe view" type was introduced. Two functions, reused
everywhere:

- `services/operations_email_triage_service.py::sanitize_parsed_for_classification(parsed)`
  — drops every key starting with `_` (every audit/diagnostic namespace in
  this codebase uses a leading underscore; every real parser field name
  does not).
- `services/operations_email_triage_service.py::flatten_parsed_values_for_scan(parsed)`
  — sanitizes, then renders **values only** (recursing into nested
  dicts/lists), never `str(dict)`/an f-string of the dict itself. This is
  what actually closes the key-name self-matching defect, independent of
  segmentation status.

`services/operations_inbox_service.py::coerce_parsed_for_classification`
— the pre-existing choke point already sitting at the top of
`operations_intent_scores`, `classify_customer_request`,
`build_operations_email_classification`, and (now)
`operations_classification_for_review` — was changed to always return
`sanitize_parsed_for_classification(...)`'s result instead of the raw
dict. Because every one of those four functions reassigns its own
`parsed` local to this return value, every function they call afterward
(`has_reference_details`, `has_quote_details`, `has_new_order_details`,
`is_booking_confirmation`, `action_required_for_request`,
`classification_confidence`, `find_matching_load`,
`find_load_match_candidates`) automatically receives only the sanitized
dict — one fix, four entry points, no new parallel path.

`services/operations_inbox_service.py::operations_parsed_for_row(row)` —
the equivalent choke point for row/DB-sourced consumers
(`operations_reference_tokens_for_row`,
`effective_operations_request_type_for_row`, two others), and two direct
`coerce_json_dict(row.get("parsed_data"))` call sites
(`row_conversation_join_key`, `timeline_filter_tokens`) were switched to
call it instead of duplicating the raw fetch.

## Parsed-data sanitizer / audit-key exclusion

Every one of the following blob-building call sites was changed from
interpolating `parsed` directly to `flatten_parsed_values_for_scan(parsed)`:
`operations_inbox_service.py` (`row_conversation_join_key`,
`timeline_filter_tokens`, `operations_intent_scores`,
`classify_customer_request`, `action_required_for_request`,
`build_operations_email_classification`,
`operations_classification_for_review`,
`effective_operations_request_type_for_row`,
`operations_reference_tokens_for_row`, `_operations_ai_rule_context`,
`_default_operations_reply_body`), `operations_case_service.py`
(`case_identity_values`), and `pages_app/operations_inbox.py` (4 call
sites, including two that previously used `json.dumps(parsed,
default=str)` — same defect, JSON serialization also includes key names).
`filter_operations_timeline_for_record`'s row-matching haystack was
switched from `safe_str(row.get("parsed_data", ""))` to
`flatten_parsed_values_for_scan(operations_parsed_for_row(row))`.

Two remaining `json.dumps(parsed, default=str)` call sites in
`pages_app/operations_inbox.py` (building the `parsed_data` column value
for a DB write) were left unchanged — that is the correct, intended place
for the full dict including audit keys to be serialized; the invariant is
about classification/token/matching *inputs*, not the storage column
itself.

## Token-extraction correction

`_extract_tokens` (`operations_email_triage_service.py`) now builds its
regex-scan blob via `flatten_parsed_values_for_scan`, closing both the
audit-leak and the key-name self-match defect in the one function that
runs during every fast-triage call.

## Non-OK segmentation policy

New `services/operations_email_triage_service.py::apply_segmentation_safety_policy(triage, segmentation_status)`,
called once, in `_prepare_operations_email_record`, immediately after
existing triage post-processing:

- `segmentation_status == "collapsed"`: `should_open_case = False`,
  `llm_required = True`, confidence capped at 40, `work_level` forced to
  `REVIEW_LEVEL`, `work_queue = "Review"`, `department_lane = "Human
  Review"` — regardless of what fast-triage's keyword rules matched
  against the subject line alone.
- `segmentation_status == "depth_limit_reached"`: `should_open_case =
  False`, `llm_required = True`, confidence capped at 55; `request_type`/
  `work_queue` are left as computed, since real (if possibly incomplete)
  nested content was preserved and can still usefully inform a
  dispatcher.

## Final collapsed routing result (measured, after fix)

```
request_type:      Customer Request
confidence_score:  40
work_queue:        Review
should_open_case:  False
llm_required:      True
tokens:            {"booking_number": "", "container_number": "", "reference_number": ""}
matched_load_id:   None
```

## Case-eligibility result

`should_open_case` is forced `False` for any non-`"ok"` segmentation
status, centrally, in `apply_segmentation_safety_policy` — not left to
confidence-threshold side effects.

## Persistence safety

Unchanged from the prior pass: `_segmentation_collapsed_raw_body` remains
in `parsed_data` (audit/display only), Booking Number stays blank unless
a trusted attachment supplies it, `_needs_review`/`_confidence` are set by
the existing `derive_review_state` plumbing.

## M1 — lane-detector correction

New `services/message_scope.py::non_envelope_label_blocks(raw_text)` —
returns the text of every maximal contiguous label-shaped block that is
**not** a coherent email envelope (`_scan_label_block`'s own `kind !=
"envelope"`), reusing `_find_block_start`/`_scan_label_block` so it can
never disagree with how the rest of the module divides text into blocks.

`operations_inbox_service.py::_has_plausible_quote_lane` now searches for
a `From:`/`To:` pair independently **within each** `non_envelope_label_
blocks` result, never across the whole scoped text. This closes two
things at once: two unrelated blocks separated by prose/blank boundaries
can never cross-pair, and a genuine coherent envelope (3+ envelope
labels) is excluded by **block kind**, not merely subjected to the same
word-plausibility filter operational values get (verified with
`From: John Smith\nTo: Jane Doe\nSubject: Rate Request` — plausible-looking
names that would have passed the old filter, correctly excluded now by
block kind alone).

## Same-block / cross-section lane results

| Case | Result |
|---|---|
| `From: Houston\nEquipment: 40HC\nTo: Dallas` (same block) | lane found |
| `From: Houston\n\nTo: Dallas` (bare pair, one blank) | lane found (documented: a bare 2-label pair stays one "none"-kind block, consistent with message_scope's own default) |
| `From: Houston\n\nUnrelated instructions here.\n\nTo: Dallas` | no lane |
| `From: Customer Service\nTo: Operations\nSubject: Rate Request` | no lane (envelope) |
| `From: John Smith\nTo: Jane Doe\nSubject: Rate Request` | no lane (envelope, plausible names) |
| Two separate order blocks, one holding `From:`, the other `To:` | no lane |
| Current `From:` + quoted-history `To:` (behind a wrote-marker) | no lane |
| `Please quote this.\nFrom: Houston\nEquipment: 40HC\n\nUnrelated recipient section:\nTo: Dallas` (Codex M1 exact case) | no lane |

Pre-existing, unaffected either direction: `De: Houston\nA: Dallas\nEquipo:
40HC` — bare `A:` (Spanish "to") was never recognized by
`_TO_LABEL_LINE_RE` (which only matches `to`/`para`) before this pass and
still isn't; confirmed identical (`False`) before and after this change.
Not fixed here — a real but separate enhancement, out of this pass's
scope.

## Real attachment-plus-collapse test (L1)

`tests/test_label_block_boundary_correction.py::test_ambiguous_body_with_valid_attachment_still_flags_collapse_for_body`
was rewritten to supply a deterministic attachment (only the disk-writing
`save_operations_attachment` primitive is stubbed; `_save_operations_
email_attachments` and the real `merge_saved_attachment_fields`
reconciliation logic run unmodified). Body segmentation stays `collapsed`
while the attachment's `Booking Number: ATTACH123` / `Container Number:
MSCU1234567` merge in with intact provenance
(`parsed["_operations_attachments"][0]["filename"]`), the body's own
`ABC123` (present only in `_segmentation_collapsed_raw_body`) never
becomes the trusted value, and `should_open_case`/`llm_required`/
`_needs_review` all reflect the conservative collapsed policy regardless
of attachment success.

## Attachment provenance / conflict handling result

New `test_conflicting_body_and_attachment_booking_numbers_require_review`:
an unambiguous body value (`ABC123`) and a differing attachment value
(`XYZ789`) for the same field are recorded in
`parsed["_reconciliation"]["conflicts"]` (`reconcile_parsed_sources`'s
existing "Review mismatch" status) and force `_needs_review = True` — the
document/attachment value wins per documented parsing precedence, but the
conflict is never silently discarded.

## Red-team boundary/quarantine probes

10 fresh probes in `tests/test_segmentation_quarantine_final.py`
(`On ... wrote:`/`escribió:` markers, signature-then-envelope, prose
wrapper before an envelope, 5 quarantine probes placing operational-
looking values inside `_parser_failures`/`_review_reasons`/
`_operations_attachments`/`_email_sync_errors`/`_candidate_conflicts`).
All behaved correctly on first run — none leaked into `classification_
text` or `flatten_parsed_values_for_scan`'s output.

## Test count reconciliation

| Suite | Before this pass | After this pass |
|---|---|---|
| `test_label_block_boundary_correction.py` | 117 | 118 (1 placeholder test replaced + 1 new conflict test) |
| `test_segmentation_quarantine_final.py` (new) | 0 | 36 |
| Focused 4-file baseline (message-scope family) | 236 | 237 |
| Focused + new file | — | 273 |
| Certification | 49 | 49 (unchanged) |
| Full pytest (with disposable DB) | 864 | 901 (864 + 36 new file + 1 net change) |

## Manual UI status

Not performed. No browser available in this environment.

## Test-DB isolation gap — found and fixed in this same branch

Running a new test that queries `find_matching_load` without
`DATABASE_URL` pointed at the disposable test database resulted in a real
**read-only** query against the live Supabase instance configured in
`.streamlit/secrets.toml` (a genuine match was returned for an
incidentally-real container number). No write occurred —
`_prepare_operations_email_record` is DB-write-free by design, and the
attachment-saving primitive was stubbed in the affected test.

Investigation found the gap was worse than one query: `config.get_secret
("DATABASE_URL")` checks Streamlit's own `st.secrets` (reads
`.streamlit/secrets.toml` directly, even outside a Streamlit runtime)
*before* checking `os.environ`, and `config.py`'s own module-level
`_load_local_env_file()` unconditionally copies `.env`'s `DATABASE_URL`
(also production, in this repo) into `os.environ` at first import. Both
files hold the same real production URL. This meant the `export
DATABASE_URL=<disposable-url> && pytest -q` pattern used for the "full
pytest with disposable DB" runs earlier in this document's own history
(and in every prior session's final report claiming isolation) **did
not** actually isolate ordinary, non-harness test code from production —
only `tests/test_migration_runner.py` and
`tests/integration/operations_inbox/harness.py` were genuinely isolated,
since both read `MIGRATION_TEST_DATABASE_URL`/
`INBOX_CERTIFICATION_DATABASE_URL` directly (`harness.py`'s
`scratch_database()` context manager patches `db_client.get_secret`
directly, scoped per case), bypassing `config.py` entirely.

Fixed with a new root `conftest.py` (`pytest_configure` hook,
session-wide): neutralizes `get_streamlit_secret`/
`_read_local_streamlit_secret`/`_read_local_env_secret` (all three
fallbacks, for every secret key, not just `DATABASE_URL`) and strips a
`.env`-sourced `DATABASE_URL` back out of `os.environ` unless the caller
had explicitly set it themselves before pytest started. Deliberately does
**not** set or mirror `DATABASE_URL` from
`MIGRATION_TEST_DATABASE_URL`/`INBOX_CERTIFICATION_DATABASE_URL` — the
certification harness's own `require_scratch_database_url` already
refuses to run if `INBOX_CERTIFICATION_DATABASE_URL` equals the app's
configured `DATABASE_URL`, specifically to catch this class of
misconfiguration, and mirroring the URL would have tripped that check.

Verified: with no test-DB env vars set, `config.get_secret("DATABASE_URL")`
now returns `None` and `db_client.get_engine()` raises `RuntimeError`
instead of silently connecting — full suite still 848 passed/53 skipped
(unchanged), proving no test secretly depended on real production data.
With `MIGRATION_TEST_DATABASE_URL`/`INBOX_CERTIFICATION_DATABASE_URL` set,
certification remains 49/49 and full suite is 901 passed, now **genuinely**
isolated rather than only appearing to be. New regression coverage:
`tests/test_conftest_database_isolation.py`.

## Remaining known risks

- Bare Spanish `A:` (vs. `Para:`) as a "to" label is not recognized by
  `_has_plausible_quote_lane` — pre-existing, unchanged.
- All remaining known risks from
  `docs/reviews/OPERATIONS_INBOX_LABEL_BLOCK_BOUNDARY_CORRECTION.md`
  (operational-label-interrupting-envelope red-team findings, two-envelope
  no-separator RT-6, booking-confirmation scope, reference-token scope,
  Full Return editor, `str(parsed)` rescanning in `order_parser.py`,
  incomplete backend-boundary migration, no dispatcher-confirmation
  provenance, conservative administrative-phrase list, person-name lane
  false positive) are unchanged by this pass.
- Test-DB isolation gap — fixed in this same branch, see above (root
  `conftest.py`).
