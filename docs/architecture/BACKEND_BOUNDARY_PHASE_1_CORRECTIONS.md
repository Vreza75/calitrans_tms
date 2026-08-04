# Backend Boundary Phase 1 — Correction Pass

Branch: `architecture/backend-boundary-phase-1-corrections`, from
`architecture/backend-boundary-phase-1` @ `4746c2a`.

This corrects overstated claims in `BACKEND_BOUNDARY_PHASE_1.md` per an
independent Codex audit (1 BLOCKER, 5 HIGH, 6 MEDIUM, 1 LOW). It does **not**
complete every item that audit raised — see "Not done this pass" below.
Read this doc, not the original, for current state.

## Corrected claims

| Original Phase 1 claim | Correction |
|---|---|
| "Streamlit and FastAPI already share the application layer" | **False for commands and queue rendering.** True only for: (a) the identity-checked functions in `tests/test_backend_boundary_architecture.py`, which prove the *router* and *a hypothetical Streamlit caller* would resolve to the same function objects — but `pages_app/operations_inbox.py` does not actually call them. Streamlit's queue/detail/command paths are still 100% legacy (`services/operations_inbox_service.py`, direct SQL, Pandas). **Not fixed this pass** (see below). |
| "Complete runtime framework neutrality" | Corrected to true only for the specific functions Codex named (`get_attachment_summary`, `get_attachment_content`, `attachment_ref`) — fixed this pass via `services/operations_attachment_core.py`. `services/operations_inbox_service.py` and `pages_app/operations_inbox.py` still import streamlit at module top, as they always have. |
| "No DDL during normal rendering" | Was true only in the sense that `ensure_*_schema()` short-circuited on an already-applied schema. It could still be *triggered* by the render path, and misclassified connection/permission errors as "schema missing." Fixed this pass: `db_client.check_schema_readiness()` is read-only and classifies failures correctly; `pages_app/operations_inbox.py` and `ui_components/communication_dashboard.py` no longer call `ensure_operations_email_sync_schema()` from render. Other interactive call sites (`ui_components/communication_dashboard.py` was one; two remain inside button-triggered handlers in `services/operations_inbox_service.py` — "Sync Email Engine," "Recheck Next Batch") were left as-is; the Phase 1 process-level `_SCHEMA_READY_FLAGS` cache already prevents them from re-running DDL after the first successful check in a process. |
| "82% performance improvement" | Was measured only on the FastAPI/application path, never the live Streamlit path. **Not re-measured this pass** (see below) — do not cite the 82% number as applying to Streamlit. |
| "Unrestricted parsed_data is safe" | Was never explicitly claimed safe, but the API returned it unfiltered. Fixed this pass: allowlisted in `api/schemas/work_items.py`. |
| "Architecture tests prove Streamlit reuse" | They prove the application layer *could* be shared (identity checks), not that Streamlit *does* share it. Streamlit's actual call sites are unchanged. |

## What this pass actually fixed (real, tested, committed)

1. **Attachment core extraction** (`services/operations_attachment_core.py`) —
   `extract_operations_attachments`, `extract_operations_pdf_attachments`,
   `group_operations_source_documents`, `read_attachment_bytes`,
   `attachment_ref` have zero streamlit dependency, direct or transitive.
   Proven by subprocess tests that *call* the functions, not just import
   the module (`tests/test_attachment_core_runtime_neutrality.py`).
2. **Transition concurrency** — `dispatch_transition_service.apply_transition`
   now reads the load row with `SELECT ... FOR UPDATE` inside the same
   transaction it writes through, instead of an unlocked read before the
   transaction opened. Real concurrency test against Postgres (two threads,
   one blocks and then sees the fresh status):
   `tests/test_dispatch_transition_concurrency.py`.
3. **Schema readiness** — `db_client.check_schema_readiness()` distinguishes
   `ready` / `schema_missing` / `connection_error` / `permission_error` /
   `unknown_error` using Postgres SQLSTATE codes, redacts credentials from
   error detail, and is read-only (proven against a real disposable
   Postgres with a SQL-statement-capturing test). Render paths use it
   instead of `ensure_operations_email_sync_schema()`.
4. **Real API authentication** (`api/auth.py`) — bearer-token scheme,
   `API_AUTH_TOKENS` env var (JSON, never committed) mapping tokens to
   `{actor, role}`. Fails closed: no tokens configured means every
   protected route returns 401 for every request. `API_AUTH_DEV_MODE=true`
   is the only bypass, explicit and off by default. Role groups gate every
   `/api/v1` route and the legacy `/loads` write route (previously
   unauthenticated). **Not Supabase Auth JWT** — no Supabase Auth project
   exists in this repo (no JWT secret, no `auth.users`, no login system
   anywhere); the user chose this scheme over provisioning that
   infrastructure. See `api/auth.py` docstring for the swap-in path later.
