# Authentication & Authorization

Two separate identity mechanisms exist, sharing one canonical role/principal
model (`application/auth/models.py`: `Role`, `AuthenticatedActor`):

| Surface | Module | Scheme |
|---|---|---|
| Streamlit app (`app.py`) | `ui_components/auth_gate.py` | Per-person login (email + bcrypt-hashed password), `st.session_state` |
| FastAPI (`api/`) | `api/auth.py` | Bearer token (`API_AUTH_TOKENS` env var) |

Neither wraps Supabase Auth/JWT - no Supabase Auth project exists in this
repo (no JWT secret, no `auth.users`). Both are real, fail-closed identity
providers built directly against Postgres, structured so a real Supabase
JWT verifier could later be swapped in underneath `AuthenticatedActor`
without changing call sites.

## Roles

`dispatcher`, `manager`, `accounting`, `admin` (`application/auth/models.py::Role`).

## Streamlit login

Backed by the `app_users` table (`database/app_users_migration.sql`):
`email`, `display_name`, `password_hash` (bcrypt), `role`, `is_active`.

`app.py` calls `ui_components.auth_gate.require_login()` before any page
content or navigation renders. Unauthenticated: renders a login form and
calls `st.stop()` - nothing else on the page executes. Authenticated:
returns the `AuthenticatedActor`, threaded through to
`pages_app/router.py::route_selected_page(principal)`.

Page-level authorization is two-layered, both driven by the single
`application/auth/permissions.py::ROLE_ALLOWED_SECTIONS` map:

1. `ui_components/app_shell.py::render_sidebar()` only ever offers
   navigation choices the principal's role permits - an unpermitted page
   is never shown as a radio option.
2. `pages_app/router.py::route_selected_page()` independently re-checks the
   resolved section via `is_section_permitted()` before rendering it. This
   does not trust the sidebar's own filtering - it is the actual
   fail-closed gate, covering any future path that could hand it an
   unfiltered section string.

Default page-visibility policy (business policy, not a security
mechanism - see the docstring in `permissions.py` before changing it):

| Role | Sees |
|---|---|
| dispatcher | Operations Inbox, Orders/Load Management, Dispatch Board, Active Status, Calendar View, Documents, Dashboard |
| accounting | Billing / ProfitTools, Dashboard, Documents, Orders/Load Management |
| manager, admin | everything, including Admin / Diagnostics (Master Data, Email Imports/Diagnostics, Port Houston Setup/Testing, Validation, AI Dispatcher Workspace) |

This gates page *visibility* only - see "Application command authorization
(Phase 5)" below for the actual mutation-level security boundary.

### Bootstrapping the first account

`app_users` starts empty after the migration runs - there is no in-app
"create user" page in v1. Run once, manually, per deployment, after
`scripts/run_migrations.py`:

```
python scripts/create_admin_user.py --email you@calitranscorp.com --display-name "Your Name" --role admin
```

Prompts for the password via `getpass` (never pass it as a CLI argument -
it would land in shell history). Refuses to create a duplicate email.

### Dev-mode bypass

`STREAMLIT_AUTH_DEV_MODE=true` (exact match, case-insensitive) skips login
entirely and grants `AuthenticatedActor(actor="dev-mode", role=Role.ADMIN)`.
Unset, empty, or any other value always requires a real login - same
contract as the API's `API_AUTH_DEV_MODE`. Never set this in a production
deployment's environment/secrets.

### Known limitations (not addressed in this pass)

- **Session does not survive a hard browser refresh.** `st.session_state`
  is in-memory per Streamlit session; a full page reload starts a new
  session and requires logging in again. Persisting across refresh would
  need cookie-based session storage (e.g. `extra-streamlit-components`),
  a deliberately separate, larger change not included here.
- **No self-service password reset, no admin UI for managing users.**
  Use `scripts/create_admin_user.py` and direct SQL (`update app_users set
  is_active = false ...`) until an admin page exists.
