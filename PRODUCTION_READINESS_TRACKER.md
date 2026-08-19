# Calitrans TMS — Production Readiness Tracker

This is the single source of truth for remediation work following the full review of `feat/operations-inbox-web` at `5faaf0f8007ac0f1e3073761d57282ed7e1a73ce`.

## Status key

- `[ ] Not started`
- `[-] In progress`
- `[?] Blocked`
- `[R] Ready for review`
- `[V] Verified`
- `[x] Complete`

An implementation is not complete merely because code was written. Use `[R]` after implementation and automated validation, `[V]` after independent review/retest, and `[x]` only when all acceptance criteria and required operational actions are satisfied.

## Rules

- Work on one package at a time.
- Use one branch and preferably one PR per package.
- Record the starting and ending commit.
- Link every package to its finding, test evidence, and PR/issue.
- Do not mark external/manual actions complete without evidence.
- Add newly discovered work under “New findings”; do not silently expand an active package.
- Update this file in every remediation PR.
- Never record secrets in this tracker.

## Program summary

| Package | Finding | Priority | Status | Owner | Branch/PR | Evidence |
|---|---|---:|---|---|---|---|
| PR-00 Credential incident response | Exposed credential in traceback | P1 | `[ ] Not started` | Victor/provider admin | — | — |
| PR-01 Database test isolation | CTMS-001 | P1 | `[x] Complete` | Vreza75 | PR #13 @ `decfaad` (merged into `feat/operations-inbox-web`) | Full suite post-merge: 1391 passed, 98 skipped, 0 failed |
| PR-02 Disposable PostgreSQL certification | Certification gap | P1 | `[?] Blocked` | — | — | Needs approved disposable DB |
| PR-03 Upload and parser hardening | CTMS-002 | P1 | `[ ] Not started` | — | — | — |
| PR-04 Browser and Streamlit session hardening | CTMS-003 | P1 | `[ ] Not started` | — | — | — |
| PR-05 Database lifecycle constraints | CTMS-005 | P2 | `[ ] Not started` | — | — | — |
| PR-06 SMS/outbox idempotency | CTMS-004 | P2 | `[ ] Not started` | — | — | — |
| PR-07 Mandatory PR CI | CTMS-008 | P2 | `[ ] Not started` | — | — | — |
| PR-08 Persistent workers and recovery | CTMS-006 | P2 | `[ ] Not started` | — | — | — |
| PR-09 Canonical path and Streamlit retirement | CTMS-007 | P2 | `[ ] Not started` | — | — | — |
| PR-10 Documentation/configuration cleanup | CTMS-009 | P3 | `[ ] Not started` | — | — | — |

## Recommended order

1. PR-00 — Rotate and investigate the exposed credential.
2. PR-01 — Review, commit, and protect database test isolation.
3. PR-02 — Certify migrations and sample data on disposable PostgreSQL.
4. PR-03 — Harden uploads and parsers.
5. PR-04 — Harden sessions.
6. PR-05 — Add database lifecycle constraints.
7. PR-06 — Harden SMS/outbox idempotency.
8. PR-07 — Add required PR CI.
9. Decide hosting topology, then perform PR-08.
10. Execute PR-09 incrementally.
11. Complete PR-10 alongside or immediately after affected packages.

## PR-00 — Credential incident response

Status: `[ ] Not started`

- [ ] Identify the exposed credential without reproducing it in an issue or log.
- [ ] Rotate/revoke it in the database provider.
- [ ] Confirm the old credential no longer authenticates.
- [ ] Review available access/audit logs for unexpected use.
- [ ] Search tracked files and Git history for accidental secret inclusion.
- [ ] Confirm current local and hosted environments use the replacement secret.
- [ ] Record the date, operator, and sanitized evidence.
- [ ] Add a secret-scanning prevention step if absent.

Evidence/notes:

- Owner:
- Date:
- Issue/PR:
- Evidence:
- Remaining risk:

## PR-01 — Database test isolation

Status: `[x] Complete`

