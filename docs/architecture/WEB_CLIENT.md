# Web Client Foundation (Phase 10)

## Status: architectural proof built (auth, API client, realtime invalidation, read-only Loads screen); not a Streamlit replacement yet

Phase 10 proves a new client stack can authenticate, call FastAPI
securely, render typed API data, subscribe to Phase 9's realtime domain
events, and update its UI without a Streamlit rerun - while backend
authorization stays the real enforcement boundary. It does **not**
migrate Operations Inbox, Dispatch, Orders, Documents, or Billing off
Streamlit. Those are later phases (see "Migration plan" below).

## Why Next.js, why now

Streamlit's model - a full-script rerun per interaction, `st.cache_data`
TTLs standing in for push updates - has no notion of "another client
just changed this record, tell me." Phase 9 built the push signal
(domain events -> Supabase Realtime Broadcast, see
`docs/architecture/REALTIME_EVENTS.md`); Phase 10 builds the first
client that can actually consume it: fetch through the Phase 8 read
model, subscribe to the Phase 9 channel contract, invalidate and refetch
on a matching event instead of waiting for a rerun or a cache TTL.

## Runtime model

```text
┌──────────────────────────┐      ┌──────────────────────────┐
│   Next.js web/ (this)    │      │   Streamlit (unchanged)   │
│  Auth · Query · Realtime │      │  Full operational surface │
└─────────────┬─────────────┘      └─────────────┬─────────────┘
              │ HTTPS                              │ direct import
              ▼                                    ▼
┌────────────────────────────────────────────────────────────┐
│                          FastAPI (api/)                     │
│         Auth · application/ commands & queries · errors     │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
                        PostgreSQL
                            │
                            ▼
                     domain_events (Phase 9)
                            │
                            ▼
                  Supabase Realtime Broadcast
                            │
                            ▼
              Next.js invalidates -> refetches FastAPI
```

The browser is never the source of truth, and never queries Postgres or
Supabase's database API directly for TMS data (loads, order_intake,
dispatch_messages, worker_jobs, domain_events) - only FastAPI. Supabase
is used from the browser strictly as a Broadcast transport (STEP 3/48).

Routine background work (email sync, inbox classification/matching) runs
independently of either client, via the durable `worker_jobs` queue
(`workers/processor.py`) processed on a schedule
(`.github/workflows/process-jobs.yml`) - neither Next.js nor Streamlit
triggers it directly in normal use. Streamlit's Operations Inbox
dispatcher view no longer exposes manual "Sync Email Engine" / "Refresh
Inbox" / "Recheck Next Batch" controls for this reason; they remain
available under Admin/Diagnostics for troubleshooting, permission-gated
(`Permission.WORK_ITEM_MANAGE`).

## Auth: browser -> session -> FastAPI -> AuthenticatedActor

No Supabase Auth project exists in this repo (see `api/auth.py`'s own
module docstring). Rather than invent a second identity system, Phase 10
reuses the existing `app_users` table (`repositories/user_repo.py`,
`application/auth/queries.py::authenticate_user` - already written with
a comment anticipating exactly this) via a small bridge:

1. `POST /api/v1/auth/login {email, password}` (`api/routers/auth.py`)
   verifies against `app_users` (bcrypt, `application/auth/password.py`)
   and issues a signed, time-limited session token
   (`application/auth/session_tokens.py` - HMAC-SHA256, stdlib only, no
   new dependency, 12h TTL, requires `SESSION_SECRET_KEY`).
2. `api/auth.py::require_auth` resolves either a static
   `API_AUTH_TOKENS` entry (existing service/integration path) or a
   session token from step 1 - same `AuthenticatedActor` either way, no
   route needs to know which kind a caller presented.
3. `GET /api/v1/me` returns `{email, display_name, role, permissions}`
   sourced from the existing Phase 5 `ROLE_PERMISSIONS` matrix
   (`application/auth/permissions.py`) - no new authorization model, no
   permission decided independently in TypeScript.
4. `web/lib/auth/AuthContext.tsx` wraps this: `signIn`/`signOut`,
   `currentUser`, `role`, `permissions`, `isAuthenticated`,
   `hasPermission`.

