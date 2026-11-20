# Calitrans functional test matrix

Baseline dataset: `baseline-01` from `scripts/sample_data.py`. Results reflect the 2026-08-18 review only: **Pass** means automated evidence ran safely; **Blocked** means credentials/disposable infrastructure were unavailable; **Not Run** means a safe runtime execution was not attempted.

| ID | Subsystem / function | Preconditions and role | Exact test / expected UI, API, DB, event result | Negative / permission / recovery | Mode | Result / evidence | Defect |
|---|---|---|---|---|---|---|---|
| AUTH-001 | Login and `/me` | Active QA user; any role | Submit credentials; token/session established; `/me` returns matching role/permissions | Wrong password 401; inactive user 401; expired/tampered token 401 | Automated | Pass: auth suites | CTMS-003 |
| AUTH-002 | Logout/session clearing | Authenticated web/Streamlit user | Logout removes local principal and protected navigation | Reuse removed token/session; expect login redirect/401 | Manual/browser | Not Run | CTMS-003 |
| AUTH-003 | Role matrix | Four QA roles | Each sensitive read/mutation matches authorization matrix | One forbidden call per permission returns 403 and makes zero writes | Automated | Pass: permission/command suites | — |
| LOAD-001 | Search/filter/paginate loads | QA dataset; all roles with load view | `/loads/search` filters deterministic records; UI count/order matches DB | Invalid page/sort/date rejected or normalized | Automated + manual | Automated Pass; runtime Blocked | — |
| LOAD-002 | Load detail/timeline | QA load; load-view role | Detail, timeline, communication, document endpoints return scoped records | Unknown ID 404; accounting remains read-only | Automated | Pass: API/read-model tests | — |
| LOAD-003 | Edit load fields | New QA load; dispatcher | Submit allowed fields; DB update and `load.updated` event commit together | Invalid field/value; accounting 403; injected failure rolls back | Automated + DB | Automated Pass; DB Blocked | CTMS-005 |
| LOAD-004 | Cancel/verify/missing-info | New QA load; dispatcher | Status and audit/domain event update | Invalid transition; unauthorized; event insert failure rollback | Automated + DB | Automated Pass; DB Blocked | CTMS-005 |
| LOAD-005 | Ready to dispatch/SMS | Assigned QA load + fake provider | Load state, dispatch message, outbox row commit; no real SMS | Missing phone, accounting 403, duplicate command, provider failure/retry | Automated + DB | Automated Pass; provider Blocked | CTMS-004 |
| DISPATCH-001 | Assign driver/tractor | Unassigned QA load; dispatcher | Assignment and audit commit atomically | Concurrent assignment conflict; unauthorized; rollback injection | Automated + DB | Automated Pass; DB Blocked | — |
| DISPATCH-002 | Status transition | Assigned QA load | Valid state change visible in board/API/timeline | Backward/invalid transition; override requires reason; retry | Automated + manual | Automated Pass; UI Not Run | — |
| INBOX-001 | Request inbox sync | Dispatcher/manager/admin | `POST /inbox/sync` returns 202/job ID; one deduped job | Accounting 403; repeated request reuses idempotency window; worker retry | Automated + DB | Automated Pass; mailbox Blocked | CTMS-006 |
| INBOX-002 | Process new booking email | CASE-001 / QA sender | One intake/draft with correct queue/fields | Missing booking routes review; parse failure retried or review outcome | Certification | Blocked: no disposable DB | — |
| INBOX-003 | Duplicate email | Duplicate message ID | No second active intake/business side effect | Concurrent duplicate delivery | Automated + DB | Automated Pass; DB Blocked | — |
| INBOX-004 | Attachment email | CASE-005 | Attachment stored/parsed and linked | Unsupported/spoofed/oversized content rejected | Automated + DB | Partial Pass | CTMS-002 |
| INBOX-005 | Existing-load update | CASE-008/009 | Match existing load; proposed update without duplicate load | Driver-assigned address change requires review; rollback | Certification | Blocked | — |
| INBOX-006 | Multiple orders in email | CASE-010 | Two isolated work items/drafts, no field bleeding | Ambiguous segmentation quarantined; repeat idempotent | Automated + DB | Automated unit evidence; certification Blocked | — |
| INBOX-007 | Create load from work item | Ready QA work item; dispatcher | Load, links, communications, case/audit, event commit atomically | Missing identifier 422; accounting 403; repeat reuses load; injected rollback | Automated + DB | Automated Pass; DB Blocked | — |
| INBOX-008 | Update/link/close work item | Matched QA work item | Requested mutation plus audit/event; disappears from active queue | Bad target 404; unauthorized; rollback/retry | Automated + DB | Automated Pass; DB Blocked | — |
| INBOX-009 | Inbox web workspace | API + Next running; dispatcher | Queue counts/list/detail/actions and URL selection work | Accounting action buttons disabled and backend 403; realtime refetch | Playwright | Not Run; spec exists | — |
| DOC-001 | Attach supported document | QA load; permitted role | Pending DB row + finalize outbox; worker moves file; available event | Unauthorized zero staging; DB rollback; missing staged file retries | Automated + DB | Automated Pass; DB Blocked | CTMS-002 |
| DOC-002 | Download/view document | QA document; load-view role | API returns exact bytes/type without raw file path | Invalid ref/work-item scope 404; traversal attempt rejected | Automated + manual | Automated partial; browser Not Run | CTMS-002 |
| DOC-003 | File validation | Any upload role | Enforce max bytes, signature, allowlist | Oversize/spoofed/unsupported rejected before disk/DB | Missing | Fail by static inspection | CTMS-002 |
| TASK-001 | Create/assign/complete task | QA load/intake; dispatcher | Task visible with owner/due/status and audit | Invalid linkage, unauthorized, rollback | Repository/manual | Not Run; repository tests exist | CTMS-007 |
| SMS-001 | Process outbox success | Fake SMS handler; pending event | Claim -> provider -> delivered + message projection | Crash/reclaim and duplicate-provider callback | Automated | Pass except exactly-once gap | CTMS-004 |
| SMS-002 | Retry/terminal failure | Fake failing provider | Backoff through max attempts; failed visible/retryable | Unauthorized operator access controlled by host | Automated | Pass: outbox tests | CTMS-006 |
| WORKER-001 | Inbox job success | Disposable DB, mocked mailbox | Claim/process/complete; second worker cannot claim same row | Handler error retries; crash row reclaimed | Automated + DB | Automated Pass; DB Blocked | CTMS-006 |
| WORKER-002 | Operator retry | Failed synthetic job | list/inspect/retry/retry-all changes only selected rows | Unknown ID nonzero exit; terminal poison message remains visible | Automated | Pass: CLI/repo tests | — |
| EVENT-001 | Record domain event atomically | Mutating command | Business write + event in one transaction | Event insert failure rolls business write back | Automated + DB | Automated Pass; DB Blocked | — |
| EVENT-002 | Broadcast/invalidate/refetch | Realtime credentials + two browsers | Publisher broadcasts envelope; client ignores stale event and refetches API | Disconnect shows degraded indicator; failed publish retries | Automated + runtime | Mocked Pass; live Blocked | CTMS-006 |
| SEARCH-001 | Work-item search/counts | QA Inbox records | Server filter/pagination; one grouped counts query | Injection string bound safely; invalid limits 422 | Automated | Pass: API/repo/web tests | — |
| ADMIN-001 | Master data edit | Manager/admin | Synthetic customer/warehouse/carrier/driver upsert | Dispatcher/accounting denied; invalid input rejected | Automated + manual | Automated permission Pass; UI Not Run | — |
| ADMIN-002 | User provisioning | Operator/admin | Create active synthetic account with bcrypt hash | Duplicate email, weak password, non-admin denial | Script/manual | Not Run | — |
| PORT-001 | Port Houston lookup/apply | Safe sandbox credentials; dispatcher | Lookup response applied through command/event | Timeout/auth error; accounting denied; retry/manual recovery | Mocked + manual | Mocked tests; live Blocked | — |
| MOTIVE-001 | Motive status/roster | Sandbox credentials | Driver/provider response normalized without password storage | Auth failure, timeout, malformed response | Mocked + manual | Mocked tests; live Blocked | — |
| BILLING-001 | ProfitTools export | Delivered/ready QA load; accounting | Only eligible columns/rows exported | Unauthorized, empty set, write failure | Automated + manual | Automated Pass; UI Not Run | — |
| QA-001 | Seed refusal | Production-like URL | Exit 3 before connection and print no secret | Missing any marker also refuses | Automated | Pass: `test_sample_data.py` | CTMS-001 |
| QA-002 | Seed idempotency | Disposable migrated DB | Second seed creates no duplicates; summary reports reused | Transaction failure leaves no partial dataset | DB integration | Blocked | — |
| QA-003 | Dataset cleanup | Seeded `baseline-01` plus unrelated rows | Deletes only exact QA marker/idempotency keys | Different dataset and non-QA records unchanged | DB integration | Blocked | — |

## Manual execution record format

Copy one row per run into an issue or test log:

```text
Test ID:
Commit:
Environment / sanitized DB identity:
Dataset ID:
Role:
Started / duration:
Result: Pass | Fail | Blocked | Not Run
Evidence (screenshot, sanitized request ID, DB query):
Defect ID:
Notes:
```

For every mutation, capture the UI response, HTTP status/body, affected primary keys, audit/event/outbox/job IDs, and proof that unrelated dataset rows were unchanged.
