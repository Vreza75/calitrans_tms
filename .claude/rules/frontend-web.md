---
paths:
  - "web/**"
---

# Frontend (Next.js web client)

Phase 10 foundation — see `docs/architecture/WEB_CLIENT.md` for the full
architecture and `web/README.md` for setup/scripts/layout. Streamlit
remains the system of record for every workflow except the read-only
Loads screen this client builds.

`web/lib/api/generated.ts` is generated from the live FastAPI OpenAPI
schema — never hand-edit it. After any FastAPI route or schema change, run
`npm run api:generate` (from `web/`) to regenerate it.
