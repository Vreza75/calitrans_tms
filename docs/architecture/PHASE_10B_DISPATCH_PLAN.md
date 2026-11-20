# Phase 10B — Dispatch Board (Next.js/FastAPI)

**Base:** `origin/master` @ `8054945` (Phase 10A, PR #12 merged; also includes PR #15–#18, #21).
**Mission:** replace the Streamlit Dispatch Board with a Next.js/FastAPI Dispatch Board, reusing the existing application/API layer. Streamlit is legacy only — no operational fallback, no data migration needed.
**Out of scope (explicit exclusions):** Motive integration, SMS, AI/dispatcher-feedback development, any schema/migration change, any Streamlit deletion beyond what Task 3 classifies as safe.

**Note on in-repo narrative vs. business reality:** `web/components/AppShell.tsx`'s topbar banner ("Streamlit remains the operational system of record") and `docs/architecture/WEB_CLIENT.md`'s status line both still describe Streamlit as the live operational system. Per current direction, Streamlit has not been used operationally for some time and is not an operational fallback. This plan does not change that banner/doc copy (out of this plan's bounded scope), but flags it as a low-risk follow-up edit to make before or alongside Phase 10B, so the UI stops asserting something no longer true.

---

## Task 1 — Current State Audit

Audited against a disposable `git worktree` at `origin/master` (8054945), not the local `integration/pr12-pr22-pr23` checkout, which lacks PR #15–#18/#21 and is not the true current baseline.

### 1. Already available in Next.js
- `web/app/app/loads/page.tsx`, `web/app/app/loads/[id]/page.tsx` — read-only load list (search/paginate) and detail view, built in Phase 8. Own code comment: *"not a replacement for the full Dispatch Board."*
- `web/lib/api/useLoads.ts` — `useLoadSearch`, `useLoadDetail` query hooks (read-only).
- `web/lib/realtime/invalidationMap.ts` — already invalidates `loadKeys.lists()/.detail()/.timeline()` on `load.status_changed`, `load.assignment_changed`, `load.updated` events. Board just needs to subscribe via the existing `useRealtimeChannels(["loads"])` pattern.
- `web/app/app/inbox/*`, `useInbox.ts` — establishes the mutation pattern to replicate (`useMutation` + `apiClient.post`, optimistic/pending UI, error surfacing).

### 2. Reusable FastAPI endpoints (`api/routers/loads.py`)
All role-gated (`READ_LOADS` = Dispatcher/Manager/Admin/Accounting; `MUTATE_OPERATIONS` = Dispatcher/Manager/Admin):
- `GET /loads/search` — paginated, filtered (status, service_flow, customer, driver_name, port, warehouse, invoice_status, search, delivery window), sorted.
- `GET /loads/{id}`, `GET /loads/{id}/detail`, `/{id}/documents`, `/{id}/communications`, `/{id}/timeline`.
- `POST /loads/{id}/transition` — status change + optional driver/truck, override/override_reason. Body: `{new_status, note, driver, truck, override, override_reason}`.
- `POST /loads/{id}/assign-driver` — assignment without status change (reapplies current status through the same transactional path). Body: `{driver, truck}`.

**Gap:** no endpoint to list active drivers (needed for an assignment dropdown) — see Task 4.

### 3. Existing application commands/queries
- `application/loads/commands.transition_load` — **the one canonical transition command**, wraps `services.dispatch_transition_service.apply_transition`. Already called by both `api/routers/loads.py` and `pages_app/dispatch_board.py` (converged in Phase 5B — do not create a second implementation).
- `application/loads/queries` — `list_loads`, `search_loads`, `get_load`, `get_load_detail`, `get_load_documents`, `get_load_communications`, `get_load_timeline`.
- `application/loads/models.LoadFilters` — filter contract already covers everything a board needs (status, driver_name, port, warehouse, etc.).
- `application/documents/commands.attach_load_document`, `application/dispatch/commands.py` — used by the Streamlit board; in scope to reuse, not re-API'd in this phase unless the board needs document attach (not required for list/assign/transition — excluded, see Task 4 exclusions).

### 4. Dispatch capabilities still missing (Next.js side)
- No board/column UI (grouped by `services/dispatch_stages.py::SHARED_STAGES`).
- No driver/truck assignment UI or mutation hook.
- No status-transition UI or mutation hook (with override flow).
- No active-drivers list source for an assignment dropdown (service exists, pandas-only, un-API'd).
- No pickup/delivery detail surfaced in list/card view beyond what `LoadListItemOut` already carries (it already includes `delivery_need_date`, `document_cutoff`; booking/container/customer/port/warehouse).
- No realtime-driven optimistic card movement between columns (invalidation exists; board UI doesn't exist to consume it).

### 5. Legacy Streamlit dependencies still necessary (today)
- `services/dispatch_*.py`, `driver_roster_service.py`, `tms_data_service.py` — **zero `streamlit` imports**, already framework-neutral, already reused by `application/loads/commands`. Keep; they are shared business logic, not legacy UI.
- `db_client.py` (root) — imported directly by `dispatch_data_service`, `dispatch_transition_service`, `dispatcher_workspace_service`, `tms_data_service`, `driver_roster_service`. Still production-required.
- `workers/processor.py`, `workers/inbox_handlers.py` — framework-neutral by design (Phase 7), unrelated to Streamlit, unaffected by this phase.

### 6. Obsolete Streamlit UI eligible for retirement (after Phase 10B ships)
- `pages_app/dispatch_board.py` (1112 lines) — UI-only once Next.js board reaches parity. Its imported business logic (application/services) stays.
- `pages_app/dispatcher_workspace.py` + `ui_components/dispatcher_action_panel.py` — "AI Dispatcher Workspace" UI. Out of scope for *this* phase's parity target (see Task 3) — do not retire yet.
- `pages_app/router.py` lines registering "Dispatch Board" / "AI Dispatcher Workspace" sections — update only after the replacement ships and is accepted, not during this phase.

---

## Task 2 — Phase 10B Design: Dispatch Board

### Load list & status visibility
- New route `web/app/app/dispatch/page.tsx`. Board view = columns per `SHARED_STAGES` (Ready to Dispatch → En Route to Pickup → At Pickup → En Route to Delivery → At Delivery → Returning Empty → Completed), minus "Returning Empty" for non-Import service flows (mirrors `get_operational_stages(move_type)` in `services/dispatch_stages.py` — do not reimplement this rule client-side; expose it via the API, see Task 4 gap #2).
- Cards grouped by `status` from `LoadListItemOut`; each card shows booking number, customer, container/reference, port/warehouse, driver_name, truck_assigned, delivery_need_date, document_cutoff.
- Backed by `useLoadSearch` (already exists) with `status` left unset and client-side bucketing into columns, OR a dedicated board query if list size requires it (decide during implementation based on current load volume — default to reusing `useLoadSearch` with a higher limit/no pagination for the board view, since `/loads/search` already supports `limit` up to 200).

### Driver/truck assignment
- New `useAssignDriver` mutation hook → `POST /loads/{id}/assign-driver`.
- New `useActiveDrivers` query hook → new `GET /drivers/active` endpoint (gap, see Task 4).
- Assignment UI: inline dropdown/modal on a card, populated from `useActiveDrivers`, free-text truck field (matches existing `AssignDriverIn.truck: str | None`).

### Load status transitions
- New `useTransitionLoad` mutation hook → `POST /loads/{id}/transition`.
- UI: per-card action menu offering only the next valid status(es). Validity determined server-side (`apply_transition` already enforces skip/backward/override rules via `dispatch_stages.py`) — client requests the transition, surfaces the server's rejection message verbatim on 409/422 rather than re-deriving the rule set.
- Override path: if the server rejects with a "requires an override" class of error, surface an explicit confirm dialog that resubmits with `override: true, override_reason: <required text>`. Never auto-override.

### Pickup and delivery information
- Already present on `LoadListItemOut`/`LoadDetailOut` (confirmed in audit). Card shows summary fields; clicking through to `/app/loads/[id]` (existing detail page) shows full detail — no duplication of the detail page's responsibility.

### Search and filtering
- Reuse `LoadFilters` wholesale: status, service_flow, customer, driver_name, port, warehouse, invoice_status, search, delivery_after/before. Board gets a filter bar equivalent to the existing Loads page's search box, extended with the additional `LoadFilters` fields already supported server-side but not yet exposed in `useLoadSearch`'s filter param set (today `useLoadSearch` only passes status/customer/search — extend it, see Task 4).

### Realtime updates
- `useRealtimeChannels(["loads"])` (existing pattern). `invalidationMap.ts` already invalidates on `load.status_changed`/`load.assignment_changed`/`load.updated` — no realtime infra changes required. Board re-renders via normal React Query refetch on invalidation; no custom websocket handling needed in the board component.

### Authorization
- Read (list/board visibility): `READ_LOADS` role set — already enforced server-side on every GET.
- Mutations (assign, transition): `MUTATE_OPERATIONS` role set — already enforced server-side on both POST endpoints.
- Client-side: hide/disable assignment and transition controls for roles outside `MUTATE_OPERATIONS` (Accounting is read-only) — cosmetic only; the real gate is the API's existing `require_role` dependency. Do not add a second authorization system client-side.

### Error handling
- Mutation hooks surface the API's error payload message directly (pattern already used in `useInbox.ts` mutations) — no custom error-string mapping.
- Network/transient failures: existing `ConnectionIndicator` pattern (already used on the Loads page) reused on the board for realtime channel state.
- Optimistic UI: none for transitions (status rules are server-authoritative; optimistic card movement risks showing an invalid state if the server rejects). Show a pending/disabled state on the card during the mutation instead.

### Acceptance tests (required)
1. Board renders columns matching `get_operational_stages` for a given load's service_flow (including the Import-only "Returning Empty" column).
2. Load with no driver/truck cannot transition to "En Route to Pickup" (server rejects; UI surfaces the rejection) — mirrors `dispatch_stages.py` line ~71 rule.
3. Successful assign-driver updates the card's driver/truck without a page reload, within one realtime invalidation cycle.
4. Successful transition moves the card to the new column without a page reload.
5. Skip-ahead/backward transition without override is rejected; override flow with a required reason succeeds and is recorded (verify via `/loads/{id}/timeline`).
6. A user with only `READ_LOADS` (Accounting) cannot see assign/transition controls, and a direct API call from that role returns 403.
7. Filter bar narrows the board using at least: status, driver_name, port, warehouse, delivery window — verified against `/loads/search` query params.
8. Realtime: a transition made by one session is reflected on a second session's board within one invalidation cycle (reuses the existing e2e realtime fixture pattern from `web/e2e/inbox-realtime.spec.ts`).

---

## Task 3 — Streamlit Retirement Plan

| Item | Classification | Dependency risk |
|---|---|---|
| `pages_app/dispatch_board.py` | **Already replaced** once Phase 10B ships acceptance tests 1–8 | Its business-logic imports (`application.loads.commands`, `application.dispatch.commands`, `application.documents.commands`) are shared — removing this file must not touch those modules. |
| `pages_app/router.py` dispatch routing entry | **Already replaced** (retire together with dispatch_board.py) | `router.py` also routes unrelated sections (inbox, admin, etc.) — edit only the dispatch-specific `elif` branch, do not touch the file wholesale. |
| `pages_app/dispatcher_workspace.py`, `ui_components/dispatcher_action_panel.py`, `services/dispatcher_workspace_service.py` | **Required and not yet replaced** | "AI Dispatcher Workspace" has no Next.js equivalent and is explicitly out of scope for Phase 10B (AI development excluded). Do not touch. |
| `services/dispatch_*.py`, `driver_roster_service.py`, `tms_data_service.py` | **Required, not Streamlit** | Zero `streamlit` imports; actively called by `application/loads/commands` (used by both old and new UI). Never classify these as retirement candidates. |
| `db_client.py` (root) | **Required** | Imported by the services above. |
| `pages_app/admin.py` | **Unaffected** | Confirmed zero dispatch/load content — no entanglement with this phase. |

**Deletion rule applied per CLAUDE.md:** no deletion proposed in this plan. Actual removal of `dispatch_board.py` and its router entry is deferred to a follow-up phase *after* Phase 10B's acceptance tests pass and a human confirms parity — this plan only identifies the eligible file, it does not schedule its deletion.

---

## Task 4 — Phase 10B Development Plan

### API gaps to close
1. **`GET /drivers/active`** (new router `api/routers/drivers.py`, or add to `loads.py` if the team prefers no new router) — thin wrapper: new `application/drivers/queries.py::list_active_drivers()` calling the existing `services/driver_roster_service.list_active_drivers()`, which already queries `select driver_name, phone, truck_number from drivers where status = 'Active'`. New `DriverOut` schema: `{driver_name: str, phone: str, truck_number: str}` (confirmed columns — no DB change needed, the `drivers` table and roster service already exist). Role-gated `READ_LOADS` (same readers as the board).
2. **`GET /loads/stages?service_flow=...`** or embed `operational_stages` in `LoadDetailOut`/board response — expose `get_operational_stages(move_type)` so the client never reimplements the Import-vs-other stage-list rule. Prefer embedding on the detail/list response over a new endpoint if it fits without a breaking schema change; otherwise a small dedicated endpoint.

### Files to create
- `web/app/app/dispatch/page.tsx` — board page.
- `web/components/dispatch/DispatchColumn.tsx`, `DispatchCard.tsx`, `AssignDriverDialog.tsx`, `TransitionMenu.tsx` — board UI components.
- `web/lib/api/useDispatch.ts` — `useActiveDrivers`, `useAssignDriver`, `useTransitionLoad` hooks (mutation pattern copied from `useInbox.ts`).
- `api/routers/drivers.py` + `api/schemas/drivers.py` — new active-drivers endpoint.
- `application/drivers/queries.py` — new application query wrapping `driver_roster_service.list_active_drivers()`.
- `docs/architecture/DISPATCH_BOARD_WEB.md` — short design-record doc once implementation lands (mirrors `OPERATIONS_INBOX_WEB.md`'s existing pattern), not written in this planning phase.

### Files to modify
- `web/lib/api/useLoads.ts` — extend `useLoadSearch` to pass through the full `LoadFilters` set already supported server-side (driver_name, port, warehouse, invoice_status, delivery_after/before), not just status/customer/search.
- `web/lib/api/queryKeys.ts` — extend `LoadSearchFilters` type to match.
- `api/schemas/loads.py` — only if `LoadListItemOut`/`LoadDetailOut` need `operational_stages` embedded (gap #2); otherwise no change.

### UI components (new, per above) — no changes to existing `web/app/app/loads/*` required; board is additive.

### Test requirements
- `web/tests/` (vitest) — unit tests for the invalidation map's existing `LOAD_EVENT_TYPES` behavior already exist; add unit tests for new mutation hooks (`useAssignDriver`, `useTransitionLoad`) following `web/tests/open-attachment.test.ts`'s mocking pattern.
- `web/e2e/` (Playwright) — new `dispatch-board.spec.ts` covering acceptance tests 1–8 above, built on the existing `web/e2e/fixtures/qa-sample-data.ts` and `auth.ts` fixtures (do not create parallel fixtures).
- Backend: `tests/` — unit/integration tests for `application/drivers/queries.list_active_drivers` and the new `GET /drivers/active` endpoint (role gating, empty-result case). No changes to `transition_load`/`assign_driver` command tests needed since those commands are unmodified.

### Acceptance criteria
- All 8 acceptance tests in Task 2 pass in CI (extends the existing PR-validation workflow — tsc/eslint/vitest/pytest).
- No modification to `application/loads/commands.py`, `services/dispatch_transition_service.py`, or `services/dispatch_stages.py` — this phase consumes them, it does not change transition semantics.
- `pages_app/dispatch_board.py` remains fully functional and routed throughout this phase (no Streamlit removal in Phase 10B).

### Explicit exclusions
- No Motive integration.
- No SMS (driver notification on assignment/transition is out of scope).
- No AI/dispatcher-feedback work (`pages_app/dispatcher_workspace.py` and its service are untouched).
- No deletion of any Streamlit file — Task 3's "eligible" classification is informational, scheduled for a later phase after human-confirmed parity.
- No database schema or migration change.
- No changes to `application/loads/commands.transition_load`'s signature or validation rules.

---

## Open questions for approval before implementation
1. Should `GET /drivers/active` live in a new `api/routers/drivers.py`, or be added to the existing `loads.py` router? (Plan above defaults to a new router for separation of concerns; either is consistent with current architecture rules.)
2. Confirm whether board should fetch via existing `/loads/search` with a high limit (simplest, reuses everything) versus a dedicated unpaginated board endpoint — depends on current total active-load volume, which this audit did not query against a live database. Given CaliTrans' fleet size (10-20 drivers, correspondingly small active-load count), `/loads/search?limit=200` is very likely sufficient and is the default recommendation.
3. Confirm current branch strategy: this plan audits `origin/master` (8054945), which already includes PR #15-#18 and #21 (SMS gate, inbox sync producer, feedback-persistence fix, psycopg3 driver, workflow chore). The locally checked-out `integration/pr12-pr22-pr23` branch does **not** include those five commits. Phase 10B implementation should branch from `origin/master`, not from `integration/pr12-pr22-pr23`, to avoid silently dropping that already-merged work.