- [x] Remove inherited `DATABASE_URL` during ordinary pytest runs.
- [x] Prevent dotenv from repopulating database configuration in tests.
- [x] Neutralize local secret resolution during tests.
- [x] Require `MIGRATION_TEST_DATABASE_URL` for schema/migration integration tests.
- [x] Update auth tests so they do not incidentally contact a database.
- [x] Run focused isolation/auth tests: 39 passed during the audit (32 passed on re-run this session across `test_conftest_database_isolation.py` + `test_api_auth.py`; the other 7 of the original 39 were re-attributed to schema tests, see below).
- [x] Review the local diff specifically for completeness and bypasses.
- [x] Add/confirm production-like target refusal tests.
- [x] Confirm database URLs are sanitized from failures and tracebacks.
- [x] Run the broader safe backend suite after final changes.
- [x] Commit the package on a dedicated branch.
- [x] Independent review/retest (see "Independent review" below).
- [x] Human-approved merge: GitHub PR #13 merged by Vreza75.
- [x] Post-merge validation on `feat/operations-inbox-web`.

Root cause: `config.get_secret()` resolved Streamlit secrets and local `.env`/`.streamlit/secrets.toml` fallbacks *before* honoring a caller-set `DATABASE_URL`, and `config.py`'s own module-level `_load_local_env_file()` copied `.env` straight into `os.environ` at import time. Both files hold this repository's real database URL, so an unmocked `db_client` call during a normal test run silently reached the real dev/prod-configured database instead of failing. The version already committed at `5faaf0f` closed three of the four vectors but still special-cased a caller-set `DATABASE_URL` (leaving it live if the invoking shell happened to have one exported) and never cleared the `config.DATABASE_URL` module attribute itself.

Intended behavior: no pytest run — regardless of what the parent shell, `.env`, or `.streamlit/secrets.toml` contain — can ever resolve `DATABASE_URL` to a real value. Database-dependent suites must opt in explicitly and positively via `MIGRATION_TEST_DATABASE_URL` / `INBOX_CERTIFICATION_DATABASE_URL`, which are never read implicitly.

Security/authorization impact: prevents automated test runs from silently reading or (in schema/DDL-adjacent tests) writing to a real database; no change to production authorization logic itself. `tests/test_api_auth.py`'s dev-mode tests now call `api.auth.require_auth()` directly instead of round-tripping through `TestClient`, removing an incidental DB dependency without changing what they assert.

Database/migration impact: none — pytest-only change, no schema or migration files touched.

Backward-compatibility impact: a developer who previously relied on an exported `DATABASE_URL` being honored by plain `pytest -q` will now see `RuntimeError("DATABASE_URL is missing...")` for any test that touches `db_client.get_engine()` unless they explicitly use `MIGRATION_TEST_DATABASE_URL`/`INBOX_CERTIFICATION_DATABASE_URL` instead — this is the intended fail-closed behavior, not a defect.

Verification performed this session (see Files changed / Tests below for exact commands and counts):

