# Phase 10C — Complete Load Workspace (Next.js/FastAPI)

**Base:** `origin/master` @ `ff66f7f` (PR #25 merged — Phase 10B Dispatch Board + its 3 P1 fixes; PR #19 untouched).
**Mission:** replace the Streamlit "Orders/Load Management" workspace with a Next.js/FastAPI Load Workspace, reusing the existing application/API layer. Streamlit is legacy UI only — no operational fallback, no data migration needed.
**Out of scope (explicit exclusions):** Billing/ProfitTools (a separate Streamlit nav section — `log_dispatch_communication`/billing handoff status untouched), Motive integration, SMS sending, AI/dispatcher-feedback work, any DB schema/migration change, any Streamlit deletion beyond what Task 3 classifies as safe.

---

## Task 1 — Current State Audit

### 1. Already available in Next.js
- `web/app/app/loads/page.tsx` — read-only, filtered/searched load list (Phase 8/10B).
- `web/app/app/loads/[id]/page.tsx` — read-only detail view showing only 12 of `LoadDetailOut`'s ~28 fields (booking/reference/container/customer/status/driver/truck/port/warehouse/address/delivery_need_date/document_cutoff). **No edit, no notes, no documents, no communications, no timeline, no status actions anywhere on this page.**
- `web/app/app/dispatch/page.tsx` — Dispatch Board (Phase 10B): board view, driver/truck assignment, status transitions. Established patterns (mutation hooks, `ApiError`-verbatim surfacing, realtime invalidation, cosmetic permission gating) to copy, not reinvent.
- `web/lib/api/useDispatch.ts` — `useAssignDriver`/`useTransitionLoad` mutation pattern to mirror for the new load-edit/notes/document/communication mutations.

### 2. Reusable FastAPI endpoints (`api/routers/loads.py`)
All role-gated (`READ_LOADS` / `MUTATE_OPERATIONS`):
- `GET /loads/search`, `/{id}`, `/{id}/detail`, `/{id}/timeline`, `/{id}/communications`, `/{id}/documents` — all already exist and are read-only (Phase 8). **None of these have a write counterpart yet.**
- `GET /loads/stages` (Phase 10B) — reusable as-is if the workspace shows stage context.
- `POST /{id}/transition`, `/{id}/assign-driver` — Dispatch Board's, reusable if the workspace also wants quick dispatch actions, but not the focus here (Dispatch Board already owns that surface).

**Gap:** no write/mutation endpoint exists for load edit, notes, missing-info/verify/cancel, document upload, or communication logging. All five already have a canonical, authorization-gated application command (see #3) — only the API layer is missing.

### 3. Existing application commands/queries (all framework-neutral, zero Streamlit imports)
- `application/loads/commands.py`:
  - `update_load_fields(actor, load_id, updates)` — free-form multi-field save, `Permission.LOAD_EDIT`. This is the Booking Review / Order Detail Editor forms' save path.
  - `mark_load_missing_info(actor, load_id, note="")` — `Permission.LOAD_EDIT`.
  - `verify_load_booking(actor, load_id, note="")` — `Permission.LOAD_VERIFY`.
  - `cancel_load(actor, load_id, note="")` — `Permission.LOAD_CANCEL`.
  - `save_load_note(actor, load_id, note)` — notes-only save, `Permission.LOAD_EDIT`.
  - All four write via one shared helper (`_update_load_fields_with_event`) that records a `load.updated` domain event in the same transaction as the write — **realtime invalidation is already wired for every one of these**, no new event-publishing code needed.
- `application/documents/commands.py::attach_load_document(actor, load_id, uploaded_file, source)` — `Permission.DOCUMENT_ATTACH`. Staged-write + checksum + outbox-finalize pattern (Phase 6B); publishes `document.file.finalize`, and the outbox processor later flips status to `available`/`failed` (both already in `invalidationMap.ts`'s `DOCUMENT_EVENT_TYPES`).
- `application/dispatch/commands.py::log_dispatch_communication(actor, load_id, message_type, direction, recipient, message_body, provider="internal")` — `Permission.DISPATCH_COMMUNICATION_LOG`. Covers the Streamlit "Save Message / Driver Communication / Customer Note / Operational Note" buttons; writes to `dispatch_messages` via `services.dispatch_data_service._insert_dispatch_message`.
- `application/loads/queries.py` — `get_load_detail`, `get_load_timeline`, `get_load_communications`, `get_load_documents` all already exist, paginated where appropriate.

**None of these commands need to change.** Phase 10C is almost entirely "add a thin API router wrapper + a Next.js UI" over commands that already exist, are already tested, and are already authorization-gated — the same shape as Phase 10B's `GET /drivers/active`/`GET /loads/stages` additions.

### 4. Load Workspace capabilities still missing (Next.js side)
- No edit form (any field).
- No quick-action buttons (Mark Missing Info / Mark Booking Verified / Cancel).
- No notes editor.
- No documents tab (list exists via API; no upload UI, no upload API endpoint).
- No communications tab (list exists via API; no "log a note" UI, no logging API endpoint).
- No timeline tab (list exists via API; no UI at all).
- Detail page doesn't surface ~16 existing `LoadDetailOut` fields (chassis, size, billing_notes, dispatcher_notes, invoice_status, driver_pay_status, closeout_stage, steamship_line, vessel_name, terminal, pickup/delivery_appointment, empty_return_location/date, parent_booking_key, container_sequence/total).
- `LoadDetailPage` only subscribes to `["loads", "load:{id}"]` — not the `"documents"` or `"communications"` collection channels (`lib/realtime/channels.ts::collectionChannel` already maps `document`→`"documents"` and `dispatch_message`→`"communications"`), so even once a documents/communications tab exists it would miss live updates unless the workspace subscribes to those channels too.

### 5. Legacy Streamlit dependencies still necessary (today)
- `application/loads/commands.py`, `application/documents/commands.py`, `application/dispatch/commands.py::log_dispatch_communication` — framework-neutral, already shared, stay as the one canonical implementation for both UIs during the transition.
- `services/document_storage_service.py`, `services/dispatch_data_service.py`, `repositories/document_repo.py`, `repositories/outbox_repo.py` — zero `streamlit` imports, required.
- `db_client.py` (root) — required, used by the above.

### 6. Obsolete Streamlit UI eligible for retirement (after Phase 10C ships) — with one confirmed pre-existing finding
- `pages_app/orders_management.py::_render_order_detail_editor` + `_render_ready_to_dispatch_panel` + the "Quick Actions" block — **correctly** authorized (uses `has_permission`/the canonical commands above). Eligible for retirement once the Next.js workspace reaches parity.
- **`pages_app/orders_management.py::render_booking_review` is a confirmed, pre-existing security gap, unrelated to Phase 10B/10C's own changes**: its "Save Booking Review", "Mark Missing Info", "Mark Booking Verified", "Save Notes", and "Cancel Booking" actions all call `DispatchDatabaseClient().update_row_fields(...)` **directly**, bypassing `update_load_fields`/`mark_load_missing_info`/`verify_load_booking`/`cancel_load` entirely — the function doesn't even receive an `AuthenticatedActor` (`render_booking_review(df: pd.DataFrame) -> None`, no `principal` parameter), so there is **no authorization check of any kind** on this code path today. This is not something Phase 10C introduces or is required to fix, but: (a) Phase 10C's Next.js workspace must **not** copy this pattern — it goes through the canonical commands exclusively, like the Dispatch Board already does; (b) this is worth a human decision on whether to patch it in Streamlit now or accept the risk until this page is retired — flagged here, not fixed, per this plan's own scope.
- `pages_app/dispatch_board.py`'s communication-log buttons (`can_log_comms` sections) — become redundant once the Load Workspace's Communications tab ships; not touched by this plan.

---

## Task 2 — Phase 10C Design: Load Workspace

### Load detail / view
- Extend `web/app/app/loads/[id]/page.tsx` (or a new `LoadWorkspace` component it renders) to show the full `LoadDetailOut` field set, grouped (Booking/Container, Route, Status/Assignment, Billing-adjacent-but-view-only fields like `invoice_status`/`driver_pay_status` — view only, no billing mutations per exclusions).
- Tabs: **Detail** (view + edit), **Documents**, **Communications**, **Timeline** — mirrors the data already split across `get_load_detail`/`get_load_documents`/`get_load_communications`/`get_load_timeline`, so each tab is its own independently-loading query exactly as today's API already shapes it (no new eager joins).

### Edit
- New `useUpdateLoadFields` mutation hook → new `PUT /loads/{id}` (or `PATCH`) wrapping `update_load_fields` 1:1. Free-form `updates: Record<string, unknown>` body, same as the Streamlit form already sends.
- Inline notes save (`save_load_note`) as a smaller, separate action — matches Streamlit's "Save Notes" button being distinct from the full form save.

### Status quick actions
- Three buttons — Mark Missing Info, Mark Booking Verified, Cancel Load — each a thin mutation hook to a new endpoint wrapping `mark_load_missing_info`/`verify_load_booking`/`cancel_load` respectively. Each takes an optional `note`.
- Client-side cosmetic gating: `hasPermission("load:edit")`, `hasPermission("load:verify")`, `hasPermission("load:cancel")` (same pattern as Dispatch Board's `hasPermission("dispatch:transition")`) — server-side `require_permission` is the real boundary, unchanged.

### Documents
- List: reuse existing `GET /loads/{id}/documents` as-is (new `useLoadDocuments` query hook).
- Upload: new `POST /loads/{id}/documents` (multipart) wrapping `attach_load_document`. Requires a small adapter in the new router — FastAPI's `UploadFile` exposes `.filename` (not `.name`) and an async `.read()`; `stage_upload()` expects a sync `.name`/`.seek()`/`.read()` object. Adapter: wrap `UploadFile.file` (the underlying `SpooledTemporaryFile`, which already has sync `.seek()`/`.read()`) in a tiny shim exposing `.name = upload.filename`. No change to `stage_upload`/`attach_load_document` themselves.
- Upload status (`pending`/`available`/`failed`) is realtime-driven already (`document.available`/`document.failed` in `invalidationMap.ts`) — the UI shows a pending state and lets the existing invalidation refresh it, no polling needed.

### Communications
- List: reuse existing `GET /loads/{id}/communications` as-is.
- Log a note: new `POST /loads/{id}/communications` wrapping `log_dispatch_communication` (`message_type`, `direction`, `recipient`, `message_body`; `provider` defaults to `"internal"` server-side, not client-settable — this is for logging, not sending).

### Timeline
- Reuse existing `GET /loads/{id}/timeline` as-is; new read-only tab UI only, no new backend work.

### Authorization
- Identical two-layer pattern already established: route-level `require_role(*MUTATE_OPERATIONS)` + command-level `require_permission` (already present in every command being wrapped) for every new endpoint. No new permission values needed — `LOAD_EDIT`, `LOAD_VERIFY`, `LOAD_CANCEL`, `DOCUMENT_ATTACH`, `DISPATCH_COMMUNICATION_LOG` already exist and already have the correct role grants (Accounting has `DOCUMENT_ATTACH` but not the others — matches Accounting's existing "view loads, attach billing documents" scope from the Phase 5B permission matrix, unchanged).

### Realtime
- Subscribe the workspace to `["loads", "documents", "communications", resourceChannel("load", id)]` — the first three are existing collection channels (`collectionChannel("document")`/`collectionChannel("dispatch_message")` already map to them), the board/detail pages just never subscribed to the last two before because there was nothing on screen that needed them. No backend/channel changes.

### Error handling
- Identical `ApiError`-message-verbatim pattern already used by `AssignDriverDialog`/`TransitionMenu`. No new error-shape work.

### Acceptance tests (required)
1. Full `LoadDetailOut` field set renders (not just the current 12).
2. Edit form save updates fields and is reflected after one realtime invalidation cycle, without a page reload.
3. Mark Missing Info / Mark Booking Verified / Cancel each succeed for an authorized role and are rejected (403 at the route, no mutation) for an unauthorized role (Accounting).
4. A rejected edit (e.g. empty required field — mirrors `update_load_fields`'s existing `ValidationError` for an empty `updates` dict) surfaces the server's message verbatim.
5. Document upload: a PDF attaches, shows `pending`, then flips to `available` after the outbox processor runs (or is mocked to in tests) without a manual refresh.
6. Communication log: a note is added and appears in the Communications tab without a manual refresh.
7. Timeline tab renders existing events; a new status/assignment/edit action appends a new timeline entry after invalidation.
8. A user with only `DOCUMENT_ATTACH` (Accounting) can upload a document but cannot see Edit/Mark-Missing-Info/Verify/Cancel controls.

---

## Task 3 — Streamlit Retirement Plan

| Item | Classification | Dependency risk |
|---|---|---|
| `pages_app/orders_management.py::_render_order_detail_editor`, `_render_ready_to_dispatch_panel`, Quick Actions | **Already replaced** once Phase 10C ships acceptance tests 1–4, 8 | Imports `application.loads.commands` — shared, do not touch when retiring the Streamlit file. |
| `pages_app/orders_management.py::render_booking_review` | **Confirmed security gap (pre-existing) + will be replaced** | Bypasses every canonical command via direct `DispatchDatabaseClient().update_row_fields()` with zero authorization check. Not fixed in this plan (human decision needed on patch-now vs. accept-until-retirement) — flagged, not touched. |
| `pages_app/dispatch_board.py`'s communication-log buttons | **Already replaced** once the Communications tab ships | `log_dispatch_communication` import is shared with Dispatch Board — do not remove the command, only the Streamlit button once retired. |
| `services/dispatch_data_service.py`, `services/document_storage_service.py`, `repositories/document_repo.py`, `repositories/outbox_repo.py` | **Required, not Streamlit** | Zero `streamlit` imports; actively used by the commands above from both UIs. Never retirement candidates. |
| `pages_app/documents.py::render_pdf_intake` | **Confirmed dead** (per existing `tests/test_direct_mutation_scanner.py` allowlist comment) | Not part of this phase's scope to delete — noted for a separate dead-code pass per CLAUDE.md's "delete only confirmed dead" rule requiring its own verification sweep. |

**Deletion rule applied per CLAUDE.md:** no deletion proposed in this plan. Actual removal is deferred to a follow-up phase after acceptance tests pass and a human confirms parity.

---

## Task 4 — Phase 10C Development Plan

### API gaps to close
1. `PUT /loads/{id}` — wraps `update_load_fields`. New `UpdateLoadFieldsIn{updates: dict[str, Any]}` schema (mirrors the existing work-item `UpdateLoadIn` shape, distinct endpoint).
2. `POST /loads/{id}/notes` — wraps `save_load_note`. `{note: str}`.
3. `POST /loads/{id}/mark-missing-info`, `/verify-booking`, `/cancel` — each wraps its command, `{note?: str}` body.
4. `POST /loads/{id}/documents` (multipart) — wraps `attach_load_document` via the `UploadFile`→`stage_upload` adapter described in Task 2.
5. `POST /loads/{id}/communications` — wraps `log_dispatch_communication`. `{message_type, direction, recipient, message_body}` — `provider` fixed server-side to `"internal"`, not accepted from the client.

### Files to create
- `api/schemas/load_workspace.py` (or extend `api/schemas/loads.py`) — the 5 new `*In`/`*Out` schemas above.
- `web/app/app/loads/[id]/documents/`, `.../communications/`, `.../timeline/` tab components (or a single tabbed component — decide at implementation time based on routing preference).
- `web/components/load-workspace/{EditForm,NotesEditor,QuickActions,DocumentUpload,DocumentList,CommunicationLog,Timeline}.tsx`.
- `web/lib/api/useLoadWorkspace.ts` — the 5 new mutation hooks + `useLoadDocuments`/`useLoadCommunications`/`useLoadTimeline` query hooks (queries already exist server-side; only the frontend hooks are new).

### Files to modify
- `api/routers/loads.py` — add the 5 endpoints above.
- `api/main.py` — none needed (same router).
- `web/app/app/loads/[id]/page.tsx` — expand to the full field set + tabs.
- `web/lib/api/types.ts`, `web/lib/api/queryKeys.ts` — new type re-exports and query-key factories for documents/communications/timeline (realtime invalidation already targets these keys — `loadKeys.documents`/`communications`/`timeline` already exist in `queryKeys.ts` from Phase 8, unused until now).

### Test requirements
- Backend: new `tests/test_api_load_workspace.py` (API-layer, dev-mode `TestClient`, mirrors `test_api_drivers.py`'s pattern) covering the 5 new endpoints' happy path, role-gating (403), and validation-error (422) cases. No new command-layer tests needed — the commands themselves are unchanged and already tested.
- Frontend: `web/tests/use-load-workspace.test.tsx` (mirrors `use-dispatch.test.tsx`'s pattern) for the 5 new hooks. `web/e2e/load-workspace.spec.ts` (mirrors `dispatch-board.spec.ts`'s mocked-backend pattern) for the 8 acceptance tests above.

### Acceptance criteria
- All 8 acceptance tests in Task 2 pass in CI (tsc/eslint/vitest/pytest).
- No change to any `application/loads/commands.py`, `application/documents/commands.py`, or `application/dispatch/commands.py` function signature or validation rule — this phase wraps them, it does not change them.
- `pages_app/orders_management.py` remains fully functional and routed throughout this phase (no Streamlit removal in Phase 10C).

### Explicit exclusions
- No Billing/ProfitTools work (the "Ready for ProfitTools" handoff status and billing fields stay view-only).
- No Motive integration, no SMS, no AI/dispatcher-feedback work.
- No database schema or migration change.
- No fix to `render_booking_review`'s authorization gap — flagged for a separate, explicitly-scoped security fix.
- No deletion of any Streamlit file.

---

## Open questions for approval before implementation
1. Should document upload go through a dedicated `api/routers/documents.py` (new router) or stay inside `api/routers/loads.py` as `/loads/{id}/documents` POST? Plan above defaults to the latter (keeps the resource under `/loads`, consistent with the existing GET).
2. Should `render_booking_review`'s authorization gap be patched now (small, isolated fix — wire it through the same commands `_render_order_detail_editor` already uses) as a prerequisite, or explicitly accepted as a known risk until Streamlit retirement? Recommend patching now given it's a live, reachable, unauthenticated-mutation path — but this is a business/risk decision, not an engineering one, and is called out rather than assumed.
3. Tab routing: nested routes (`/app/loads/[id]/documents`) vs. a single page with client-side tab state. Either is consistent with current architecture; recommend client-side tabs (simpler, matches the Inbox page's existing two-pane pattern) unless deep-linking to a specific tab is a real requirement.