- **No rate limiting or lockout on either login surface.** Neither the
  Streamlit login form (`ui_components/auth_gate.py`) nor the API's
  bearer-token check (`api/auth.py::require_auth`) tracks failed
  attempts or applies backoff/lockout - both are brute-forceable given
  enough requests. bcrypt's own hashing cost (~100-300ms/attempt) is the
  only friction on the Streamlit side today. Needs a real design decision
  (lockout policy, rate-limit middleware/library choice) before this app
  is reachable from a genuinely public URL - not addressed in this pass.

## Application command authorization (Phase 5)

Three distinct questions, three distinct mechanisms - do not conflate them:

| Question | Mechanism | Enforced by |
|---|---|---|
| Who is this caller? | Authentication (above) | `ui_components/auth_gate.py`, `api/auth.py` |
| May this role see this section? | Page visibility | `application/auth/permissions.py::is_section_permitted` - UX, not the security boundary |
| May this authenticated principal execute this specific business operation? | **Business permission** | `application/auth/permissions.py::require_permission`, called inside the application command itself |

### The business-permission matrix

`application/auth/permissions.py::Permission` (an enum, e.g. `LOAD_CANCEL`,
`LOAD_VERIFY`, `DRIVER_MESSAGE_SEND`, `BILLING_EDIT`) names a capability,
not a page or a button - the same permission means the same thing
regardless of which interface is asking. `ROLE_PERMISSIONS` maps each
`Role` to its granted set; `has_permission()`/`require_permission()` are
the only functions that read it.

| Permission | dispatcher | accounting | manager | admin |
|---|---|---|---|---|
| `load:view` | Y | Y | Y | Y |
| `load:edit` | Y | N | Y | Y |
| `load:cancel` | Y | N | Y | Y |
| `load:verify` | Y | N | Y | Y |
| `load:ready_to_dispatch` | Y | N | Y | Y |
| `driver_message:send` | Y | N | Y | Y |
| `billing:view` | N | Y | Y | Y |
| `billing:edit` | N | Y | Y | Y |
| `task:create` | Y | N | Y | Y |
| `task:edit` | Y | N | Y | Y |
| `user:admin` | N | N | N | Y |

Rationale for the non-obvious cells (full detail in
`application/auth/permissions.py`'s own comment above `ROLE_PERMISSIONS`):
dispatcher gets `load:cancel` because cancelling a bad booking is an
explicit, existing part of this role's own page (`orders_management.py`'s
own caption: "...cancel bad orders before dispatch work begins");
accounting gets no `load:*` mutation permission (only `load:view`, for
invoice context) because nothing in this repository establishes it should
perform operational dispatch mutations - the more restrictive mapping is
the default until a real business requirement says otherwise; manager does
not get `user:admin` for the same reason (account provisioning has only
ever been a `scripts/create_admin_user.py` operator action, never an
in-app manager capability). Admin's full grant is still enforced through
the normal `has_permission()` lookup, not a `role == admin` shortcut
anywhere - admin is not equivalent to a bypass.

### Where it's enforced

`application/loads/commands.py` holds the Orders/Load Management
application commands: `mark_load_missing_info`, `save_load_note`,
`verify_load_booking`, `cancel_load`, `update_load_fields`,
`mark_load_ready_to_dispatch` (plus the pre-existing
`create_load_from_work_item`/`update_load_from_work_item`/
`transition_load`). Each calls `require_permission(actor, Permission.X)`
as its first action, before any read or write - an unauthorized call
raises `application.exceptions.AuthorizationError` and performs zero
mutation. This module has no `streamlit` import and calls no HTTP
framework; it is safe to call from Streamlit, a FastAPI route, a script,
or a future AI agent/worker, and behaves identically regardless of the
caller.

`pages_app/orders_management.py` calls these commands instead of writing
directly to `DispatchDatabaseClient`. It also uses `has_permission()` to
disable/hide buttons a role can't use - this is UX only. The command
itself independently re-enforces the same permission, so a caller cannot
bypass authorization merely by invoking the command directly instead of
clicking through the page that normally exposes it (proven by
`tests/test_security_ui_bypass.py`, which calls the commands directly with
no Streamlit involved at all).

