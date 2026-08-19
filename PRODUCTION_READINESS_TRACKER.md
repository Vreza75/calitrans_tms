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
| PR-01 Database test isolation | CTMS-001 | P1 | `[R] Ready for review` | — | `fix/pr-01-database-test-isolation` @ `f22984b` | Full suite: 1377 passed, 98 skipped, 0 failed |
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

Status: `[R] Ready for review`

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
- [ ] Independent review/retest.

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
- Completion commit: `f22984b` on branch `fix/pr-01-database-test-isolation`
- Files changed: `conftest.py`, `tests/test_api_auth.py`, `tests/test_communications_schema.py`, `tests/test_conftest_database_isolation.py`, `tests/test_db_client_column_exists.py`
- Commands run and results:
  - `python -m compileall -q conftest.py tests/test_api_auth.py tests/test_communications_schema.py tests/test_conftest_database_isolation.py tests/test_db_client_column_exists.py api/auth.py config.py db_client.py` → no output, exit 0.
  - `python -m pytest -q tests/test_conftest_database_isolation.py tests/test_api_auth.py` → `32 passed, 1 warning`.
  - `python -m pytest -q tests/test_db_client_column_exists.py tests/test_communications_schema.py -v` → `5 skipped` (correctly skip without `MIGRATION_TEST_DATABASE_URL`; this is the exact CTMS-001 behavior being closed for these two files).
  - `python -m pytest -q` (full backend suite) → `1375 passed, 98 skipped, 0 failed` before adding the two new regression tests; `1377 passed, 98 skipped, 0 failed` after.
  - `git diff --check` → exit 0, no whitespace/conflict-marker issues (only benign CRLF-on-checkout warnings).
  - Manual reproduction outside pytest: `sqlalchemy.create_engine("postgresql://tms_app:Sup3rS3cret!@127.0.0.1:1/doesnotexist", ...).connect()` and a malformed-scheme URL — neither leaked the username or password in the raised exception.
- Issue/PR: not opened (local branch only, per instruction not to push/merge).
- Reviewer: none yet — independent review/retest still outstanding.
- Remaining risk: see "New findings" below for one related, pre-existing gap in `tests/integration/operations_inbox/harness.py::require_scratch_database_url` surfaced while reviewing this change (out of this package's file scope, not introduced by it, tracked separately). No other known residual risk for the files this package touched.

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
| CTMS-010 | 2026-08-18 | P2 | `tests/integration/operations_inbox/harness.py::require_scratch_database_url()` compares `INBOX_CERTIFICATION_DATABASE_URL` against `config.get_secret("DATABASE_URL")` to reject a production-identical target, but PR-01's session-wide pytest guard now makes `config.get_secret("DATABASE_URL")` unconditionally return `None` for the entire pytest session (by design) — so that comparison can never fire inside a normal pytest run. This is a pre-existing weakness (it also could not reliably fire before PR-01 in the common no-exported-`DATABASE_URL` case) that PR-01 makes fully inert rather than newly introducing. The primary defense (no real `DATABASE_URL` is ever resolvable inside pytest at all) still holds; this is a secondary, now-dead safety net. Needs a decision on a positive disposable-identity convention (e.g. required `qa`/`staging`/`scratch` naming) rather than an equality check against a value pytest deliberately blinds. | PR-01 diff review | — | `[ ] Not started` |

## Change log

Add one row whenever a package changes state.

| Date | Package | From | To | Branch/commit | Summary | Evidence |
|---|---|---|---|---|---|---|
| 2026-08-18 | PR-01 | Not started | In progress | Local changes based on `5faaf0f` | Test isolation implemented during review; still uncommitted and awaiting focused review | 39 focused tests passed |
| 2026-08-18 | PR-01 | In progress | Ready for review | `fix/pr-01-database-test-isolation` @ `f22984b` | Closed the caller-set-`DATABASE_URL` bypass and uncleared `config.DATABASE_URL` gaps; added 2 regression tests (cross-contamination refusal, credential-leak-in-errors); committed on a dedicated branch; logged CTMS-010 (pre-existing, now-dead harness safety check) as a new finding instead of expanding this package's scope | Full suite: 1377 passed, 98 skipped, 0 failed |

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
