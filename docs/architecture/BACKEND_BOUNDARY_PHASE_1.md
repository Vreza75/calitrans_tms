# Calitrans Backend Boundary and Performance Foundation — Phase 1

Status: implemented on `architecture/backend-boundary-phase-1`, branched from
`review/current-calitrans-build-20260803` (`6b601dc`).

This is **not** a rewrite. The Streamlit dispatcher/admin app is unchanged in
behavior; this phase adds a framework-neutral layer underneath it and expands
the FastAPI surface on top of that same layer, so a future Next.js dispatcher
UI has something real to call without duplicating business rules.

## Previous architecture

```
Streamlit pages (pages_app/*.py)
    -> services/*.py  (business logic + direct SQL + Streamlit rendering,
                        often in the SAME function/module - e.g.
                        services/operations_inbox_service.py mixes SQL
                        queries, @st.cache_data, and render_* functions
                        that call st.* directly)
    -> db_client.py / database/db_client.py (SQLAlchemy engine, read_df/execute)
    -> Supabase/PostgreSQL

api/main.py (FastAPI) -> db_client.py directly, 2 endpoints, no shared
                          logic with the Streamlit path
```

Concretely, before this phase:

- `services/operations_inbox_service.py` (5,982 lines) and
  `pages_app/operations_inbox.py` (5,440 lines) mix five concerns in the same
  files: SQL queries, Pandas transforms, classification/business rules, IMAP
  sync, and Streamlit rendering.
- `pages_app/operations_inbox.py` called `ops.ensure_operations_email_sync_schema()`
  on every `render_operations_inbox()` invocation.
- The Operations Queue loaded the entire open queue (`load_operations_inbox_df`,
  no `LIMIT`/`OFFSET`) into Pandas on every rerun, then rendered an `Open`
  button for every row.
- 19 call sites across 7 files called `st.cache_data.clear()` (wipes every
  `@st.cache_data` function in the whole app - Admin, Dispatch Board, Orders
  Management, Port Houston integration, Documents, order intake - not just
  Operations Inbox) after a single Operations Inbox action.
- `config.py` imported `streamlit` at module top. `db_client.py` imports
  `config.py`. Every repository and service imports `db_client.py`. So
  **nothing** touching the database could avoid importing Streamlit, even
  code with no UI concerns at all.
- `api/main.py` was 69 lines: `/health`, `GET /loads`, `POST /loads`, talking
  to `db_client.py` directly with no shared application layer.
- `dispatch_transition_service.apply_transition()` wrote a driver/truck
  assignment, its audit row, the status change, and the closeout-stage flag
  as four separate, independently-committed statements - a crash between any
  two of them left inconsistent state (e.g. driver assigned, no audit row).
- The Admin UI collected and wrote a plaintext Motive password on every
  driver save.

## Phase 1 architecture

```
Streamlit dispatcher/admin UI (unchanged pages, new: pagination controls,
    targeted cache invalidation, timing sub-stages)
        |
        v
application/*  (framework-neutral: no streamlit import, no session_state,
    no st.cache_data, no rendering, no IMAP/AI/PDF-parsing during a read)
    work_items/  queries.py, commands.py, models.py
    loads/       queries.py, commands.py, models.py
    attachments/ queries.py, models.py
    conversations/ queries.py, models.py
    order_drafts/  queries.py, commands.py, models.py
        |
        v
repositories/*  (SQL only - no rendering, no streamlit)
    work_item_repo.py  (new: paginated/sorted/filtered queue queries,
                         conversation pagination, transactional writes)
    inbox_repo.py, load_repo.py  (pre-existing, already framework-neutral,
                                   reused rather than duplicated)
        |
        v
db_client.py  (+ new transaction() context manager for multi-statement
    business commands)
        |
        v
Supabase/PostgreSQL

FastAPI (api/main.py + api/routers/*.py, versioned under /api/v1)
        |
        v
the SAME application/* functions Streamlit calls (verified by identity
    assertion in tests/test_backend_boundary_architecture.py)
```

