# Calitrans TMS system inventory

Baseline: `5faaf0f8007ac0f1e3073761d57282ed7e1a73ce`, 2026-08-18. Status terms are restricted to the requested vocabulary. Runtime means exercised in this review; most database/provider subsystems are **not runtime-verified** because no disposable database was established.

## Repository and component baseline

| Item | Evidence |
|---|---|
| Root / repository | `C:\GitHub\calitrans_tms_postgres_upgrade_clean`; origin `Vreza75/calitrans_tms` |
| Branch / base | `feat/operations-inbox-web`; remote default `origin/master` |
| Languages | Python 3.14.6 locally (`runtime.txt` targets 3.11); TypeScript/React; Node 24.18.0, npm 11.16.0 |
| Dependency management | `requirements*.txt`; `web/package-lock.json` |
| Database | PostgreSQL/Supabase; ordered idempotent SQL runner `scripts/run_migrations.py` |
| Tests | pytest; Vitest/Testing Library; Playwright specifications |
| Quality tools | Python compile/tests (no configured Ruff/Mypy found); ESLint + TypeScript strict |
| CI | scheduled `.github/workflows/process-jobs.yml`; no PR validation workflow found |
| Main entry points | Streamlit `app.py`; API `api.main:app`; web `web/app`; workers/outbox/realtime CLIs under `scripts/` |
| Secrets precedence | Streamlit secrets, environment, local `.env`, local Streamlit file via `config.get_secret`; pytest now blocks local fallbacks and `DATABASE_URL` |
| Deployment files | `runtime.txt`, `.devcontainer`; scheduled GitHub workflow; no Dockerfile/Procfile discovered |
| Overlapping paths | Yes: application layer plus legacy services/direct Streamlit/legacy API compatibility paths |

## Subsystem inventory

