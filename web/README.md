# CaliTrans TMS - Web Client (Phase 10 foundation)

Next.js (App Router, TypeScript strict) client proving out the
architecture that will eventually replace Streamlit for operational
workflows: FastAPI for reads/writes, Supabase Realtime Broadcast for
push invalidation, TanStack Query as the client-side cache. See
`docs/architecture/WEB_CLIENT.md` at the repo root for the full
architecture writeup - this file only covers running/developing this
package.

Streamlit remains the system of record for every workflow except the
read-only Loads screen this phase builds. See "Phase 10" in
`docs/architecture/WEB_CLIENT.md` for scope.

## Setup

```bash
npm install
cp .env.example .env.local   # fill in NEXT_PUBLIC_SUPABASE_* if testing realtime
npm run dev
```

Requires the FastAPI app running separately (repo root):

```bash
uvicorn api.main:app --reload
```

and `CORS_ALLOWED_ORIGINS=http://localhost:3000` set wherever the API
reads its environment (see `api/main.py`).

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Local dev server |
| `npm run build` | Production build |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest (unit + integration, jsdom) |
| `npm run api:generate` | Regenerate `lib/api/generated.ts` from the live FastAPI OpenAPI schema (dumps `openapi.json` via a one-off Python import of `api.main.app`, then runs `openapi-typescript` - see `package.json`) |

`lib/api/generated.ts` is generated - never hand-edit it. Re-run
`npm run api:generate` after any FastAPI route/schema change.

## Layout

```text
app/            App Router routes (login, protected /app/* shell, loads list/detail)
components/     AppShell, ConnectionIndicator
lib/api/        Typed API client, query keys, generated OpenAPI types
lib/auth/       Browser session context (POST /auth/login, GET /me)
lib/query/      TanStack QueryClient provider
lib/realtime/   Supabase Broadcast client, channel naming, ordering, invalidation map
tests/          Vitest unit/integration tests
```
