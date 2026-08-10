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

This gates page *visibility* only. Button/action-level authorization for
Streamlit mutations is not yet implemented (see "Known limitations"
below) - the API layer (`api/auth.py`) already has finer-grained
per-endpoint role checks for mutations reached through `/api/v1/*`.

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
- **No button/action-level Streamlit authorization** (e.g. a dispatcher
  and a manager both viewing Orders/Load Management currently see the
  same mutation controls). Page-visibility gating only, in this pass.
- **No rate limiting or lockout on either login surface.** Neither the
  Streamlit login form (`ui_components/auth_gate.py`) nor the API's
  bearer-token check (`api/auth.py::require_auth`) tracks failed
  attempts or applies backoff/lockout - both are brute-forceable given
  enough requests. bcrypt's own hashing cost (~100-300ms/attempt) is the
  only friction on the Streamlit side today. Needs a real design decision
  (lockout policy, rate-limit middleware/library choice) before this app
  is reachable from a genuinely public URL - not addressed in this pass.

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