1. Read `conftest.py`'s working-tree diff line by line against the committed `5faaf0f` version and confirmed the remaining gap (caller-set `DATABASE_URL` bypass, uncleared `config.DATABASE_URL`) is fully closed, with no new bypass introduced.
2. Confirmed empirically that `config.DATABASE_URL` and `config.get_secret("DATABASE_URL")` are never referenced anywhere else in the codebase by direct attribute access (only through `get_secret()`), so the residual leak vector had no other call site to worry about.
3. Confirmed `.streamlit/secrets.toml` exists in this working tree (so the streamlit-secrets fallback path is live and meaningfully exercised by this guard); `.env` does not currently exist here.
4. Added a regression test proving `MIGRATION_TEST_DATABASE_URL`/`INBOX_CERTIFICATION_DATABASE_URL` being set does not widen what `db_client.get_engine()` resolves for unrelated tests (`test_migration_test_database_url_never_leaks_into_the_app_database_url`).
5. Added a regression test empirically pinning that a failed database connection through `db_client.get_engine()` never echoes the username/password back in the raised exception (`test_connection_failures_do_not_leak_credentials_in_the_raised_error`), and independently reproduced the same result directly against this repo's pinned SQLAlchemy/psycopg2 versions outside pytest (connection-refused and malformed-URL cases both omit credentials).
6. Confirmed `utils/error_sanitizer.py` (the repository's canonical DSN/credential redactor, already used by `db_client.check_schema_readiness()` and the `application`/`api` layers) has its own full test coverage in `tests/test_error_sanitizer.py`, providing defense-in-depth for any wrapped-error path this change doesn't directly touch.

Evidence/notes:

- Starting commit: `5faaf0f8007ac0f1e3073761d57282ed7e1a73ce`
- Branch commits: `f22984b` (PR-01 code), `a85d48e` (tracker), `859db96` (CTMS-010 fix), `b41886d` (verification/tracker) on `fix/pr-01-database-test-isolation`
- **Merge commit: `decfaad5d86ae2937d0afa3dcb01703c05503e0b`** — GitHub PR #13, merged into `feat/operations-inbox-web` (merge type: merge commit, branch not deleted)
- Merge date: 2026-08-19T17:57:38Z
- Merged by: Vreza75 (human-approved; explicit merge instruction given directly by the repository owner in this session)
- `feat/operations-inbox-web` local branch fast-forwarded `5faaf0f` → `decfaad` to match `origin/feat/operations-inbox-web` and re-validated post-merge (see "Post-merge validation" below)
- Files changed: `conftest.py`, `tests/test_api_auth.py`, `tests/test_communications_schema.py`, `tests/test_conftest_database_isolation.py`, `tests/test_db_client_column_exists.py`, `tests/integration/operations_inbox/harness.py`, `tests/integration/operations_inbox/test_harness_safety.py` (new), `docs/operations_inbox_certification/README.md`, `PRODUCTION_READINESS_TRACKER.md` (new)
- Commands run and results:
  - `python -m compileall -q conftest.py tests/test_api_auth.py tests/test_communications_schema.py tests/test_conftest_database_isolation.py tests/test_db_client_column_exists.py api/auth.py config.py db_client.py` → no output, exit 0.
  - `python -m pytest -q tests/test_conftest_database_isolation.py tests/test_api_auth.py` → `32 passed, 1 warning`.
  - `python -m pytest -q tests/test_db_client_column_exists.py tests/test_communications_schema.py -v` → `5 skipped` (correctly skip without `MIGRATION_TEST_DATABASE_URL`; this is the exact CTMS-001 behavior being closed for these two files).
  - `python -m pytest -q` (full backend suite) → `1375 passed, 98 skipped, 0 failed` before adding the two new regression tests; `1377 passed, 98 skipped, 0 failed` after; `1391 passed, 98 skipped, 0 failed` after the CTMS-010 fix + 14 new harness-safety tests.
  - `git diff --check` (both against the prior commit and against baseline `5faaf0f`) → exit 0, no whitespace/conflict-marker issues (only benign CRLF-on-checkout warnings).
  - Manual reproduction outside pytest: `sqlalchemy.create_engine("postgresql://tms_app:Sup3rS3cret!@127.0.0.1:1/doesnotexist", ...).connect()` and a malformed-scheme URL — neither leaked the username or password in the raised exception.
  - `python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core api application scripts tests` → exit 0.
  - `python -m pytest -q tests/integration/operations_inbox/` → `14 passed, 49 skipped` (new harness-safety tests pass; every real-DB certification case still correctly skips with no `INBOX_CERTIFICATION_DATABASE_URL` set — no database was contacted during this review).
- Issue/PR: GitHub PR #13 (`fix/pr-01-database-test-isolation` → `feat/operations-inbox-web`), state `MERGED`.
- Reviewer: independent review performed pre-merge (see "Independent review" below); merge itself explicitly authorized and executed by Vreza75 (repository owner) in this session.
- Remaining risk: none known for the files this package touched. CTMS-010 (see below) is resolved as part of this package's closure, not left open.

### Post-merge validation — 2026-08-19

- `git fetch origin` → confirmed `origin/feat/operations-inbox-web` tip is `decfaad`, a merge commit with `5faaf0f` and `b41886d` as parents (PR-01's four commits are ancestors of the base branch).
- Local `feat/operations-inbox-web` fast-forwarded `5faaf0f` → `decfaad` (`git merge --ff-only origin/feat/operations-inbox-web`) — no local divergence, no manual conflict resolution needed.
- `python -m pytest -q` on the merged `feat/operations-inbox-web` (commit `decfaad`) → `1391 passed, 98 skipped, 0 failed`, matching the pre-merge branch evidence exactly.
- Merge state independently confirmed two ways: `gh api`/`gh pr view 13` (`state: MERGED`, `mergedBy: Vreza75`, `mergeCommit.oid: decfaad...`) and `git log origin/feat/operations-inbox-web` (merge commit present, PR-01 commits are ancestors) — not accepted on the user's word alone.

### Independent review — 2026-08-18

Reviewed `fix/pr-01-database-test-isolation` (`f22984b`, `a85d48e`) independently against baseline `5faaf0f` — diff, behavior, and test evidence re-derived and re-run rather than accepted from the prior implementation report.

Verified directly (see commands above and CTMS-010 section below):
- Ambient `DATABASE_URL` exported in the parent shell before invoking pytest cannot leak in (empirically reproduced with a synthetic production-shaped URL — test still resolved `None`).
- `dotenv.load_dotenv` cannot restore `DATABASE_URL` during collection or execution — confirmed via a throwaway check calling `dotenv.load_dotenv()` mid-session and via code trace of `services/email_client.py`, `services/operations_ai.py`, `services/port_houston_client.py` (each does its own module-level `from dotenv import load_dotenv` + call) — none can execute before `conftest.py`'s `pytest_configure` patches `dotenv.load_dotenv`, since there is exactly one `conftest.py` in this repository and `pytest_configure` always runs before collection.
- Streamlit/local secret resolution neutralized — confirmed empirically (`get_streamlit_secret`, `_read_local_streamlit_secret`, `_read_local_env_secret` all return `None`) against a working tree where `.streamlit/secrets.toml` genuinely exists.
- `config.DATABASE_URL` cleared after import and confirmed to have no other read site in the codebase.
- `MIGRATION_TEST_DATABASE_URL` (and `INBOX_CERTIFICATION_DATABASE_URL`) confirmed not to leak into `config.get_secret("DATABASE_URL")` — empirically, not only via the new regression test.
- Migration/schema-dependent tests skip cleanly without explicit disposable configuration; no database was contacted anywhere in this review.
- `get_engine()`'s `RuntimeError` and a live connection-refused `OperationalError` against a credentialed bogus URL both omit the username/password.
- Confirmed `tests/test_api_auth.py` still exercises the real HTTP/dependency-injection stack (`TestClient`, role checks, 401s) in 27+ tests; only the 2 dev-mode-specific tests were narrowed to a direct `require_auth()` call, and that narrowing does not remove role/authorization coverage.
- Confirmed conftest.py's pytest-only hooks (`pytest_configure`/`pytest_runtest_setup`) cannot execute outside a pytest session, so no application runtime behavior (Streamlit, FastAPI, workers) is affected by this package.

### CTMS-010 determination and resolution

Confirmed: `tests/integration/operations_inbox/harness.py::require_scratch_database_url()`'s original safety control — rejecting a target only if it was byte-identical to `config.get_secret("DATABASE_URL")` — is structurally unreachable under PR-01's own `conftest.py` guard, because that call returns `None` for the entire pytest session and every caller of `require_scratch_database_url()` is a pytest test. This was not newly introduced by PR-01 (it was already unreliable in the common no-exported-`DATABASE_URL` case before PR-01), but PR-01 makes it permanently, unconditionally inert rather than situationally weak.

Fix implemented as part of this package's closure (commit `859db96`): replaced the equality check with a parse-and-validate model reusing `scripts/sample_data.py`'s `assert_safe_target()` convention (not a competing policy) —
1. Reads only `INBOX_CERTIFICATION_DATABASE_URL` (or an explicit test-provided URL); never falls back to `config.get_secret("DATABASE_URL")`.
2. No longer compares against `config.get_secret("DATABASE_URL")` at all.
3. Parses the URL with `sqlalchemy.engine.make_url` rather than trusting the raw string.
4. Requires the database name to contain an explicit `dev`/`test`/`qa`/`sandbox`/`scratch`/`disposable`/`cert`/`certification`/`ci` marker.
5. Rejects `postgres`/`production`/`prod` outright, and rejects any name without a safe-marker token.
6. Requires a second env var, `INBOX_CERTIFICATION_DATABASE_IDENTITY`, to exactly acknowledge the sanitized `host[:port]/database` identity before any destructive test runs (mirrors sample-data's `CALITRANS_QA_DATABASE_IDENTITY`).
7. Fails closed (raises) on missing URL, unparseable URL, unsafe name, or missing/mismatched identity acknowledgement.
8. Never includes the raw URL, username, or password in any raised message — regression-tested.

Regression tests added: `tests/integration/operations_inbox/test_harness_safety.py` (14 tests, no database connection) — missing URL, malformed URL, production-like name, generic/ambiguous name (`calitrans`), bare `postgres`, safe name without identity acknowledgement, identity mismatch, accepted disposable identity (case/whitespace-insensitive), env-var-only invocation, and credential-leak checks across every refusal path (parametrized).

Documentation corrected for accuracy: `conftest.py`'s docstring and `docs/operations_inbox_certification/README.md` both described the old (now-removed) equality check; both updated to describe the new model, and the README's example `export` commands now include the required `INBOX_CERTIFICATION_DATABASE_IDENTITY`.

**CTMS-010 status: Resolved.**

## PR-02 — Disposable PostgreSQL certification

Status: `[?] Blocked`

- [ ] Provision an empty, disposable PostgreSQL database.
- [ ] Record its sanitized `host[:port]/database` identity.
- [ ] Explicitly approve that identity for destructive QA operations.
- [ ] Apply every migration from an empty database.
- [ ] Run schema/migration integration tests.
- [ ] Run sample-data dry run.
- [ ] Run `seed`.
- [ ] Run `validate`.
- [ ] Run a second `seed` and prove idempotency.
- [ ] Run `list`.
- [ ] Run `cleanup` and prove unrelated records remain.
- [ ] Run `reset` and validate final state.
- [ ] Record exact commands and sanitized results.

Blocker: No approved disposable PostgreSQL URL and exact identity have been supplied.

## PR-03 — Upload and parser hardening

Status: `[ ] Not started`

- [ ] Define maximum request and decoded file sizes.
- [ ] Define supported file extensions, MIME types, and signatures.
- [ ] Enforce limits before persistence.
- [ ] Validate magic bytes/signatures.
- [ ] Reject extension/MIME/signature mismatches.
- [ ] Add archive expansion, member-count, depth, and nesting limits.
- [ ] Add document page/parser time/resource limits where applicable.
- [ ] Apply the policy to API uploads.
- [ ] Apply the policy to Streamlit uploads.
- [ ] Apply the policy to inbox attachments.
- [ ] Apply the policy to worker/parser paths.
- [ ] Confirm rejected uploads leave no partial residue.
- [ ] Add valid, oversized, spoofed, malformed, unsupported, and bomb-like fixtures.
- [ ] Document the policy and operator-visible errors.

## PR-04 — Session hardening

Status: `[ ] Not started`

- [ ] Decide the canonical session model.
- [ ] Document browser, API, and Streamlit transition behavior.
- [ ] Replace browser script-readable token storage.
- [ ] Use Secure, HttpOnly, appropriately SameSite cookies.
- [ ] Add CSRF defenses for cookie-authenticated mutations.
- [ ] Define trusted origins and CORS policy.
- [ ] Define session expiration and refresh.
- [ ] Define logout and revocation behavior.
- [ ] Test XSS token-access resistance at the architectural boundary.
- [ ] Add CSRF, CORS, logout, expiry, and unauthorized tests.
- [ ] Decide the interim Streamlit authentication boundary.

## PR-05 — Database lifecycle constraints

Status: `[ ] Not started`

- [ ] Inventory every persisted lifecycle/status value.
- [ ] Inventory retry/count fields.
- [ ] Compare database values with application enums and transitions.
- [ ] Detect invalid legacy rows before migration.
- [ ] Define remediation for invalid rows.
- [ ] Add supported-status check constraints.
- [ ] Add nonnegative retry/count constraints.
- [ ] Test clean migration.
- [ ] Test migration with invalid legacy data.
- [ ] Test PostgreSQL rejection of invalid writes.
- [ ] Preserve clear application-level validation.

## PR-06 — SMS/outbox idempotency

Status: `[ ] Not started`

- [ ] Document the exact provider-accepted/local-not-committed crash window.
- [ ] Confirm provider idempotency capability.
- [ ] Confirm provider message lookup/reconciliation capability.
- [ ] Define a stable idempotency key.
- [ ] Persist provider identifiers safely.
- [ ] Add an ambiguous/reconciliation-required state.
- [ ] Prevent uncontrolled automatic resend after unknown outcomes.
- [ ] Add operator reconciliation tooling.
- [ ] Test crash before send.
- [ ] Test crash after provider acceptance.
- [ ] Test retry.
- [ ] Test duplicate callback.
- [ ] Test concurrent workers.
- [ ] Document the residual unavoidable risk.

## PR-07 — Pull-request CI

Status: `[ ] Not started`

- [ ] Backend unit/integration-safe tests.
- [ ] Frontend unit tests.
- [ ] Python formatting/lint/compile checks.
- [ ] TypeScript typecheck.
- [ ] ESLint.
- [ ] Next.js production build.
- [ ] Migration validation using disposable infrastructure.
- [ ] Dependency vulnerability scanning.
- [ ] Secret scanning.
- [ ] Concurrency cancellation for superseded runs.
- [ ] Safe dependency caches.
- [ ] Confirm CI cannot inherit production configuration.
- [ ] Document required branch-protection checks.
- [ ] Verify an intentional test failure blocks the workflow.

## PR-08 — Persistent workers and recovery

Status: `[ ] Not started`

- [ ] Decide production hosting topology.
- [ ] Decide which processors require persistent workers.
- [ ] Define worker process ownership and scaling.
- [ ] Implement readiness and liveness checks.
- [ ] Expose queue lag and oldest-job age.
- [ ] Expose retry and terminal-failure counts.
- [ ] Provide dead-letter/retry/reconciliation operations.
- [ ] Test graceful shutdown and job lease recovery.
- [ ] Test multiple-worker concurrency.
- [ ] Add deploy and incident runbooks.

## PR-09 — Canonical application path and Streamlit retirement

Status: `[ ] Not started`

- [ ] Confirm the target architecture.
- [ ] Map every Streamlit page to its replacement status.
- [ ] Identify direct database access in UI code.
- [ ] Freeze new features in superseded paths.
- [ ] Select the next workflow for migration.
- [ ] Define API/read/write coverage needed for that workflow.
- [ ] Add parity tests.
- [ ] Migrate one workflow.
- [ ] Retest roles and failure paths.
- [ ] Remove or restrict the replaced Streamlit path.
- [ ] Repeat one workflow at a time.

## PR-10 — Documentation and configuration

Status: `[ ] Not started`

- [ ] Correct README setup instructions.
- [ ] Document environment-variable precedence.
- [ ] Document safe test-database configuration.
- [ ] Document sample-data commands.
- [ ] Document API/web/Streamlit start commands.
- [ ] Document worker and retry operations.
- [ ] Document supported upload policy.
- [ ] Document session/authentication model.
- [ ] Remove or label obsolete instructions.
- [ ] Execute or mechanically validate every documented command.

## New findings

Add newly discovered defects here before changing the active package scope.

| ID | Date | Severity | Description | Discovered during | Issue | Status |
|---|---|---:|---|---|---|---|
| CTMS-010 | 2026-08-18 | P2 | `tests/integration/operations_inbox/harness.py::require_scratch_database_url()` compared `INBOX_CERTIFICATION_DATABASE_URL` against `config.get_secret("DATABASE_URL")` to reject a production-identical target, but PR-01's session-wide pytest guard makes `config.get_secret("DATABASE_URL")` unconditionally return `None` for the entire pytest session (by design), so that comparison could never fire inside a normal pytest run. Resolved in commit `859db96` (same branch as PR-01): replaced with a parse-and-validate model reusing `scripts/sample_data.py`'s `assert_safe_target()` convention — explicit safe-name-marker requirement, rejection of generic/production names, and operator identity acknowledgement via `INBOX_CERTIFICATION_DATABASE_IDENTITY`. 14 new regression tests in `tests/integration/operations_inbox/test_harness_safety.py`. | PR-01 diff review | — | `[x] Resolved` |

## Change log

Add one row whenever a package changes state.

| Date | Package | From | To | Branch/commit | Summary | Evidence |
|---|---|---|---|---|---|---|
| 2026-08-18 | PR-01 | Not started | In progress | Local changes based on `5faaf0f` | Test isolation implemented during review; still uncommitted and awaiting focused review | 39 focused tests passed |
| 2026-08-18 | PR-01 | In progress | Ready for review | `fix/pr-01-database-test-isolation` @ `f22984b` | Closed the caller-set-`DATABASE_URL` bypass and uncleared `config.DATABASE_URL` gaps; added 2 regression tests (cross-contamination refusal, credential-leak-in-errors); committed on a dedicated branch; logged CTMS-010 (pre-existing, now-dead harness safety check) as a new finding instead of expanding this package's scope | Full suite: 1377 passed, 98 skipped, 0 failed |
| 2026-08-18 | PR-01 | Ready for review | Verified | `fix/pr-01-database-test-isolation` @ `859db96` | Independent review: re-derived and re-ran all evidence rather than trusting the prior report; confirmed all 10 requested isolation properties empirically. Resolved CTMS-010 in the same branch (parse-and-validate certification-target gate reusing sample-data's safety convention, 14 new tests). Not marked `[x] Complete` — awaiting a second human reviewer. | Full suite: 1391 passed, 98 skipped, 0 failed; harness safety: 14 passed; operations_inbox integration folder: 14 passed, 49 skipped, no DB contacted |
| 2026-08-19 | PR-01 | Verified | Complete | GitHub PR #13 merged @ `decfaad` into `feat/operations-inbox-web` | Human (Vreza75) reviewed and explicitly directed the merge of PR #13; merge verified independently via `gh pr view` (`state: MERGED`) and `git log` (merge commit is on `origin/feat/operations-inbox-web`, PR-01 commits are ancestors) before updating status — not accepted on request alone. Local `feat/operations-inbox-web` fast-forwarded to match; full suite re-run post-merge. | Full suite post-merge: 1391 passed, 98 skipped, 0 failed |

## Per-session update template

Copy this block for every Claude/Codex work session:

```markdown
### Session YYYY-MM-DD — PR-XX

- Starting branch:
- Starting commit:
- Goal:
- Finding reproduced by:
- Files changed:
- Database migrations:
- Tests added:
- Commands run:
- Results:
- Ending commit or `uncommitted`:
- Acceptance criteria satisfied:
- Acceptance criteria remaining:
- New findings:
- Status after session:
- Exact next action:
```

## Quick bug report for Victor

```markdown
Test ID:
Page/function:
User role:
Sample record ID:
Commit tested:
What I did:
What I expected:
What happened:
Error text:
Approximate time:
Screenshot/log attached:
Can I reproduce it again? Yes/No
```
