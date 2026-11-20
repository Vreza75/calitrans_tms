---
paths:
  - "web/**"
---

# Frontend (Next.js web client)

Phase 10 foundation — see `docs/architecture/WEB_CLIENT.md` for the full
architecture and `web/README.md` for setup/scripts/layout. Streamlit is
legacy UI, not an operational fallback - Loads, Inbox, and (Phase 10B)
the Dispatch Board are live here. Remaining Streamlit-only workflows are
migration candidates, not a system of record to preserve.

`web/lib/api/generated.ts` is generated from the live FastAPI OpenAPI
schema — never hand-edit it. After any FastAPI route or schema change, run
`npm run api:generate` (from `web/`) to regenerate it.
