# Operations Inbox Web Migration (Phase 10A)

## Status: primary dispatcher Inbox workflow available in Next.js; Streamlit Inbox remains the fallback during acceptance

## Architecture

```text
Email
  |
  v
Worker runtime (workers/inbox_handlers.py, scheduled via
.github/workflows/process-jobs.yml, JOB_HANDLERS["inbox.process_message"])
  |
  v
parse / segment (CASE-010) / classify / match
  |
  v
PostgreSQL (order_intake, operations_cases, work_queue column)
  |
  v
FastAPI (/api/v1/work-items - read model + application commands)
  |
  v
Next.js Operations Inbox (/app/inbox)
  ^
  |
Supabase Realtime Broadcast (inbox.received, inbox.review_status_changed)
  -> query invalidation -> FastAPI refetch
```

## Data ownership

- **Worker**: all processing (fetch, parse, classify, match, persist). Runs independently of both clients on a schedule; neither Streamlit nor Next.js triggers it in normal use.
- **PostgreSQL**: business state, including the persisted `order_intake.work_queue` column that already centralizes queue-taxonomy derivation - the same source both clients read, so Next.js can never drift onto a second, conflicting queue definition.
- **FastAPI** (`api/routers/work_items.py`): the read/command boundary. Every list/detail/conversation/attachment read and every create-load/update-load/link-load/close/draft-edit mutation goes through this router; nothing in the browser talks to Postgres directly.
- **Next.js**: presentation and workflow UX only - no parsing, classification, matching, or business-rule logic duplicated in TypeScript.
- **Realtime**: invalidation signal only. `inbox.received` / `inbox.review_status_changed` broadcasts never carry the full work-item record; the client invalidates the matching query and refetches from FastAPI, which stays authoritative.

## What already existed vs. what Phase 10A added

The FastAPI read/command API for work items (`api/routers/work_items.py`, `application/work_items/`, `repositories/work_item_repo.py`) was already built and tested on `master` before this phase (Phase 5B/9 work) - list (paginated/sorted/filtered/searched), detail (with an allowlisted `parsed_data`, never the raw dict), conversation, attachment metadata (never a filesystem path), draft edit, and all four mutation actions, each behind `require_role(*MUTATE_OPERATIONS)` and `require_permission(Permission.WORK_ITEM_MANAGE)` inside the application command. Phase 10A's actual scope was:

1. One new endpoint, `GET /api/v1/work-items/counts` - a single bounded `GROUP BY work_queue` aggregation query (`repositories/work_item_repo.py::count_work_items_by_queue`), so queue-nav counts never do N+1 per-queue requests or load the full queue client-side.
2. The Next.js workspace itself (`web/app/app/inbox/page.tsx`, `web/components/inbox/WorkItemDetail.tsx`): two-pane desktop layout, queue navigation with backend-derived counts, server-side paginated/searched work-item list, URL-addressable selection (`/app/inbox?id=42&queue=New+Orders`), and the detail pane (header, extracted fields, match, conversation, attachments, actions).
3. Query-key factories (`inboxKeys.counts/.conversation/.attachments`) and realtime invalidation wiring for the two real inbox event types - `web/lib/realtime/invalidationMap.ts` already had `inbox.received`/`inbox.review_status_changed` -> list/detail invalidation from Phase 10; this phase added counts invalidation to the same block.

## Manual controls

The normal `/app/inbox` view has no Sync Email Engine, Refresh Inbox, or Recheck Next Batch controls - the same reasoning as the Streamlit transitional UX pass (see `pages_app/email_imports.py`'s "Manual Inbox Processing" expander): routine processing is fully automated via the scheduled worker, so a dispatcher never needs to trigger it manually. Those controls remain in Streamlit's Admin/Diagnostics only, gated to Manager/Admin roles plus an explicit `require_permission(WORK_ITEM_MANAGE)` check on the one action (Recheck Next Batch) with no application-command layer of its own.

## Permissions / access matrix

| Role | Can view `/app/inbox` | Can perform actions (Create/Update/Link/Close/Draft edit) |
|---|---|---|
| Dispatcher | Yes | Yes (`work_item:manage`) |
| Manager | Yes | Yes (`work_item:manage`) |
| Admin | Yes | Yes (`work_item:manage`, plus every other permission) |
| Accounting | Not evaluated this phase - `api/routers/work_items.py`'s router-level `READ_OPERATIONS` gate excludes accounting from the read side entirely (same as Streamlit); the frontend nav item has no route-level role check yet beyond backend 403s. |

Frontend permission checks (`hasPermission("work_item:manage")`, disabling action buttons) are UX only. The real boundary is `require_permission`/`require_role` enforced server-side inside `api/routers/work_items.py` and the application commands - proven by `web/e2e/inbox.spec.ts`'s second test (an accounting-role session sees every action button disabled) and the existing backend authorization tests (`tests/test_work_item_command_authorization.py`).

## Known gaps / deferred (not built this phase)

- **Owner assignment / review-status change as a standalone UI action**: no application command exists for this yet (only implicit via `close_work_item`/`link_work_item_to_load`). Not built - would require a new thin command, out of this phase's narrow scope per the mission brief.
- **Multi-container work-order creation** and **field-provenance reconciliation** (Streamlit's `create_container_work_orders`, `services/operations_field_service.py`): confirmed to have no FastAPI route in this phase's audit. Deferred - flagged as a real gap, not silently dropped.
- **Attachment inline preview**: the workspace links to the existing secure `GET /api/v1/attachments/{attachment_ref}/content` endpoint (opens in a new tab); no in-app PDF viewer was built.
- **Deep component-level unit tests** (QueueNav/WorkItemList/WorkItemDetail in isolation with mocked query state) were not written given this phase's time/context budget; the integrated behavior is covered by `web/e2e/inbox.spec.ts` (real render, mocked backend) plus `web/tests/inbox-query-keys.test.ts` and the extended `web/tests/realtime-invalidation.test.ts`.
- **Live two-browser realtime proof**: not executed this phase (requires a configured Supabase project) - same outstanding item already documented for the Phase 10 Loads proof.

## Streamlit transition

Streamlit's Operations Inbox (`pages_app/operations_inbox.py`) is **not removed or disabled** by this phase. It remains fully available as the transitional fallback. Next.js's `/app/inbox` is the primary candidate for daily dispatcher use going forward; removal/disabling of the Streamlit page is an explicit later closure step requiring user acceptance, not part of this phase.
