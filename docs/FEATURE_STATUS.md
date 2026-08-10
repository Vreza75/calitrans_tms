# Feature Status

Tracks implementation status for features under active development. Not a
full application inventory — only lists what's been explicitly certified or
is mid-flight. Update only when status genuinely changes.

## Operations Inbox certification (`docs/operations_inbox_certification/`)

Framework: a repeatable harness that runs one fixture email through the real
production intake pipeline against a disposable scratch Postgres database
and compares the result field-by-field against a hand-approved
`expected.json`. See `docs/operations_inbox_certification/README.md`.

| Case | Scenario | Status |
|---|---|---|
| CASE-000 | Harness smoke test (infra only) | ACCEPTED |
| CASE-001 | New import, single container | ACCEPTED |
| CASE-002 | New export, single container | ACCEPTED |
| CASE-003 | New local import | ACCEPTED |
| CASE-004 | New local export | ACCEPTED |
| CASE-005 | New import with PDF attachment | ACCEPTED |
| CASE-006 | One booking, four containers | ACCEPTED |
| CASE-007 | Container quantity mismatch (declared vs. found) | ACCEPTED |
| CASE-008 | Existing order delivery-date change | ACCEPTED |
| CASE-009 | Delivery address change after driver assignment | ACCEPTED |
| CASE-010 | Two separate orders in one email | ACCEPTED |

**Next planned work**: none currently queued for the certification harness.
See `tests/fixtures/operations_inbox/CASE-010/verification.md` for CASE-010's
acceptance audit and known limitations.

### Capabilities added while certifying CASE-007

- Multi-container-number extraction from one message
  (`services/email_parser.py::_all_container_numbers`)
- Free-text quantity recognition (`_container_qty_from_sentence`)
- Declared-vs-found quantity mismatch detection
  (`detect_container_quantity_mismatch`) routed to the existing `Review`
  work queue (`enforce_container_quantity_mismatch_review`) — no new
  schema/enum, no hard block on order creation.

### Capabilities added while certifying CASE-010

- Explicit multi-order block detection on `Order N` headers, 2-10 blocks
  (`services/email_parser.py::detect_order_blocks`) — one `order_intake`
  row inserted per detected block instead of one per email
  (`services/operations_inbox_service.py::_prepare_operations_email_records`,
  `_assign_split_row_identity`, `_insert_operations_email_record_row`).
  Row identity: block 0 keeps the real `source_message_id`; block N≥1 gets
  a synthetic `::order-N` suffix; all blocks share `email_thread_id`.
- Narrow customer-name-in-prose fallback (`_customer_from_prose`) for
  emails with no `Customer:` label, consulted only after label-based and
  signature-derived lookups both come up empty.
- Known limitation (not fixed): the per-block insert loop isn't
  transactional — if block N's insert fails after block N-1 already
  committed, block N-1's row is stranded permanently (rerun-dedupe keys on
  the base `message_id`, which block 0 already claimed). Matches the
  existing non-atomic/idempotent-by-check convention used elsewhere in this
  pipeline (e.g. `create_container_work_orders`), not a regression.
- Known limitation (not fixed): `_customer_from_prose` searches subject +
  body combined — an early unrelated `"for <Capitalized Word>"` phrase
  (e.g. subject `"New bookings for August"`) can be misread as a customer
  name. Bounded to the narrow fallback path only; every new order still
  requires dispatcher approval regardless. Full detail:
  `tests/fixtures/operations_inbox/CASE-010/verification.md`.

### Known limitations (not fixed, documented)

- `customer` sometimes falls back to the test sender's placeholder domain
  name instead of a real customer name, when no `Customer:` label exists
  and (for update cases) a matched load already has the real value on file.
- `services/order_parser.py`'s `Customer`/`Port`/`Warehouse` `find_pattern`
  calls have a latent missing-comma + `re.DOTALL` interaction bug — known,
  deliberately not touched (a partial fix regressed a previously-correct
  case; needs a combined fix or none at all).
- `_extract_tokens` in `services/operations_email_triage_service.py` has an
  unaudited dict-repr-into-blob pattern similar to one fixed elsewhere this
  session — likely benign for its current consumers (regex extraction), not
  independently verified.

Full detail on all of the above: `docs/handoffs/CURRENT_SESSION_HANDOFF.md`.

## Backend Boundary Phase 1 (`docs/architecture/BACKEND_BOUNDARY_PHASE_1.md`)

Branch: `architecture/backend-boundary-phase-1`. Adds a framework-neutral
`application/` layer + expanded `/api/v1` FastAPI surface backed by the same
services Streamlit uses, server-side Operations Queue pagination, targeted
(not global) cache invalidation for Operations Inbox actions, transactional
`dispatch_transition_service.apply_transition()`, and removes the plaintext
Motive password field from Admin. Full detail, known limitations, and Phase 2
recommendations in the architecture doc. Does not touch the Operations Inbox
classification/parsing pipeline, does not add authentication, does not add
Next.js, does not touch Motive integration.