Both interfaces call into `application/*` in-process. Streamlit does not call
its own FastAPI server over HTTP, and FastAPI does not re-implement business
rules - this was an explicit Phase 1 requirement (Step 10 of the build spec).

## Folder responsibilities

| Path | Responsibility |
|---|---|
| `application/*/queries.py` | Read-only, framework-neutral. Pagination/sort/filter math, response assembly. No writes. |
| `application/*/commands.py` | Writes. Validate input, execute inside one `db_client.transaction()`, return a structured result. Raise `application.exceptions.*`, never render. |
| `application/*/models.py` | Plain `@dataclass(frozen=True)` DTOs - no Pydantic, no ORM, so this layer has zero framework coupling. |
| `repositories/work_item_repo.py` | New. All SQL for paginated/sorted/filtered work-item queries, conversation pagination, draft lookups, and the transactional write helpers (`update_review_status`, `link_to_load`, `insert_case_event`, `update_order_draft_fields`). |
| `api/routers/*.py` | Thin: parse request -> call one `application.*` function -> map the dataclass to a Pydantic response model. No SQL (enforced by `test_fastapi_routers_do_not_contain_raw_sql`). |
| `api/schemas/*.py` | Pydantic response/request models. Deliberately exclude raw file-system paths (`AttachmentMetaOut` has no `file_path`). |
| `api/errors.py` | Maps `application.exceptions.*` to HTTP status codes; catches everything else and returns a generic message (never the raw exception - database errors can contain connection strings). |
| `api/dependencies.py` | `get_current_actor()` - a placeholder actor resolver so routers depend on a named function now instead of a hardcoded string. No auth yet (see Known limitations). |

## Request flow (read)

```
GET /api/v1/work-items?page=2&page_size=25&sort_by=customer
    -> api/routers/work_items.py: list_work_items()
    -> application.work_items.queries.get_work_item_queue_page()
    -> repositories.work_item_repo.count_work_items() + list_work_items_page()
       (WHERE/ORDER BY/LIMIT/OFFSET all built in Postgres, bound params only)
    -> WorkItemPageOut Pydantic model
```

Streamlit's Operations Queue still runs its existing Python/Pandas
filter/classify/sort pipeline (see Known limitations - that pipeline was not
touched), but the **render** step is now paginated (`page_df = tab_df.iloc[...]`)
so only the current page's rows get an `Open` button drawn, instead of one
per historical item in the queue.

## Command flow (write)

```
POST /api/v1/work-items/{id}/close
    -> application.work_items.commands.close_work_item()
    -> with db_client.transaction() as conn:
           work_item_repo.update_review_status(..., conn=conn)
           work_item_repo.insert_case_event(..., conn=conn)   # only if case_id is set
       # both commit together, or both roll back together
    -> CommandResult(ok=True, work_item_id=..., detail="Closed")
```

The same pattern now applies to `link_work_item_to_load`,
`update_order_draft` (whitelisted columns, dispatcher-confirmed), and
`dispatch_transition_service.apply_transition()` (driver/truck assignment +
its audit row + status change + closeout-stage write, all through one
connection).

## Transaction boundaries added

| Command | Before | After |
|---|---|---|
| `dispatch_transition_service.apply_transition()` | 4 separate `execute()` calls (assignment write, assignment audit, status write, closeout write) | 1 `db_client.transaction()` - see `tests/test_dispatch_transition_service.py::test_assignment_status_and_audit_share_one_transaction` and `test_forced_failure_mid_transaction_rolls_back_status_write` |
| `application.work_items.commands.close_work_item()` | did not exist | 1 transaction: status update + case-event audit |
| `application.work_items.commands.link_work_item_to_load()` | did not exist | 1 transaction: load-existence check + link + case-event audit |
| `application.order_drafts.commands.update_order_draft()` | did not exist | 1 transaction, whitelisted columns only |