**Token storage tradeoff (documented, not solved this phase):** the
session token lives in memory and is mirrored to `sessionStorage`
(tab-scoped, cleared on tab close) - not `localStorage` (persists
indefinitely) and not an httpOnly cookie (would require the backend to
set/rotate cookies plus CSRF protection, out of scope for the "smallest
secure bridge" `session_tokens.py` chose). `sessionStorage` is still
readable by any script on the page - same XSS exposure as
`localStorage`, just a smaller time window. **Recommended follow-up
before this client carries real production traffic:** move to an
httpOnly, `SameSite=Lax` cookie issued by `/auth/login` and verified via
a CSRF-safe pattern (double-submit token or origin check on mutations).

**Streamlit has the analogous limitation.** A transitional fix
(`ui_components/auth_gate.py`) restores a dropped `st.session_state`
after a real browser refresh by reusing the same
`application/auth/session_tokens.py` HMAC token, stored in a cookie set
via a small inline `document.cookie` script - Streamlit has no
first-party cookie-write API, so this cookie is **not** httpOnly (a
script-written cookie cannot be) and carries the same readable-by-any-
page-script exposure as this client's `sessionStorage`. It is
fail-closed (no `SESSION_SECRET_KEY` configured means no cookie is ever
issued) and bounded to the same 12h TTL. The real fix for both clients is
the same recommended follow-up above: a server-set httpOnly cookie.

**Frontend permissions are UX only.** `hasPermission()` gates
navigation/button state. The actual boundary is
`application/auth/permissions.py::require_permission`, called inside
application commands - unchanged by this phase, and not reproduced in
TypeScript beyond render decisions.

## API client and contracts

`web/lib/api/client.ts` is the single fetch chokepoint: base URL
(`NEXT_PUBLIC_API_BASE_URL`), bearer header, JSON parsing, and error
normalization from `api/errors.py`'s envelope
(`{"error": {"code", "message", "details"}}`) into a typed `ApiError`.
A 401 triggers a registered callback (wired to `AuthContext.signOut` at
app start) rather than each caller checking status codes individually.

Types are generated, not hand-maintained: `npm run api:generate` dumps
`api.main.app`'s live OpenAPI schema (no server needs to be running -
just imports the FastAPI app object) and runs `openapi-typescript`
against it into `web/lib/api/generated.ts`. `web/lib/api/types.ts`
re-exports the handful of schemas the app actually uses under readable
names. Re-run after any FastAPI route/schema change; never hand-edit the
generated file.

Query keys are centralized in `web/lib/api/queryKeys.ts`
(`loadKeys`/`inboxKeys`/`meKeys`) so realtime invalidation and
components can never key the same resource two different ways.

## Realtime: exact Phase 9 contract, never guessed

`web/lib/realtime/channels.ts` mirrors `realtime/channels.py`'s naming
by hand (small enough not to justify codegen): `loads` / `inbox` /
`communications` / `documents` collection channels, `load:{id}` as the
only resource channel this pass. `web/lib/realtime/client.ts` uses
`@supabase/supabase-js` with the browser publishable key only - never
the service-role key, which stays server-side in
`realtime/publisher.py`.