| Subsystem | Status | Purpose and entry points | Tables / API / UI | Permissions / jobs / dependencies | Tests, runtime, gaps, next action |
|---|---|---|---|---|---|
| Authentication | Functional but incomplete | `ui_components/auth_gate.py`, `POST /api/v1/auth/login`, `GET /api/v1/me` | `app_users`; Streamlit and Next login | bcrypt, HMAC session token, static service tokens | Strong unit/API tests; not live-DB verified. Move to HttpOnly cookies and rotation. |
| User/role management | Partially implemented | `repositories/user_repo.py`, `scripts/create_admin_user.py` | `app_users`; no complete user-admin UI/API | Admin policy exists | Automated repository/auth tests; provisioning is operator-script based. Add audited admin commands/API. |
| RBAC | Functional but incomplete | `application.auth.permissions`, route role gates, command `require_permission` | `/me` returns permissions; Streamlit navigation filtered | Four roles; command permissions | Broad negative tests; continue scanner enforcement and cover every remaining legacy write. |
| Loads/orders | Functional but incomplete | application load queries/commands, Streamlit Orders/Dispatch, Next loads | `loads`, `status_events`; `/api/v1/loads*` | Load/dispatch permissions; events/outbox for some commands | Strong unit/API tests; DB/runtime not verified. Complete web mutations and DB status constraints. |
| Order intake | Functional but incomplete | `services/order_intake.py`, inbox worker | `order_intake`, drafts | worker jobs, parsing/AI | Large regression corpus; live mailbox not verified. Continue consolidation. |
| Operations Inbox | Functional but incomplete | worker handlers; Streamlit Inbox; Next `/app/inbox` | `order_intake`, cases, drafts; `/work-items*` | `WORK_ITEM_MANAGE`; inbox jobs/events | CASE fixtures 000–010 and web/API tests; live DB not run. Accept Next workflow in staging. |
| Inbox deduplication | Functional but incomplete | message IDs and worker idempotency | message-id indexes/fields, `worker_jobs.idempotency_key` | mailbox provider | Automated tests; concurrency with live DB not verified. Run certification repeatedly in staging. |
| Inbox-to-load create/update | Functional but incomplete | `application.loads.commands` -> canonical inbox service | `loads.source_intake_id`, intake links | `WORK_ITEM_MANAGE`, transaction/domain events | Atomicity/idempotency tests exist; live rollback not verified. Run disposable DB fault tests. |
| Attachments/documents | Functional but incomplete | application document command, attachment API, Streamlit | `documents`; attachment content endpoint | `DOCUMENT_ATTACH`; finalize outbox | Lifecycle tests; no upload size/signature limit, local disk durability unverified. |
| Drivers | Functional but incomplete | master data, roster, dispatch assignment | `drivers`, loads assignment fields | dispatch/master-data permissions | Unit/concurrency tests; no complete API/web roster. Add driver read model. |
| Vehicles/equipment | Scaffold only | truck/chassis strings on load/driver | no normalized vehicle/trailer tables found | none | No lifecycle/assignment inventory. Design only after business requirements. |
| Dispatch board | Functional but incomplete | Streamlit `pages_app/dispatch_board.py`; application dispatch commands | loads/status/audit tables | `DISPATCH_TRANSITION`; SMS outbox | Good transition/concurrency tests; web absent. Next migration candidate. |
| Tasks/work items | Functional but incomplete | `repositories/task_repo.py`, work-item applications/API | `operations_tasks`, `order_intake` | task/work-item permissions | Automated coverage; task API/web incomplete. Move task writes behind application commands. |
| Communications | Functional but incomplete | communication services/providers | `dispatch_messages`, replies, notifications | dispatch communication permission | Provider/unit tests; inbound reconciliation and live email not verified. |
| SMS | Functional but incomplete | ready-to-dispatch command -> outbox -> Twilio | `outbox_events`, `dispatch_messages` | `DRIVER_MESSAGE_SEND`; outbox processor | Retry/idempotency tests; duplicate crash window remains; no live send performed. |
| Transactional outbox | Functional but incomplete | `repositories/outbox_repo.py`, `services/outbox_processor.py`, CLI | `outbox_events` | scheduled job, Twilio/file system | Claim/retry/reclaim/operator tests; live concurrency not verified; add metrics and DB checks. |
| Worker jobs | Functional but incomplete | `workers/processor.py`, inbox handlers, CLI | `worker_jobs`; `/jobs`, `/worker/status` | scheduled GitHub Action, mailbox/AI/PDF | Extensive tests; schedule is best-effort and live jobs not exercised. |
| Dead-letter/recovery | Functional but incomplete | terminal `failed`, list/inspect/retry/retry-all CLIs | three queue tables | operator shell | Automated tests; no RBAC UI or alert integration. Add operator dashboard and paging. |
| Domain events | Functional but incomplete | `realtime.events`, repository, publisher | `domain_events` | scheduled publisher | Unit/repository tests; live broadcast not verified. |
| Realtime client | Functional but incomplete | Supabase publisher + Next subscriptions/invalidation | Broadcast only; no DB reads from browser | service role server-side, public browser key | Mocked web/Python tests; two-browser proof not run. |
| Search/pagination/filtering | Functional but incomplete | load/work-item query repos and APIs | `/loads/search`, `/work-items` | read roles | Automated query/API tests; Streamlit still has client-side/legacy paths. |
| Audit history | Functional but incomplete | status events, dispatcher actions, case events, timeline API | several audit tables | command actor | Automated transactional tests; no immutable/audit retention policy. |
| Admin | Functional but incomplete | Streamlit Admin/Diagnostics; scripts | master tables, users, queue tables | manager/admin plus operator shell | Permission tests; operator CLIs bypass app RBAC by design. Document host access controls. |
| Port Houston | Partially implemented | Streamlit integration and application commands | load integration columns | `PORT_DATA_APPLY`; external OAuth/API | Unit tests only; credentials/live authorization unavailable. |
| Motive | Partially implemented | provider adapter, admin status | driver legacy fields/settings | external Motive | Mocked tests; live integration and secure credential lifecycle not verified. |
| Reporting/billing | Functional but incomplete | Streamlit dashboard, billing, ProfitTools export | loads and export files | billing permissions | Unit tests; no web surface and export security/runtime not verified. |
| Notifications | Partially implemented | email notifications/status email services | `email_notifications` | SMTP | Static/tests only; delivery/retry semantics less mature than SMS outbox. |
| Streamlit UI | Functional but incomplete | `app.py`, `pages_app`, `ui_components` | broad direct service access | session auth/nav + command gates | Compiles and many UI logic tests; browser runtime not run; large modules remain. |
| Dedicated web client | Functional but incomplete | Next login, loads, Inbox | FastAPI only, Broadcast invalidation | backend authorization | 39 web tests, lint/typecheck pass; build/live E2E pending this review. |
| FastAPI | Functional but incomplete | `api/main.py`, versioned routers plus legacy routes | auth, loads, inbox, work items, attachments, jobs | bearer/session auth, role gates | Strong API tests; legacy endpoints and incomplete workflow coverage remain. |
| Health/readiness | Partially implemented | `/api/v1/health`, legacy `/health`, schema verifier | no dependency-rich readiness response | none/public | Health does not prove DB/workers/providers. Add liveness vs readiness and queue-age health. |
| Migrations | Functional but incomplete | ordered runner and schema verifier | SQL files + `schema_migrations` | operator DB credentials | Static/migration tests; not applied in review. Add rollback notes and checksum/drift policy. |
| Observability | Partially implemented | CLI summaries, job status endpoints, sanitized errors | queue status counts | logs/GitHub run status | No confirmed metrics/tracing/alerts/SLOs. Add structured correlation IDs and backlog alerts. |

## Workflow ownership map

| Workflow | Current primary surface | Canonical boundary | Event/async behavior | Migration state |
|---|---|---|---|---|
| Login/logout | Next + Streamlit | auth queries/session token | none | Transitional token design |
| Load read/search/detail | Next + Streamlit | application load queries | realtime invalidation | Web available |
| Load mutations | Streamlit/API | application load/dispatch commands | domain events; SMS outbox where applicable | Web incomplete |
| Operations Inbox | Next primary candidate, Streamlit fallback | work-item API/application plus worker pipeline | worker jobs + realtime | Acceptance needed |
| Documents | Streamlit | application document command | finalize outbox + domain event | Web absent |
| Billing/ProfitTools | Streamlit | mixed service paths | file export | Web absent |
| Admin/integrations | Streamlit + operator scripts | mixed commands/repos | provider calls | Retain temporarily |

## Dead/unreachable and compatibility candidates

- `database/db_client.py` is a compatibility wrapper, not dead.
- Legacy unversioned API routes are deprecated but reachable.
- `pages_app/documents.py::render_pdf_intake` is documented as confirmed dead by existing scanner tests, but was not removed in this review.
- Root `db_client.py`, service aliases, and Operations Inbox compatibility symbols remain production-required or uncertain; do not delete without call-site/dynamic-key/runtime evidence.
