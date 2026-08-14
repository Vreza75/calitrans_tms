# API Read Model (Phase 8 — partial)

## Status: loads read model complete; dispatch/driver read models deferred

This is a **partial** Phase 8 pass, not the full original scope. See
"What's not built" below before relying on this doc as complete.

## Why

Streamlit's dominant read pattern (`services/tms_data_service.py::
load_tms_data()`) loads the entire `loads` table (unbounded, no WHERE, no
LIMIT) into a DataFrame on every render (45s TTL cache), then 9 different
pages filter/sort/paginate it in Python. That pattern is fine for
Streamlit's own use (small data volume, one process) but is not a
contract a future web client can depend on - no pagination bound, no
stable filter/sort vocabulary, no typed shape. Phase 8 builds the
alternative: typed, paginated, filtered, searched reads pushed to SQL,
independent of Streamlit.

## Query architecture

```text
Client (FastAPI today; Streamlit optionally later, per-page, opt-in)
        |
        v
api/routers/loads.py           (thin: parse request, call application query, map response)
        |
        v
application/loads/queries.py   (pagination math, typed DTOs, NotFoundError)
        |
        v
repositories/load_query_repo.py (parameterized SQL, allowlisted sort, explicit columns)
        |
        v
PostgreSQL
```

`services/tms_data_service.py::load_tms_data()` is untouched - existing
Streamlit pages that consume it still work exactly as before. This is a
new, parallel read path, not a replacement of the old one (yet).

## Pagination

**Page/offset**, not cursor - see `application/common/pagination.py`'s
docstring for the reasoning: at this business's actual scale (~10-20
drivers, a handful of concurrent dispatchers), cursor pagination's main
advantage (stable pages under high write-churn) doesn't meaningfully
apply, and Phase 7's `WorkItemPage` already proved page/offset works
fine in production for the busier Inbox queue. `PageResult[T]`
(`application/common/pagination.py`) is the shared generic; Phase 7's
`WorkItemPage` was deliberately **not** retrofitted to use it (already
correct and tested - STEP 14's "if Phase 7 APIs are already correct,
leave them alone" rule generalized).

Response shape:

```json
{"items": [...], "page": 1, "page_size": 50, "total_items": 137, "total_pages": 3, "sort_by": "updated_at", "sort_direction": "desc"}
```

Bounds: `DEFAULT_PAGE_SIZE = 50`, `MAX_PAGE_SIZE = 200`, enforced both at
the FastAPI layer (`Query(..., le=200)`, → `422` if exceeded) and inside
`normalize_page_size` (so calling the application function directly,
bypassing FastAPI, gets the same cap).

## Filtering/search

`repositories/load_query_repo.py::build_load_filters` - every value
bound as a SQL parameter, never string-interpolated (tested with SQL-
metacharacter-containing input). Filters: `status`, `service_flow`
(exact match), `customer`/`driver_name`/`port`/`warehouse` (`ILIKE`
partial match), `invoice_status` (exact), `delivery_after`/
`delivery_before` (date window on `delivery_need_date`), `search`
(`ILIKE` across booking/container/reference/customer/driver/truck).
**No `dispatcher` filter** - no such column exists on `loads`; not
fabricated.

Sort: allowlisted mapping (`SORTABLE_COLUMNS`), never raw user input in
`ORDER BY` - tested with an injection-shaped `sort_by` value, confirmed
it falls back to the default rather than erroring or being interpolated.

## DTO strategy: list vs. detail

- `LoadListItem` (16 fields) - what a collection view needs, nothing
  more.
- `LoadDetail` (32 fields) - richer, but still excludes timeline,
  communications, and documents. Those are separate, independently
  paginated/bounded resources:
  - `GET /loads/{id}/timeline` - unions `status_events` +
    `dispatch_messages` (both already `load_id`-scoped) into
    chronological history. Does **not** include
    `operations_case_events` (case-scoped, not load-scoped - joining
    through case linkage would be a bigger unification than "at minimum
    provide chronological operational history" calls for).
  - `GET /loads/{id}/communications` - `dispatch_messages` only, typed.
    Foundational for a later Communications Hub; inbound SMS not
    implemented (out of scope).
  - `GET /loads/{id}/documents` - metadata only, **never** `file_path`
    (a storage key, not a public field - same rule
    `application/work_items/models.py::AttachmentMeta` already follows).
    No `size`/`available_at` fields - the `documents` table has neither
    column; not fabricated.

## Endpoints (this pass)

| Endpoint | Filters | Search | Pagination | Sort | Permission |
|---|---|---|---|---|---|
| `GET /loads` (existing, unchanged) | `status` | — | bare `limit` | fixed | `READ_LOADS` |
| `GET /loads/search` (new) | status/service_flow/customer/driver_name/port/warehouse/invoice_status/delivery window | ✓ (`search`) | page/page_size envelope | allowlisted, both directions | `READ_LOADS` |
| `GET /loads/{id}` (existing, unchanged) | — | — | — | — | `READ_LOADS` |
| `GET /loads/{id}/detail` (new) | — | — | — | — | `READ_LOADS` |
| `GET /loads/{id}/timeline` (new) | — | — | ✓ | fixed (chronological) | `READ_LOADS` |
| `GET /loads/{id}/communications` (new) | — | — | ✓ | fixed (chronological) | `READ_LOADS` |
| `GET /loads/{id}/documents` (new) | — | — | bounded `limit` | fixed | `READ_LOADS` |

`GET /loads` was **not** changed in place (unlike the original plan) -
kept exactly as-is since it already has real behavior to preserve, and
`/search` is additive. No breaking change to version.

## Index changes

`database/load_query_indexes_migration.sql` (registered in
`scripts/run_migrations.py::MIGRATION_ORDER`): `loads(updated_at)`
(the default sort column, previously unindexed), `loads(container_number)`,
`loads(reference_number)`, `loads(driver_name)` (search/filter targets
without prior indexes - `booking_number`/`status` already had them),
`documents(load_id)` (every per-load documents query filters on this;
`documents.status` was indexed, `load_id` was not). `ILIKE` search itself
isn't accelerated by a plain btree index - a trigram (`pg_trgm`) index
would help but requires an extension not currently enabled; not added
this pass, flagged as a follow-up if search performance becomes a real
problem at higher data volume than this business's current scale.

## What's NOT built this pass (genuine follow-ups, not silently dropped)

- **`GET /api/v1/dispatch/active`** (STEP 13) - the read model inventory
  flagged as hardest: Dispatch Board's readiness/exception computation
  (`services/dispatch_workflow_service.py::_load_readiness_details`)
  currently runs per-row in Python, not just filtering. Porting it into
  the query layer (or calling it per-row from the query layer without
  duplicating the logic) is real work that needs its own careful pass,
  not a rushed addition here.
- **`GET /api/v1/drivers`** (STEP 16) - `services/driver_roster_service.py::
  list_active_drivers()` exists but has no search/pagination; not wrapped.
- **Streamlit pilot consumer** (STEP 31) - no Streamlit page was migrated
  to consume the new query layer. `pages_app/documents.py`'s own
  unbounded raw SQL (bypasses `repositories/document_repo.py` entirely)
  was identified as the easiest, most contained candidate but not
  touched.
- **OpenAPI contract-quality pass** (STEP 29/30) - not audited for
  duplicate schema names, excessive `Any`, or operation-id sensibility.
- Inbox review (STEP 14/15) - confirmed still correct/unchanged from
  Phase 7, no action needed.

## Future realtime

Realtime events in Phase 9 will invalidate/refetch these stable query
resources rather than replacing the read model.
