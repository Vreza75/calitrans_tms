# Production readiness gaps and roadmap

Baseline: `5faaf0f8007ac0f1e3073761d57282ed7e1a73ce`, reviewed 2026-08-18. Verdict: do not create/connect live production data yet.

## Immediate safety work

| Priority | Recommendation | Reason / dependencies / risk | Acceptance criteria | Milestone |
|---|---|---|---|---|
| P1 | Rotate any database credential exposed in prior test output and separate local/staging/production secrets | CTMS-001 proved environment ambiguity; requires database owner | Old credential rejected; environment-specific secrets documented; no repository-local production URL | Safety 0 |
| P1 | Merge pytest isolation and require it in CI | Prevent test access to app DB | Tests prove `DATABASE_URL` is removed even if preloaded; DB suites require dedicated validated variables | Safety 0 |
| P1 | Add upload byte/signature/content controls | Prevent exhaustion/disguised content | Boundary tests, streaming limit, quarantine/rejection, sanitized errors | Safety 0 |
| P1 | Replace script-readable sessions | Reduce token theft/XSS impact | HttpOnly/Secure/SameSite cookies, CSRF defense, expiry/rotation/revocation tests | Safety 1 |
| P2 | Add PR CI and secret/dependency scanning | Current only workflow is operational schedule | Required Python/web/build checks on every PR; secret scan and dependency review enabled | Safety 1 |

## Production-readiness blockers

| Priority | Recommendation | Scope / risk | Acceptance criteria |
|---|---|---|---|
| P1 | Establish isolated development, staging, and production databases | Prevent cross-environment writes | Unique projects/credentials; least-privilege roles; visible environment banners; destructive commands refuse production |
| P1 | Apply/verify migrations in staging and perform backup/restore drill | Schema and recovery are unverified | Migration runner + verifier pass; rollback notes reviewed; timed restore succeeds |
| P1 | Run full functional matrix with named dataset | Runtime claims are otherwise unproven | Every P1/P2 workflow Pass or accepted Blocked with owner/date |
| P1 | Add queue/backlog/failure monitoring | Scheduled work can stall silently | Alerts on oldest pending, failed count, schedule absence, document pending age, SMS failure |
| P2 | Add PostgreSQL check constraints after data audit | Prevent impossible statuses/negative attempts | Data cleanup report clean; constraints applied and tested |
| P2 | Define retention/privacy/access policy | Email bodies, attachments, phone numbers are sensitive | Approved retention periods, deletion/export process, storage encryption/access logs |

## Reliability work

1. Provider-side SMS idempotency/reconciliation and a crash-window regression test.
2. Continuously supervised or managed workers once measured staging volume justifies them; retain `SKIP LOCKED` claim safety.
3. Operator dashboard/API for failed jobs/outbox/events, protected by admin/operator permissions.
4. Durable object storage for documents; checksum, malware scanning, lifecycle/retention, and orphan cleanup.
5. Add timeouts/circuit-breaking/backpressure metrics for mailbox, AI, Twilio, Motive, and Port Houston.
6. Add immutable correlation IDs across request, command, event, job, and provider response.

## Web migration

1. Accept Next.js Operations Inbox in staging while Streamlit remains fallback.
2. Migrate Dispatch Board using existing application commands and realtime events.
3. Migrate load workspace mutations and driver assignment.
4. Migrate documents/communications with secure upload/download controls.
5. Migrate Billing/ProfitTools with accounting-specific acceptance tests.
6. Keep Admin/Diagnostics in Streamlit until equivalent recovery tools exist; freeze/remove page-by-page only after parity and one stable release.

Each migration must demonstrate permission parity, transaction/event parity, browser tests for the critical path, disconnect/retry behavior, and no direct browser database access.

## Integration readiness

- Motive: provision sandbox credentials, document scopes/webhook verification, normalize driver/vehicle identity, and test timeouts/duplicates.
- Driver tracking/maps: define consent, retention, update cadence, map provider cost, and geospatial model before implementation.
- SMS: approved sender, opt-out/compliance policy, sandbox/fake provider, delivery callback verification, reconciliation.
- Port Houston: sandbox/authorization, request signing/token storage, rate limits, retry classification, manual fallback.
- Email: dedicated non-production mailboxes, sender/thread spoofing review, attachment controls, bounded sync monitoring.
- Realtime: private channel authorization decision, service-role rotation, two-browser disconnect/refetch acceptance.

## Quality improvements

- Split large Operations Inbox modules by responsibility only after parity tests pin behavior.
- Expand type coverage for application/repository DTOs; add Ruff/Mypy gradually rather than mass-formatting legacy code.
- Replace stale README/setup text with current architecture/runbooks.
- Measure query plans and add indexes from staging evidence; avoid speculative indexes.
- Add load tests for search, queue claims, large inbox histories, and attachment streaming.
- Maintain the defect workflow and require stable test IDs in PR evidence.

## Release gates

A production go/no-go review must show: clean migration/schema verification; successful backup restore; P1/P2 test matrix acceptance; least-privilege role test; no real-provider sends from QA; queue/worker alerts firing in a drill; upload security tests; credential rotation; incident/rollback runbook; and named owners for integration failures.