`db_client.DispatchDatabaseClient.update_row_fields()` gained an optional
`conn` keyword (default `None`, so every existing caller is unaffected) so
callers that need atomicity can pass a shared connection instead of letting
it open its own transaction.

## Streamlit adapter pattern

Streamlit pages call `application/*` functions directly, in-process - no HTTP
hop to the FastAPI server. This phase did **not** rewire the existing
Operations Inbox render/classification pipeline to call the new application
layer (see Known limitations); the new layer is fully wired into FastAPI and
proven against production data (read-only), and is the intended target for
Streamlit call sites in Phase 2.

## FastAPI routes (`/api/v1`)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | |
| GET | `/work-items` | Paginated/sorted/filtered queue |
| GET | `/work-items/{id}` | Consolidated detail (no IMAP/AI/PDF/write) |
| GET | `/work-items/{id}/attachments` | Metadata only, no bytes |
| GET | `/work-items/{id}/conversation` | Paginated; not loaded as part of detail |
| GET | `/work-items/{id}/draft` | |
| PUT | `/work-items/{id}/draft` | Whitelisted, dispatcher-confirmed fields only |
| POST | `/work-items/{id}/create-load` | Delegates to the existing canonical workflow (see Known limitations) |
| POST | `/work-items/{id}/close` | Transactional |
| POST | `/work-items/{id}/link-load` | Transactional |
| GET | `/loads` | |
| GET | `/loads/{id}` | |
| POST | `/loads/{id}/transition` | Transactional (see above) |
| POST | `/loads/{id}/assign-driver` | Re-applies current status through the same transactional path |
| GET | `/attachments/{ref}/content` | Reads bytes; `ref` is opaque (sha256 of the server path, truncated), never the raw path |

Legacy unversioned `/health` and `/loads` (GET/POST) are kept, tagged
`legacy` in `api/main.py`, and covered by `test_legacy_health_still_works`.

## Attachment-read behavior

Attachment **metadata** (filename, content type, `is_pdf`, an opaque
`attachment_ref`) is read from `parsed_data` JSON already persisted on
`order_intake` - no file I/O. Attachment **bytes** are read only by
`GET /api/v1/attachments/{ref}/content`, which resolves `ref` back to a
server path by re-deriving refs for that work item's own attachments and
matching (never by accepting a path from the caller) - see
`application/work_items/queries.py::attachment_ref()` and
`get_attachment_content()`. Proven by
`tests/test_work_item_queries.py::test_attachment_summary_reads_metadata_without_reading_bytes`
and `tests/test_api_work_items.py::test_attachments_response_never_includes_a_raw_file_path`.

## Migration/startup procedure

Unchanged by this phase: `scripts/run_migrations.py` against a target
`DATABASE_URL`. What changed is *when* the Operations Inbox's own
idempotent `ensure_*_schema()` ALTER/CREATE INDEX chains run:

- Readiness is now cached at **process** level (`_SCHEMA_READY_FLAGS`
  module-level `set` in `services/operations_inbox_service.py`), not just
  `st.session_state`. One Streamlit worker serves many browser sessions;
  previously every new session re-paid a `column_exists()` round trip even
  seconds after another session in the same process had confirmed the
  schema.
- The full ALTER/CREATE INDEX chain still only runs the first time a given
  process finds the schema NOT ready (self-healing, idempotent, unchanged
  from before) - normal page rendering never runs DDL once the schema is
  confirmed. Measured cost of the steady-state check:
  `column_exists('order_intake', 'case_id')` = 159–410 ms per call in this
  environment (single `SELECT` against `information_schema.columns`, not a
  DDL statement) - see Performance measurements.

## Performance measurements

Read-only, against the real production Supabase database (no writes, no
schema changes) - 111 open work items at measurement time. Script:
`tests/../` scratch measurement, reproducible via
`application.work_items.queries` + `repositories.work_item_repo` calls.
Network latency from this dev environment to Supabase appears to run
~150–400 ms per round trip; the *relative* improvement is the meaningful
number, not the absolute ms on a different network path.

