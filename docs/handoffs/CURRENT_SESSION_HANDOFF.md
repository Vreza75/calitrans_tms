# Session Handoff — Operations Inbox Certification (CASE-000 through CASE-010)

Generated at end of session. Read this before doing anything else in this area.

## 1. Current branch and repository state

- Branch: `master` (all work this session committed directly to master — no
  feature branch/worktree was used; user gave explicit consent for this mid-session).
- `git status --short`: clean except one pre-existing untracked directory,
  `.claude/skills/`, which predates this session and was never touched.
- Working tree has **no uncommitted changes**. Every change described below
  is already committed.
- Scratch Postgres container `calitrans-postgres-scratch` (port 5433,
  database `calitrans_inbox_cert`) is running and holds the certification
  schema. It is disposable/local-only — not needed for the app itself, only
  for re-running `scripts/run_inbox_case.py` or the
  `tests/integration/operations_inbox/` suite.

## 2. Work completed

Two threads of work, both finished:

### A. Built the Operations Inbox certification framework and ran all 10 cases

Per `docs/OPERATIONS_INBOX_REQUIREMENTS.md`-adjacent request ("Prompt 8"):
built a repeatable certification harness (`tests/integration/operations_inbox/harness.py`
+ `scripts/run_inbox_case.py`) that runs one fixture email through the real
production intake pipeline (`services.operations_inbox_service.sync_operations_email_engine`)
against a disposable scratch Postgres DB, and compares the result field-by-field
against a hand-approved `expected.json`. Full design/usage docs:
`docs/operations_inbox_certification/README.md` and `CASE_TEMPLATE.md`.

Ran all 10 planned business cases plus one infrastructure smoke case:

| Case | Scenario | Status |
|---|---|---|
| CASE-000 | Harness smoke test (not a business case) | ACCEPTED |
| CASE-001 | New import, single container | ACCEPTED |
| CASE-002 | New export, single container | ACCEPTED |
| CASE-003 | New local import | ACCEPTED |
| CASE-004 | New local export | ACCEPTED |
| CASE-005 | New import with PDF attachment | ACCEPTED |
| CASE-006 | One booking, four containers (RICGX1235800) | ACCEPTED |
| CASE-007 | Container quantity mismatch (4 declared, 3 found) | **ACCEPTED** (see below — was NOT ACCEPTED, then built) |
| CASE-008 | Existing order delivery-date change | ACCEPTED |
| CASE-009 | Delivery address change after driver assignment | ACCEPTED |
| CASE-010 | Two separate orders in one email | **NOT ACCEPTED — real capability gap, not started** |

Every accepted case found and fixed at least one genuine pre-existing defect
in classification/parsing (not just fixture tuning) — full list of the
recurring bug *patterns* (not individual fixes) is in
`docs/CODE_REVIEW_PLAYBOOK.md` §38, written specifically so future work in
this area doesn't reintroduce them. Worth reading before touching
`services/email_parser.py`, `services/operations_inbox_service.py`, or
`services/operations_email_triage_service.py`.

### B. CASE-007 was built via full brainstorm → spec → plan → subagent-driven implementation

CASE-007 originally failed because two capabilities didn't exist: extracting
more than one container number from a message, and detecting/blocking a
declared-vs-found quantity mismatch. This was planned and built properly:

1. Brainstormed with the user → design doc:
   `docs/superpowers/specs/2026-07-24-case-007-container-quantity-mismatch-design.md`
2. Implementation plan (5 bite-sized TDD tasks):
   `docs/superpowers/plans/2026-07-24-case-007-container-quantity-mismatch.md`
3. Executed via `superpowers:subagent-driven-development` — fresh implementer
   subagent per task, task-scoped reviewer per task, then one final
   whole-branch review.
4. Final whole-branch review found 3 Important issues (missing call-site
   coverage, missing precondition gate, a list-value leaking into a
   keyword-search blob as Python repr text) + 2 Minor — all fixed in one
   follow-up commit, re-verified directly by the controller (see §7).

CASE-007 is now genuinely ACCEPTED — `tests/fixtures/operations_inbox/CASE-007/verification.md`
was rewritten from the original "NOT ACCEPTED" audit into a full acceptance audit.

## 3. Files added or changed

**Production code** (`services/`):
- `services/email_parser.py` — many alias additions (Order Number, Pickup
  Address, Local Client, New Delivery Date/Warehouse/Address, Pickup
  Terminal, Export Terminal), a fixed booking-number-from-subject regex, a
  fixed business-name-vs-person-name heuristic, and (CASE-007) `_all_container_numbers`,
  `_container_qty_from_sentence`, `detect_container_quantity_mismatch`, plus
  a new `"Container Numbers"` field on `FIELDS`.