**Wire payload.** Phase 9 originally broadcast only the raw metadata
dict, with no ordering token and no aggregate id on a collection
channel. This phase required that contract, so
`services/realtime_publisher.py::_envelope_payload` was extended (in
this same branch, on top of the not-yet-merged Phase 9 PR - see that
commit's message for the note to fold it back before Phase 9 merges) to
wrap metadata in an envelope: `event_id`, `aggregate_type`,
`aggregate_id`, `version`, `occurred_at`, `metadata`. See
`docs/architecture/REALTIME_EVENTS.md`'s "Wire payload" subsection for
the exact shape.

**Ordering.** `web/lib/realtime/ordering.ts::EventOrderingTracker`
tracks the highest `event_id` seen per `(aggregate_type, aggregate_id)`
and ignores anything not strictly newer - a same-aggregate guarantee
only, no claim of one global order across resources.

**Invalidation map.** `web/lib/realtime/invalidationMap.ts` centralizes
`event_type -> which queries to invalidate`, matching
`docs/architecture/REALTIME_EVENTS.md`'s "Client invalidation contract"
table exactly. A `load.*` event invalidates only the load list, that
load's detail, and its timeline - never the whole query cache.

**Disconnect fallback.** `web/lib/realtime/useRealtimeChannels.ts`
exposes `connecting | connected | disconnected | error`.
`ConnectionIndicator` renders "Live updates temporarily unavailable" -
never a message implying the displayed data is wrong - and the API
remains fully functional (manual refetch, page reload) regardless of
realtime state. Realtime is an enhancement, never a correctness
dependency (STEP 36/27).

## Load proof screen

`/app/loads` (list, Phase 8's `GET /loads/search`, subscribes to the
`loads` collection channel) and `/app/loads/[id]` (detail, `GET
/loads/{id}/detail`, subscribes to `loads` + `load:{id}`). Read-only
this phase (STEP 33/34) - no mutation UI shipped, since the goal is
proving API-reads-plus-realtime-invalidation before adding write paths.

**Two-browser acceptance path:** open a load's detail page in one
browser, change that same load elsewhere (Streamlit, or a direct API
call under `API_AUTH_DEV_MODE`), and once
`scripts/process_realtime_events.py process` broadcasts the resulting
domain event, the open detail page invalidates and refetches
automatically - no manual refresh. This requires a live Supabase
project with `REALTIME_ENABLED=true` and matching
`NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`; with
realtime unconfigured (the default), the same screens work via API
reads alone, confirming the fallback described above.

## Security

- No backend secret name (`SUPABASE_SECRET_KEY`, `SERVICE_ROLE`,
  `DATABASE_URL`, `TWILIO_*`, `SMTP_PASSWORD`, `SESSION_SECRET_KEY`)
  appears as a real value anywhere under `web/.next/static` (the
  client-shipped bundle) - verified for this phase by grepping the
  production build output. Only browser-safe `NEXT_PUBLIC_*` values are
  read by client code (`web/.env.example`).
- CORS (`api/main.py`) is an explicit allowlist
  (`CORS_ALLOWED_ORIGINS`), never `"*"`, since `allow_credentials=True`
  is required for the browser to send the Authorization header
  cross-origin in local dev (`http://localhost:3000` ->
  `http://localhost:8000`).
- The browser never talks to Postgres or a Supabase database API for
  TMS data - only FastAPI (business reads/writes) and Supabase Realtime
  (broadcast transport, invalidation signals only).

## Tests

- Backend: `tests/test_session_tokens.py`, `tests/test_api_auth_login_me.py`
  (new), plus the existing `tests/test_api_auth.py` suite, unaffected.
  `tests/test_realtime_publisher_service.py` / `tests/
  test_watch_realtime_events.py` updated for the envelope change.
- Frontend (`web/tests/`, Vitest + Testing Library, jsdom): API client
  (auth header, error envelope, 401 handling), query key stability,
  realtime channel naming (mirrors the Python test's own assertions),
  event ordering, invalidation mapping, `ConnectionIndicator` never
  implying invalid data, `AuthContext` sign-in/out/401-bootstrap flow,
  and one integration test driving `useRealtimeChannels` end-to-end with
  a mocked transport (`load.updated` broadcast -> query invalidation ->
  refetch - including a stale/out-of-order broadcast correctly ignored).
  No live Supabase connection required for any of it.
- `npm run typecheck`, `npm run lint`, `npm run build` all pass with
  `strict: true` and no `any`/`@ts-ignore` in application code.

## Migration plan

```text
Phase 10  - auth, API client, realtime foundation, read-only Loads proof
Phase 10A - Operations Inbox web migration (in progress - see
            docs/architecture/OPERATIONS_INBOX_WEB.md)
Phase 10B - Dispatch Board web migration
Phase 10C - Load Workspace (full mutation surface) web migration
```

Operations Inbox is the first operational (not just read-proof) workflow
being migrated: `/app/inbox` now supports the primary dispatcher
workflow (queue navigation, search, work-item detail, create/update/
link/close load actions) against the same `api/routers/work_items.py`
boundary Streamlit's own Admin/Diagnostics tooling and application
commands already use. Streamlit's Operations Inbox is **not retired** by
this work - it remains available as the transitional fallback until an
explicit later closure step accepts removing it. Streamlit is not
deprecated by this phase - it remains the operational system of record
for every workflow Phase 10/10A doesn't yet cover.