| Measurement | Before (old pattern) | After (new pattern) |
|---|---|---|
| Full open-queue load, no pagination (111 rows, all columns) | 2,340.53 ms | — |
| Paginated queue: `count_work_items` + `list_work_items_page` (25 rows) | — | 409.01 ms (240.09 + 168.92) |
| Work-item detail fetch (`get_work_item_detail`) | not previously isolated as one call | 495.88 ms warm / 504.57 ms cold (first call pays a one-time lazy-import cost) |
| Attachment metadata only (`get_attachment_summary`) | not previously isolated | 173.81 ms |
| Schema readiness check (already applied) | full ALTER/CREATE INDEX chain risk on every render (mitigated already by a session-level flag) | 159.49 ms, one `SELECT`, now cached at process level |
| Cached queue read, repeat Streamlit rerun within 30s TTL | same (unchanged `@st.cache_data` mechanism) | 6.15 ms (vs 234.18 ms to populate) |

**Targets vs. actual:**

- Queue initial display "under ~1.5s locally for one paginated page": **met**
  (409 ms measured, backend-only).
- Basic work-item content "under ~1s locally, warm DB connection": **met**
  for the backend service call (496 ms measured); this does not include
  Streamlit's own rendering/rerun overhead, which was not isolated in this
  measurement (see Known limitations - `WorkItemOpenTiming` now has a
  `queue_query` / `queue_transform_and_render` split for that, but it
  requires a manual interactive Streamlit run to capture, not an automated
  one).
- No IMAP/AI/OCR/parser call during work-item read: **met** - proved by
  `tests/test_work_item_queries.py::test_work_item_detail_does_not_import_or_call_imap_or_ai`
  and by `get_work_item_detail()`'s only DB-touching calls being read-only
  (traced in the same test file).
- No schema DDL during normal page rendering: **met** in the already-applied
  case (measured above); the one-time healing path is unchanged (idempotent
  `IF NOT EXISTS` DDL, as before).

**Remaining bottleneck** (from measured evidence): per-call network/connection
overhead from this environment to Supabase (~150-400 ms baseline per round
trip, visible even on the single `column_exists` check) dominates over actual
query execution time. Reducing round trips further (e.g. combining
`count_work_items` + `list_work_items_page` into one query with a window
function) would help more than further query optimization at current data
volume (111 open items).

## Motive password handling changes

- Streamlit Admin driver form no longer renders a "Motive Password" input.
- `pages_app/admin.py::_upsert_driver()` no longer writes `motive_password`
  in its `INSERT`/`UPDATE`.
- The `drivers.motive_password` column is **not** dropped (no destructive
  migration in this phase) - existing values are untouched and never
  selected/displayed as plaintext; the driver list shows a
  "Yes - rotate manually" / "No" indicator only.
- **Action required (manual, outside this phase):** any driver row with an
  existing Motive password should have that credential rotated directly in
  Motive. This phase does not do so automatically.

## Known limitations

1. **Not fully atomic yet:** `application.loads.commands.create_load_from_work_item`
   and `update_load_from_work_item` delegate to the existing
   `services.operations_inbox_service.create_load_from_inbox_item` /
   `update_load_from_inbox_item`, which still make several separate,
   non-atomic writes (create the load, insert its document row, two
   different `order_intake` updates, save communication, update the case).
   Phase 1 deliberately reused this canonical, certification-tested
   workflow rather than re-implementing multi-container/duplicate-protection
   business rules inside `application/`. Making this one transaction is
   Phase 2 scope.
2. **Operations Queue filter/classify/sort pipeline untouched:** the
   Streamlit queue still filters, classifies, and sorts in Python/Pandas
   after loading the (still `@st.cache_data`-cached, still SQL-filtered by
   `inbox_review_where_clause()`) open queue - only the **render** step is
   now paginated. Pushing filtering/sorting fully into
   `application.work_items.queries.get_work_item_queue_page()` for the
   Streamlit page too (matching what `/api/v1/work-items` already does) is
   Phase 2 scope.