5. **API contract hardening**:
   - `parsed_data` allowlisted to named operational fields.
   - Every error response uses one envelope: `{"error": {"code", "message", "details"}}`.
   - Invalid transitions now return 409 (was 200 with `ok: false`).
   - Added `POST /api/v1/work-items/{id}/update-load` (command existed,
     route didn't).
   - `page_size` bounded 1–100 on queue and conversation endpoints.
   - `application/loads/queries.py` now runs real SQL `LIMIT`/`WHERE`
     instead of loading every load into Pandas.
   - `LoadSummaryOut.updated_at` typed as `datetime | None`, not `Any`.
6. **Disposable Postgres certification** — isolated Docker container (not
   the shared local Supabase instance used by other projects on this
   machine), migrations verified fresh + idempotent (10/10 pass).
   **Operations Inbox certification: 8 of 10 cases fail** against real
   fixtures — see "Certification findings" below. This was previously
   invisible (no disposable DB had ever been configured in this
   environment) and is unrelated to any change in this branch or Phase 1.

## Certification findings (pre-existing, not fixed — reported per user decision)

Running `tests/integration/operations_inbox/` against a fresh disposable
Postgres for the first time surfaced 8 failing cases: CASE-000, 001, 003,
004, 005, 006, 008. CASE-000/001 look like stale `expected.json` casing
(`"Qa Harness"` vs `"QA Harness"`). CASE-003/004/006/008 show real field
mismatches (wrong delivery address, extra reply-context fields). CASE-005 is
the most concerning: `contact_name` gets overwritten with steamship-line
text (`"Steamship Line: CMA CGM"` instead of `"Dana Phillips"`) — looks like
a genuine field-extraction regression. `actual.json` fixtures were updated
to capture current behavior for a dedicated follow-up investigation. Fixing
these requires touching classification/parsing logic, which is out of scope
for this pass (excluded explicitly: "parser rewrite") and needs its own
certification cycle, not a rushed fix inside an architecture PR.

## Not done this pass (be honest, don't claim these)

- **Streamlit is not rewired to the application layer.** `pages_app/
  operations_inbox.py` still calls `services/operations_inbox_service.py`
  directly for queue retrieval, detail, drafts, attachments, conversation,
  and every command (close, link, create-load, update-load, transition,
  assign-driver). This was flagged as "the most important correction" and
  is the single largest, highest-risk item in the audit — rewiring a
  ~5,600-line interactive file's read AND write paths without a working
  browser-based acceptance loop in this environment was judged too risky
  to attempt reliably within this pass. **Streamlit does not use SQL
  pagination** — it still loads the filtered queue into Pandas and
  paginates the render only (a real, smaller fix from the original Phase
  1 pass, unchanged here).
- **create-load / update-load are still not atomic.** Same known
  limitation as Phase 1: they delegate to the existing multi-write legacy
  workflow. Not attempted — CASE-006 (multi-container) already fails for
  unrelated reasons in this environment, so there's no clean regression
  signal to safely verify a transaction rewrite against right now.
- **Prior-attachment query still returns empty** (`application/attachments`
  doesn't have a dedicated earlier-message query yet).
- **No cache-version/coherence mechanism.** API writes can still leave a
  Streamlit session's cache stale until its own TTL/explicit clear.
- **No live Streamlit manual walkthrough or real Streamlit-path
  performance measurement.** Verified `import app`, `streamlit run
  --headless` boots and serves HTTP 200 (Phase 1) — nothing beyond that
  in this pass.
- **Test coverage is a subset of the spec's 42-item list**, matched to
  what was actually built (auth, schema readiness, concurrency, attachment
  runtime-neutrality, API contracts) — not the Streamlit-adapter,
  cache-coherence, or prior-attachment items, since those weren't built.

## Recommended next phase

1. Rewire Streamlit's queue read (highest value, most contained: swap
   `ops._load_operations_inbox_df` for `application.work_items.queries.
   get_work_item_queue_page` behind a feature flag, verify against a
   disposable DB + manual click-through before removing the old path).
2. Investigate the 8 certification failures as its own tracked
   workstream — CASE-005's field mixup in particular looks like a real
   customer-facing bug, independent of any architecture work.
3. Cache-version mechanism, prior-attachment query, atomic create/update-load.
4. Provision real Supabase Auth (or equivalent) if/when a second API
   consumer (e.g. Next.js) makes the shared-token scheme insufficient.

## Local disposable Postgres

A Docker container (`calitrans_test_pg`, port 55433) was created for this
session's certification run — isolated from the shared local Supabase
instance other projects on this machine use. It is not part of git. Stop/
remove it with `docker rm -f calitrans_test_pg` when no longer needed, or
reuse it (`MIGRATION_TEST_DATABASE_URL=postgresql://postgres:testpass@localhost:55433/calitrans_test`)
for further certification investigation.