Audit trail: `DispatchDatabaseClient.update_row_fields()` and
`services/dispatch_data_service.py::_insert_dispatch_message()` both
accept an optional `created_by`/`sent_by` parameter (default preserves
prior behavior for every caller that doesn't pass it). The Phase 5
commands pass the real `actor.actor` identity, so `status_events`/
`dispatch_messages` now record who actually performed the action instead
of a generic per-framework label.

### API integration

`api/routers/work_items.py`'s mutating routes already require
`api/auth.py::require_role(*MUTATE_OPERATIONS)` - a coarse, role-level
check independent of Streamlit. Where a route calls into
`application/loads/commands.py` (e.g. `create_load_from_work_item`,
`update_load_from_work_item`, `transition_load`), the same fine-grained
`Permission` enforcement inside those commands applies too - defense in
depth, matching the Streamlit path. The 6 new Orders/Load Management
commands above are not yet exposed as API routes (no `/api/v1` endpoint
calls them) - wiring them in is Orders/Load Management API parity, not
part of this pass.

### Known remaining direct-mutation paths (not migrated in this pass)

Confirmed via `grep` for `DispatchDatabaseClient().update_row_fields\(` /
`.add_row\(` across `pages_app/`, `services/`: the same
page-visibility-only pattern this section replaces for Orders/Load
Management still exists, unmigrated, in:

- `pages_app/dispatch_board.py` (driver/truck/chassis assignment, notes,
  "Mark Ready for ProfitTools" status change) - reachable only by
  dispatcher/manager/admin (Dispatch Board is not in accounting's
  page-visibility set), lower severity than the accounting-on-Orders gap
  this pass closed, but the same structural gap.
- `pages_app/documents.py` (file attach / intake row creation).
- `pages_app/port_houston_integration.py` (load field updates from Port
  Houston lookups).
- `services/dispatch_transition_service.py::apply_transition()` (the
  canonical status-transition function) - already role-gated when reached
  through the API (`MUTATE_OPERATIONS`), but reachable with zero
  permission check when called directly from
  `pages_app/dispatch_board.py`.

None of these are new gaps introduced by this pass - they predate it and
are simply not yet migrated to the command-boundary pattern. Recommended
as the next phase's scope, using the same `Permission`/`require_permission`
model already in place rather than inventing a second one.

### Non-human actors (not built in this pass)

`AuthenticatedActor` carries no Streamlit-specific assumption (see
`application/auth/models.py` - already framework-neutral, shared by both
the Streamlit and API identity layers). It is not yet extended with an
actor-type field (human/service/worker/integration) - not needed by
anything that exists today, and adding it now would be speculative. When
a real non-human caller (a worker, an AI agent, the eventual Motive
integration) needs to invoke these commands, extending `AuthenticatedActor`
and `Role`/`Permission` to cover it is straightforward because the
enforcement point is already the command, not the UI.

## API authentication

Unchanged behavior, see `api/auth.py`'s own module docstring for the
`API_AUTH_TOKENS` / `API_AUTH_DEV_MODE` contract. `Role` and
`AuthenticatedActor` are now imported from `application/auth/models.py`
(previously defined locally in `api/auth.py`) - one canonical definition,
zero behavior change; `tests/test_api_auth.py` passes unmodified.

## Manual deployment actions required

1. Run `python scripts/run_migrations.py` against the target database
   (adds `app_users`, among any other unapplied migrations).
2. Run `python scripts/create_admin_user.py ...` once to create the first
   login.
3. Do not set `STREAMLIT_AUTH_DEV_MODE` or `API_AUTH_DEV_MODE` in
   production environment/secrets.
4. Add `bcrypt` to whatever environment installs `requirements.txt`
   (already added there; nothing extra needed if deploying from this repo).