3. **Queue timing instrumentation is a 2-way split, not the full
   query/transform/render breakdown** the spec asked for -
   `queue_query` vs. `queue_transform_and_render` (see
   `pages_app/operations_inbox.py`, `_operations_open_prerender_stages`).
   Splitting transform from render precisely would require finding a safe
   seam inside a ~600-line function; not attempted this phase to avoid
   destabilizing the classification pipeline.
4. **Cache invalidation targeting is scoped to Operations Inbox.** The 9
   `st.cache_data.clear()` call sites inside
   `services/operations_inbox_service.py` were converted to
   `refresh_data()` (which clears only the 4 Operations-Inbox-specific
   `@st.cache_data` functions). `st.cache_data.clear()` call sites in
   `pages_app/admin.py`, `pages_app/dispatch_board.py`,
   `pages_app/orders_management.py`, `pages_app/port_houston_integration.py`,
   `pages_app/documents.py`, and `services/order_intake.py` were left
   untouched - out of scope for a backend-boundary phase focused on
   Operations Inbox.
5. **No authentication.** `api/dependencies.py::get_current_actor()` is a
   placeholder. The API must not be exposed publicly without real auth.
   CORS is restricted to an explicit allowlist (`CORS_ALLOWED_ORIGINS` env
   var, comma-separated) and defaults to no cross-origin access at all when
   unset.
6. **`GET /api/v1/work-items`'s `WorkItemSummaryOut` includes `customer`,
   `booking_number`, etc. pulled from `parsed_data ->> 'Customer'` and
   similar JSON paths** - these mirror the app-column naming convention
   used elsewhere (`db_client.SM_TO_DB_COLUMNS`) rather than being real
   typed columns; a customer name with inconsistent casing/whitespace in
   `parsed_data` will sort/filter accordingly (unchanged from existing
   behavior - not a regression, just not fixed either).
7. **`app.routes` route-listing quirk:** with the FastAPI/Starlette
   versions installed in this environment (fastapi 0.139.0, starlette
   1.3.1), `app.include_router(...)` produces `_IncludedRouter` wrapper
   objects in `app.routes` alongside plain `APIRoute` objects for
   directly-decorated legacy endpoints. The literal validation command
   `python -c "from api.main import app; print([route.path for route in app.routes])"`
   raises `AttributeError` on the `_IncludedRouter` entries (they expose
   `.original_router.routes` instead of `.path`). The API itself works
   correctly end-to-end (proven via `TestClient` in
   `tests/test_api_work_items.py` and manually against production data);
   this is a route-introspection quirk of this framework version, not a
   routing bug. Use `python -m uvicorn api.main:app` + `GET /docs` to
   browse routes instead, or the enumeration snippet below.

   ```python
   from api.main import app

   def iter_routes(routes):
       for r in routes:
           if hasattr(r, "path"):
               yield r.path
           elif hasattr(r, "original_router"):
               yield from iter_routes(r.original_router.routes)

   print(list(iter_routes(app.routes)))
   ```

## Phase 2 recommendations

1. Make `create_load_from_work_item` / `update_load_from_work_item` fully
   atomic (thread one connection through the existing canonical workflow,
   or wrap it in a `SAVEPOINT`).
2. Migrate the Streamlit Operations Queue's filter/sort to call
   `application.work_items.queries.get_work_item_queue_page()` directly,
   retiring the Pandas-side filter/sort pipeline for the normal queue view.
3. Split `queue_transform_and_render` timing into its two real components.
4. Extend targeted cache invalidation to the other 6 files still using
   `st.cache_data.clear()`.
5. Add real authentication to `api/dependencies.py::get_current_actor()`
   before any external exposure; wire `CORS_ALLOWED_ORIGINS` for the actual
   Next.js origin when that phase starts.
6. Combine `count_work_items` + `list_work_items_page` into one query
   (window function) to cut the paginated queue read from 2 round trips to 1.
7. This is where Next.js frontend work begins - the API surface above is
   the intended contract; no Next.js code was written in this phase.