- `services/operations_inbox_service.py` — `contains_any` word-boundary fix,
  narrowed `PORT_ISSUE_TERMS`, an `already_matched_load` guard on
  `enforce_authoritative_booking_triage`, a `sender` passthrough fix in
  `_prepare_operations_email_record`, and (CASE-007)
  `enforce_container_quantity_mismatch_review` wired into all 3 call sites
  of `enforce_authoritative_booking_triage` (lines ~1811, ~3786, ~4263) with
  a request-type precondition gate.
- `services/operations_email_triage_service.py` — `_contains_any`
  word-boundary fix, narrowed `DRIVER_PORT_TERMS`, and (CASE-007) `_lower_blob`
  fixed to flatten list values instead of leaking Python repr syntax.
- `services/operations_attachment_service.py` — `merge_saved_attachment_fields`
  now takes `force=True` on initial merge, with an identity-field carve-out
  (`_ATTACHMENT_MERGE_IDENTITY_FIELDS`) so Contact Name/Email/Phone/Company
  stay fill-blank-only.
- `services/order_parser.py` — `Size` regex widened to keep the HC/FT suffix.

**Test infrastructure** (new):
- `tests/integration/operations_inbox/harness.py` — the certification harness
- `tests/integration/operations_inbox/test_case_00{0..9}_*.py` — permanent
  regression tests, one file per accepted case (CASE-010 has none — nothing
  correct exists yet to lock in)
- `tests/fixtures/operations_inbox/CASE-{000..010}/` — one fixture dir each
  (`case.yaml`, `email.txt`, `expected.json`, `actual.json`, `attachments/`,
  `verification.md`)
- `scripts/run_inbox_case.py` — CLI runner
- `tests/test_container_quantity_mismatch.py` — unit tests for CASE-007's
  new pure functions (22 tests)

**Docs** (new/updated):
- `docs/operations_inbox_certification/README.md`, `CASE_TEMPLATE.md` — framework docs
- `docs/CODE_REVIEW_PLAYBOOK.md` §38 — recurring bug-pattern lessons
- `docs/superpowers/specs/2026-07-24-case-007-container-quantity-mismatch-design.md`
- `docs/superpowers/plans/2026-07-24-case-007-container-quantity-mismatch.md`
- `docs/PROGRESS.md`, `docs/FEATURE_STATUS.md` — created this session (see §12)
- `requirements-dev.txt` — added `PyYAML` (case.yaml parsing)

**Session bookkeeping** (git-ignored, not committed, local only):
- `.superpowers/sdd/progress.md` — task ledger for the subagent-driven CASE-007 build
- `.superpowers/sdd/*.diff`, `task-*-brief.md`, `task-*-report.md` — per-task
  briefs/reports/diffs from the subagent-driven run (safe to delete any time;
  git-ignored scratch, not part of the repo)

## 4. Commits created (chronological, oldest first)

```
b036f67 fix: data_integrity_report checked service_flow on the wrong table          <- pre-existing, before this session
7f51cff feat: add deterministic migration runner and close order_intake schema gap  <- pre-existing, before this session
--- this session starts here ---
e49a03d test: add Operations Inbox certification harness
639cd9d fix: correct new-booking classification and facility-name parsing gaps
242bcf9 test: certify CASE-001 (new import, single container, email body only)
0c60e96 fix: add export pickup/terminal parser fields and pass sender to parser
b0af64a test: certify CASE-002 (new export, single container)
31855a5 fix: recognize Order Number and Pickup Address label variants
456945d test: certify CASE-003 (new local import)
15f4397 fix: stop treating business-name facility values as person names
315ba6e test: certify CASE-004 (new local export)
8a9ecee fix: let PDF attachment fields win over weak email-body guesses
ba0e14f test: certify CASE-005 (new import with PDF attachment)
1986f78 fix: recognize "Local Client" as a Customer label
5a6a3b0 test: certify CASE-006 (one booking with four containers)
9e7461f test: certify CASE-007 (container quantity mismatch) - NOT ACCEPTED
3839abc fix: don't reclassify an existing-load update as a new booking
a34f242 test: certify CASE-008 (existing order delivery-date change)
9987e61 fix: narrow DRIVER_PORT_TERMS to real problem phrasing
d6f8418 test: certify CASE-009 (address change after driver assignment)
d59206e test: certify CASE-010 (two separate orders in one email) - NOT ACCEPTED
e559e2b docs: capture Operations Inbox certification lessons + CASE-007 design
5a11019 docs: add CASE-007 implementation plan
296d495 feat: extract every container number, not just the first
44ff54a feat: recognize stated container quantity in free-text sentences
4e500b1 feat: detect a declared-vs-found container quantity mismatch
6ca8e27 feat: route a container quantity mismatch to the Review queue
839bc32 test: certify CASE-007 (container quantity mismatch)
f4497a1 fix: harden container-quantity-mismatch review pass per final review
```

