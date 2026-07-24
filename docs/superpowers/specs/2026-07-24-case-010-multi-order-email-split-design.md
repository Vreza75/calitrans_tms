# CASE-010: Multi-Order Email Split Detection

## Context

Operations Inbox certification (`docs/operations_inbox_certification/`) found
CASE-010 cannot pass today, and like CASE-007 it's a missing capability, not
a bug: `tests/fixtures/operations_inbox/CASE-010/verification.md` documents
that an email declaring two separate "Order 1" / "Order 2" blocks, each with
its own Booking Number and Container Number, is silently processed as if
there were one order (`_insert_operations_email_message` always creates
exactly one `order_intake` row per email message, and every parser it calls
extracts a single Customer, Booking Number, Container Number, etc.). See
that file and `tests/fixtures/operations_inbox/CASE-010/email.txt` for the
exact reference fixture.

Verified by reading the code:

- `services/operations_inbox_service.py`'s `_insert_operations_email_message()`
  (line ~4312) calls `_prepare_operations_email_record()` (line ~4174) exactly
  once per message and performs exactly one `insert into order_intake` per
  call - there is no loop over "how many orders does this message contain."
- `services/email_parser.py`'s `parse_email_text()` (line ~872) is a
  single-pass label parser over the whole body - it has no concept of
  "block boundaries," so a second `Booking Number:` / `Container Number:`
  / `Terminal:` / `Delivery Address:` / `Delivery Need Date:` label later in
  the same body simply overwrites (or is ignored, depending on
  `_find_labeled_value`'s first-match behavior) the first one.
- `order_intake` has a **unique index on `source_message_id`**
  (`database/operations_email_workflow_migration.sql:217-219`,
  `where source_message_id is not null`). Two rows for one email cannot
  share the literal same `source_message_id` - this is a hard DB constraint,
  not a design preference, and directly shapes the row-identity approach
  below.
- `services/operations_inbox_service.py`'s `sync_operations_email_engine()`
  (line ~4531) computes one `message_id` per fetched message and checks
  `operations_email_already_imported(message_id, ...)` **once**, before any
  insert, to decide whether to skip a message on rerun. Whatever row-identity
  scheme is used for split rows must keep this single dedupe check correct.
- `services/operations_multi_container_service.py`'s
  `create_container_work_orders()` is a *different* capability that already
  exists: it splits **one booking** into N *container* placeholders sharing
  one `parent_booking_key`, via the `order_intake_drafts` table (which has a
  UNIQUE index on `conversation_key` - one draft per conversation). CASE-010
  is the opposite shape - **multiple bookings** (multiple booking numbers,
  multiple conversation keys) inside **one email** - so this existing
  machinery is not reused directly, though the "reuse existing correction-
  pass / triage shape" principle from CASE-007 still applies.
- `tests/integration/operations_inbox/harness.py`'s `capture_actual_result()`
  (line ~308) currently reads only `rows[0]` (`primary`) for a given
  `source_message_id` - it has no aggregation across multiple rows from one
  email, which `expected.json`'s `order_numbers`/`container_count`/
  `containers` (each expecting 2 values) requires.
- No `Customer:` label exists anywhere in the CASE-010 fixture; the customer
  name only appears in prose ("...enter these two separate import orders for
  Apex Retail."). This is a real, separate prerequisite gap that must be
  closed for `expected.json`'s `customer: "Apex Retail"` to be reachable at
  all, independent of order-splitting itself.

## Decisions (from brainstorming)

- **Split trigger**: explicit repeated block headers only (`Order 1`,
  `Order 2`, ... - line-start, digits only, case-insensitive). Not "any
  repeated `Booking Number:` label" - narrower, safer, matches the one real
  fixture available, same conservative posture as CASE-007's digits-only
  non-goal.
- **Data model**: two `order_intake` rows for one email (not one row + two
  `order_intake_drafts` rows) - matches `order_intake`'s existing
  one-row-per-order shape that every prior certified case already assumes.
- **Row identity / dedupe**: block 0 keeps the real `source_message_id`
  (so the single rerun-dedupe check in `sync_operations_email_engine` still
  finds it and skips the whole email on rerun). Block N≥1 gets a synthetic
  `source_message_id` = `f"{base_id}::order-{n+1}"` (satisfies the unique
  index). All blocks get `email_thread_id` explicitly forced to the base
  message id (that column already exists and already means "which email
  thread this came from") so a query for "all rows from this email" is
  `email_thread_id = :base_id`. Each row keeps its own `conversation_key`,
  computed by the existing canonical conversation-key function from that
  block's own booking number, so a later reply about one order's booking
  number reopens only that order's work item.
- **Customer prose extraction**: narrow `"for <Company>"` fallback pattern,
  consulted only when no `Customer:` label matched and no reliable
  signature-derived company exists (same guard shape as the existing
  `Contact Company` fallback chain). Not general customer-name NER.
- **Surfacing**: unlike CASE-007, a clean split is not routed through the
  `Review` queue. `expected.json` requires `queue: "New Orders"` and
  `decision: "Create New Order"` - a detected split is a routine multi-order
  email, not a flagged problem. Every fresh `order_intake` row already gets
  `review_status='Open'` at insert time, and the harness's existing
  `requires_human_review` formula (`review_status=='Open' and no linked
  load`) already evaluates `true` for any new, not-yet-approved order with
  no extra flagging needed - "Human review remains authoritative" is
  already satisfied by the normal new-order approval flow. No
  `llm_review_required`/`work_queue="Review"`/`action_required` override is
  added for the split case itself (this was reconsidered after the initial
  brainstorm - the first pass mirrored CASE-007's mismatch-flagging pattern,
  but that contradicts `expected.json`, which was written from the business
  requirement before any code existed and takes precedence).
- **Enforcement**: flagging only. No hard block added to
  `create_load_from_inbox_item()` - matches how every other
  human-review-required case already works in this codebase.

## Design

### 1. Block detection (`services/email_parser.py`)

```python
_ORDER_BLOCK_HEADER_RE = re.compile(r"^\s*Order\s+(\d{1,2})\s*$", re.I | re.M)
_MAX_ORDER_BLOCKS = 10

def detect_order_blocks(subject: str, body: str) -> list[str] | None:
    """Split a message body into per-order text segments when it contains
    2+ explicit "Order N" block headers. Returns None (meaning: use the
    existing single-pass parse, unchanged) when fewer than 2 headers are
    found - this function is purely additive, never overrides the current
    path for a normal single-order email."""
    matches = list(_ORDER_BLOCK_HEADER_RE.finditer(body or ""))
    if len(matches) < 2 or len(matches) > _MAX_ORDER_BLOCKS:
        return None

    preamble = body[: matches[0].start()]
    blocks = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append(f"{preamble}\n{body[start:end]}")
    return blocks
```

- The preamble (everything before "Order 1") is prepended to every block so
  the *existing*, unmodified `parse_email_text()` still sees the shared
  Customer/subject context on every call.
- Nothing about `parse_email_text()` itself changes. Because each call only
  ever sees its own block's text (plus the shared preamble), Order 2's
  `Terminal`/`Address`/`Delivery Need Date` labels are physically absent
  from the text Order 1's parse call receives - there is no bleed-through
  to guard against inside the parser.

### 2. Customer prose fallback (`services/email_parser.py`)

```python
_CUSTOMER_PROSE_RE = re.compile(r"\bfor\s+([A-Z][\w&,.\- ]{2,40})\.?\s*$", re.M)

def _customer_from_prose(text: str) -> str:
    """Fallback for a company name stated in prose ("...orders for Apex
    Retail.") when no Customer: label exists. Only consulted when the
    label-based lookup and signature-derived company both come up empty -
    narrow pattern, not general name extraction."""
    match = _CUSTOMER_PROSE_RE.search(text or "")
    return match.group(1).strip().rstrip(".") if match else ""
```

Wired into `parse_email_text()` right after the existing
`if (not parsed["Customer"] or _is_own_company_value(parsed["Customer"])) and parsed["Contact Company"]:`
block (`email_parser.py:916-917`): if `Customer` is still empty after that,
fall back to `_customer_from_prose(combined)`.

### 3. Per-block record preparation (`services/operations_inbox_service.py`)

```python
def _prepare_operations_email_records(message: dict) -> list[dict]:
    """Returns one prepared record per detected order block, or a
    single-element list (today's unchanged behavior) when the message
    has no multi-order split."""
    raw_body = safe_str(message.get("body"))
    subject = safe_str(message.get("subject")) or "(no subject)"
    blocks = detect_order_blocks(subject, raw_body)
    if not blocks:
        return [_prepare_operations_email_record(message)]

    records = []
    for index, block_text in enumerate(blocks):
        block_message = dict(message)
        block_message["body"] = block_text
        if index > 0:
            block_message["attachments"] = []  # attachments only attach to block 0
        record = _prepare_operations_email_record(block_message)
        record["_order_block_index"] = index
        record["_order_block_count"] = len(blocks)
        records.append(record)
    return records
```

Each call reuses `_prepare_operations_email_record()` completely unchanged -
parsing, attachment saving (block 0 only), classification, and triage all
run exactly as they do today, just once per block instead of once per email.

### 4. Row identity assignment

Inside the new per-block loop (replacing the single-record body of
`_insert_operations_email_message()`), for each record at index `n`,
**only when more than one record was produced** (a single-record result is
left completely untouched, so no existing certified case can regress):

```python
base_message_id = _email_sync_unique_message_id(message)
if len(records) > 1:
    for n, record in enumerate(records):
        record["message_id"] = base_message_id if n == 0 else f"{base_message_id}::order-{n + 1}"
        record["thread_id"] = base_message_id  # forced, overrides the normal fallback
```

No triage/queue/review-flag fields are touched - see the "Surfacing"
decision above. `conversation_key` is left untouched - it's already computed per-record
inside `_prepare_operations_email_record()` from that record's own `parsed`
dict (via `build_operations_email_classification`), so each block's own
Booking Number already produces a distinct conversation key with no extra
code.

The existing `insert into order_intake (...)` statement in
`_insert_operations_email_message()` is unchanged; it just runs once per
record in the loop instead of once per message.

### 5. Harness updates (`tests/integration/operations_inbox/harness.py`)

`capture_actual_result()`:

- Query changes from `where source_message_id = :message_id` to
  `where email_thread_id = :message_id or source_message_id = :message_id`,
  `order by id asc` (unchanged ordering clause).
- `order_numbers` = `[r["booking_number"] for r in rows if r.get("booking_number")]`
  (today's single-row case degenerates to a 0-or-1-element list, unchanged
  output).
- `containers` = concatenation of each row's own container list (reuses the
  existing per-row `Container Numbers` / `Container Number` fallback logic,
  looped instead of applied once).
- `container_count` = sum of each row's own per-row `container_count` value
  (same stated-qty-first-else-len(containers) logic as today, summed across
  rows).
- `decision` derivation is unchanged - a clean split still resolves to
  `"Create New Order"` (or whatever the existing `matched_load_id`/mismatch
  branches already produce), matching `expected.json`'s
  `decision: "Create New Order"`. No new branch is added for the split case
  itself.
- `requires_human_review` becomes
  `any(bool(r.get("llm_review_required")) for r in rows) or (str(primary.get("review_status") or "") == "Open" and not primary.get("linked_load_id"))`
  - the second disjunct (unchanged from today's single-row formula) already
    evaluates `true` for any freshly inserted, not-yet-approved row, which
    is what actually makes `expected.json`'s `requires_human_review: true`
    reachable for CASE-010 with no new flagging logic.
- `intent`, `service_flow`, `customer`, `queue` continue to read from `rows[0]`
  (`primary`) only - these are expected to agree across all rows from one
  split email (same customer, same import/export flow), so no aggregation
  is needed for them.

Every existing 1-row case (CASE-000 through CASE-009) produces byte-identical
output after this change: `rows` has exactly one element, so every `any()` /
`sum()` / list-comprehension above degenerates to today's single-row value.

### 6. CASE-010 fixture updates

- Re-run against the real scratch DB once the above lands; verify
  `tests/fixtures/operations_inbox/CASE-010/expected.json` matches with no
  further changes needed (it was written from the business requirement
  up front, per the framework's hard rule).
- Add the permanent regression test file (mirrors CASE-001..009's pattern):
  passes-clean, rerun-no-duplicates (specifically: confirms a rerun inserts
  zero additional rows for *either* block, not just block 0), deterministic-
  across-runs, plus a dedicated `test_case_010_...` asserting both booking
  numbers and both container numbers survive and land on separate rows with
  separate `conversation_key` values.
- Delete the `NOT ACCEPTED` framing from `verification.md`, replace with the
  acceptance audit once it passes.

## Non-goals (explicit)

- Any repeated-label trigger without an explicit "Order N" header.
- Nested multi-container blocks (an "Order N" block that itself declares a
  `Container Qty` greater than its own listed container numbers) - CASE-007's
  mismatch detector still runs per-block since it's unchanged and pure, so
  it isn't actively broken by this design, but no fixture exercises it and
  it is not a certification target here.
- Attachments split across blocks - attachment merge only applies to block 0.
- More than 10 order blocks in one email.
- General customer-name NER beyond the narrow `"for <Company>"` pattern.
- Any change to `create_load_from_inbox_item()` - no hard block.
- Any UI change in `pages_app/operations_inbox.py`.
- A new `source_email_group_id`-style schema column - `email_thread_id`
  (already a column) is reused for this instead, avoiding a migration.

## Testing

- Pure-function unit tests (no DB), added to a new
  `tests/test_order_block_splitting.py`:
  - `detect_order_blocks`: 0 headers → `None`; 1 header → `None`; 2 headers
    → 2 segments with correct boundaries; 10 headers → 10 segments; 11
    headers → `None` (over cap); malformed header ("Order ABC", "Order"
    with no number) → not matched, falls through to `None` when fewer than
    2 real matches remain.
  - `_customer_from_prose`: "...for Apex Retail." → `"Apex Retail"`; no
    "for ..." phrase → `""`; already-labeled `Customer:` present → fallback
    never consulted (tested via `parse_email_text`, not this function in
    isolation).
- `python -m compileall` + full suite (`pytest -q`) after each change, same
  as every prior certification case - zero regressions is the bar.
- Live run: `python scripts/run_inbox_case.py CASE-010` against the scratch
  DB, twice independently, plus the standard 3x targeted + duplicate-rerun
  protocol (confirming row count stays at 2, not 1 and not 4) before
  marking ACCEPTED.