Current `HEAD` = `f4497a1`. 25 commits this session, all on `master`, none reverted.

## 5. Uncommitted work

**None.** `git status --short` shows only the pre-existing untracked
`.claude/skills/` directory, which is unrelated to this session and was never
staged or touched.

## 6. Validation results (actually executed, this session, at handoff time)

```
$ python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core scripts tests
(no output — clean)

$ unset INBOX_CERTIFICATION_DATABASE_URL; pytest -q
306 passed, 42 skipped in 10.26s
```

The 42 skipped tests are the Operations Inbox certification suite
(`tests/integration/operations_inbox/`), gated behind
`INBOX_CERTIFICATION_DATABASE_URL` — they skip cleanly without it, which is
correct/expected for a normal `pytest -q` run.

With the scratch DB set (`INBOX_CERTIFICATION_DATABASE_URL=postgresql://calitrans_test:calitrans_test_pw_2026@localhost:5433/calitrans_inbox_cert`),
every one of those 42 tests (CASE-000 through CASE-009's regression suites)
was run 3x during the session and passed all 3 times, plus 2 independent
`python scripts/run_inbox_case.py CASE-0NN` CLI runs per case for
determinism. This was verified live against the running scratch container,
not assumed.

`docker ps` confirms `calitrans-postgres-scratch` is `Up 47 hours` at
session end — still running, no action needed to reuse it.

## 7. Known issues

1. **CASE-010 is NOT ACCEPTED — real capability gap, not started.** The
   pipeline always creates exactly one `order_intake` row per email; nothing
   detects or splits multiple distinct booking blocks in one message. See
   `tests/fixtures/operations_inbox/CASE-010/verification.md` for the full
   finding. This needs the same brainstorm → spec → plan → implement cycle
   CASE-007 just went through — do not attempt to patch it in ad hoc.

2. **`_extract_tokens` in `services/operations_email_triage_service.py` still
   has a same-class latent bug the final review didn't catch**: it builds
   `blob = f"{subject or ''}\n{body or ''}\n{parsed}"` — stringifying the
   whole `parsed` dict directly (same pattern as the `_lower_blob` bug that
   *was* fixed this session, one function over). Its consumers are regex
   token-extraction (`BOOKING_RE`/`CONTAINER_RE`/`REFERENCE_RE`.search()),
   not keyword-phrase matching, so it's likely benign today (the dict-repr
   noise doesn't break a specific token regex match) — but it wasn't
   independently verified, and it's worth a real look if
   `services/operations_email_triage_service.py` gets touched again.

3. **Known, accepted (not fixed) limitation across several cases**: `customer`
   often shows `"Example"` (the test fixtures' sender domain,
   `@example.com`) instead of a real customer name, when the email has no
   explicit `Customer:` label and (for update cases) a matched load already
   has the real customer on file. Suggested fix is documented in
   `tests/fixtures/operations_inbox/CASE-008/verification.md`'s "Known
   limitation" section: backfill `Customer`/`Warehouse`/`Address` from the
   matched load before falling back to the sender-domain guess. Not fixed —
   judged out of scope for a certification-only session.

4. `services/order_parser.py`'s `find_pattern` calls for `Customer`/`Port`/
   `Warehouse` still have a missing-comma bug (documented in
   `docs/CODE_REVIEW_PLAYBOOK.md` §38, "A `find_pattern`'s `re.DOTALL` + a
   missing comma is a silent multi-line bug") — deliberately left as-is.
   Fixing the comma alone was tried and tried again during CASE-005 and
   caused a real regression (a previously-correct field went empty) because
   of an interaction with `re.DOTALL`; it needs both the comma and every
   pattern in the same list made line-bounded (`[^\n]+`, not `.+`) at once,
   or not touched at all.

## 8. Decisions made this session

- **Committed directly to `master` throughout**, including CASE-007's
  subagent-driven implementation — explicit user consent given when the
  `subagent-driven-development` skill flagged this as needing sign-off.
- **CASE-007 and CASE-010 both required a real feature, not a bug fix.**
  Decided (with user) to formally brainstorm/spec/plan CASE-007 first,
  build it properly, then circle back to CASE-010 as its own cycle rather
  than rushing either in an ad hoc patch.
- **Reused existing schema/fields for CASE-007's "needs review" state**
  (`llm_review_required`, `work_queue="Review"`, `action_required`) instead
  of adding new columns or enum values — matches how every other
  human-review-required case in this codebase already works, and was an
  explicit user choice during brainstorming (over adding a distinct
  `"Quantity Mismatch"` status).
- **No hard block added to `create_load_from_inbox_item()`** for a detected
  mismatch — flagging/routing only, per explicit user choice during
  brainstorming, consistent with how every other review-required case
  already behaves (nothing in this codebase hard-blocks load creation today).
- **Harness's `pickup`/`delivery`/`dates`/`references` fields are sparse**
  (only populated keys included, via a `_sparse()` helper) rather than a
  fixed schema padded with `null` — decided during CASE-002 so each new
  case's fields don't force reshaping already-accepted cases' `expected.json`.
- **`harness.py`'s `expected.json` load now hard-fails on an empty/missing
  file** instead of silently producing a vacuous 100%-accuracy pass — caught
  during CASE-005's first (accidental) run before its fixture was written.

## 9. Work intentionally deferred

- CASE-010 (two separate orders in one email) — full capability not built;
  see Known Issues #1.
- `_extract_tokens`'s dict-repr leak — see Known Issues #2.
- `customer` field backfill-from-matched-load — see Known Issues #3.
- `services/order_parser.py`'s comma/`DOTALL` bug — see Known Issues #4.
- No UI changes were made anywhere in `pages_app/operations_inbox.py` this
  entire session, by design — every case was certified against the
  automated intake pipeline only, never the dispatcher-facing UI.

## 10. Exact next action for a new Claude session

**Do not start writing code immediately.** CASE-010 needs the same process
CASE-007 just went through:

1. Read `tests/fixtures/operations_inbox/CASE-010/verification.md` in full —
   it already contains the finding, the required capability breakdown, and
   a "What would be required to pass" section that is effectively a design
   sketch.
2. Invoke `superpowers:brainstorming` to turn that sketch into an approved
   design (the user will want to weigh in on: how multiple booking blocks
   are detected, whether each becomes its own `order_intake` row or a
   booking-level draft per block, and how per-block field parsing avoids
   Order 2's fields bleeding into Order 1's — same shape of questions asked
   for CASE-007).
3. Write the design doc to `docs/superpowers/specs/`, get user approval.
4. Invoke `superpowers:writing-plans` to produce
   `docs/superpowers/plans/<date>-case-010-*.md` — bite-sized TDD tasks,
   same format as `docs/superpowers/plans/2026-07-24-case-007-container-quantity-mismatch.md`.
5. Execute via `superpowers:subagent-driven-development` (the user's
   preferred execution mode this session) — fresh implementer + reviewer
   subagent per task, final whole-branch review, fix round if needed.
6. Certify CASE-010 (rewrite its `verification.md` from NOT ACCEPTED to
   ACCEPTED), add its regression test file, run the full acceptance
   protocol (targeted 3x, full suite, two independent CLI runs).

If the user instead wants to address one of the "Known Issues" items
(§7) or "Work intentionally deferred" items (§9) first, those are all
independently actionable — none require CASE-010 to be done first.

## 11. Recommended files a new session should read first

In this order:

1. This file.
2. `tests/fixtures/operations_inbox/CASE-010/verification.md` — the actual
   next task's requirements.
3. `docs/CODE_REVIEW_PLAYBOOK.md` §38 — recurring bug patterns to avoid;
   read before touching `services/email_parser.py`,
   `services/operations_inbox_service.py`, or
   `services/operations_email_triage_service.py` again.
4. `docs/operations_inbox_certification/README.md` and `CASE_TEMPLATE.md` —
   how the certification framework works, how to add/run a case.
5. `docs/superpowers/specs/2026-07-24-case-007-container-quantity-mismatch-design.md`
   and `docs/superpowers/plans/2026-07-24-case-007-container-quantity-mismatch.md`
   — the most recent worked example of the brainstorm→plan→implement cycle
   in this exact area, useful as a template for CASE-010.
6. `.claude/rules/operations-inbox.md` — the project's own standing rules
   for this feature area (classification precedence, multi-container
   requirements, parsing rules) — required reading per `CLAUDE.md` before
   any Operations Inbox code change.
