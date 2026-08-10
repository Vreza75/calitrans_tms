# CASE-010 Multi-Order Email Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when an email contains two or more explicit "Order N" blocks, each with its own Booking Number/Container Number/etc., and create one `order_intake` row per block instead of silently keeping only the first block's fields.

**Architecture:** Add a pure block-splitting function to `services/email_parser.py` that slices a body into per-block text segments on explicit `Order N` headers (2+ headers required, else unchanged single-order behavior). `services/operations_inbox_service.py` gains a thin orchestration layer that calls the existing, unmodified `_prepare_operations_email_record()` once per block instead of once per email, assigns each block a distinct row identity (to satisfy `order_intake`'s unique index on `source_message_id`), and inserts one row per block using the existing insert SQL unchanged. The certification harness is extended to aggregate multiple rows from one email instead of reading only the first.

**Tech Stack:** Python 3.14, pytest, existing `services/email_parser.py` / `services/operations_inbox_service.py` modules, the Operations Inbox certification harness (`tests/integration/operations_inbox/harness.py`) and its scratch-Postgres DB.

## Global Constraints

- Split trigger is explicit `Order N` headers only (line-start, digits only, case-insensitive) — 2+ required to split; any other repeated-label shape stays on today's single-order path, completely unchanged.
- Cap at 10 blocks — more than 10 headers is treated as a parsing anomaly, not a real case (`detect_order_blocks` returns `None`).
- A clean split is **not** routed through the `Review` queue — no `llm_review_required`/`work_queue="Review"`/`action_required` override for the split case itself. `expected.json` requires `queue: "New Orders"`, `decision: "Create New Order"`; `requires_human_review: true` already comes from the existing `review_status=='Open' and no linked load` formula, unchanged.
- No hard block in `create_load_from_inbox_item()` — nothing in this plan touches order/load creation at all, only intake.
- No UI changes in `pages_app/operations_inbox.py`.
- No new database columns or enum values — `email_thread_id` (existing column) is reused to link split rows; `source_message_id` gets a synthetic suffix for rows beyond the first.
- The single-order path (any email with 0 or 1 `Order N` headers) must be provably unchanged by every task — every step that touches shared code must include a check that CASE-000 through CASE-009 still pass unmodified.
- After every task: `python -m compileall -q services/email_parser.py services/operations_inbox_service.py tests/integration/operations_inbox/harness.py tests` then `pytest -q` (unset `INBOX_CERTIFICATION_DATABASE_URL` for this) with zero failures before moving to the next task.
- Full spec: `docs/superpowers/specs/2026-07-24-case-010-multi-order-email-split-design.md` (read the corrected version — the original brainstorm had a Review-queue flagging step that was removed after cross-checking `expected.json`). Recurring bug patterns to avoid: `docs/CODE_REVIEW_PLAYBOOK.md` §38.

---

### Task 1: Order-block detection

**Files:**
- Modify: `services/email_parser.py` (add `detect_order_blocks`, placed right after `detect_container_quantity_mismatch`, which currently ends at line 568)
- Test: `tests/test_order_block_splitting.py` (new file)

**Interfaces:**
- Produces: `detect_order_blocks(body: str) -> list[str] | None` — `None` when fewer than 2 or more than 10 `Order N` headers are found (meaning: use today's unchanged single-pass parse). Otherwise a list of per-block text segments, each segment being the shared preamble (everything before the first header) followed by that block's own header-to-next-header (or end-of-body) text.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_order_block_splitting.py`:

```python
"""Tests for CASE-010's multi-order email split detection: slicing a body
into per-block text segments on explicit "Order N" headers, and a narrow
customer-name prose fallback for emails that state a company name only in
prose (e.g. "...orders for Apex Retail.") with no Customer: label.
"""
from services.email_parser import detect_order_blocks


def test_returns_none_for_zero_headers():
    assert detect_order_blocks("Booking Number: A\nContainer Number: B\n") is None


def test_returns_none_for_a_single_header():
    body = "Order 1\nBooking Number: A\nContainer Number: B\n"
    assert detect_order_blocks(body) is None


def test_splits_two_blocks_without_cross_contamination():
    body = (
        "Please enter these two orders.\n\n"
        "Order 1\n"
        "Booking Number: APEX-260810\n"
        "Container Number: HLXU3000001\n\n"
        "Order 2\n"
        "Booking Number: APEX-260811\n"
        "Container Number: HLXU3000002\n"
    )
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert len(blocks) == 2
    assert "APEX-260810" in blocks[0]
    assert "HLXU3000001" in blocks[0]
    assert "APEX-260811" not in blocks[0]
    assert "HLXU3000002" not in blocks[0]
    assert "APEX-260811" in blocks[1]
    assert "HLXU3000002" in blocks[1]
    assert "APEX-260810" not in blocks[1]
    assert "HLXU3000001" not in blocks[1]


def test_shared_preamble_is_present_in_every_block():
    body = (
        "Please enter these two orders for Apex Retail.\n\n"
        "Order 1\nBooking Number: A\n\n"
        "Order 2\nBooking Number: B\n"
    )
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert "for Apex Retail" in blocks[0]
    assert "for Apex Retail" in blocks[1]


def test_ignores_non_numeric_headers():
    body = "Order Alpha\nBooking Number: A\nOrder Beta\nBooking Number: B\n"
    assert detect_order_blocks(body) is None


def test_allows_exactly_ten_blocks():
    body = "\n".join(f"Order {i}\nBooking Number: B{i}" for i in range(1, 11))
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert len(blocks) == 10


def test_returns_none_above_ten_blocks():
    body = "\n".join(f"Order {i}\nBooking Number: B{i}" for i in range(1, 12))
    assert detect_order_blocks(body) is None


def test_returns_none_for_empty_body():
    assert detect_order_blocks("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: FAIL — `ImportError: cannot import name 'detect_order_blocks'`.

- [ ] **Step 3: Add `detect_order_blocks`**

In `services/email_parser.py`, add this right after `detect_container_quantity_mismatch` (search for `def detect_container_quantity_mismatch` — its closing `}` and blank lines currently end at line 568-569; add the new code after that):

```python
_ORDER_BLOCK_HEADER_RE = re.compile(r"^\s*Order\s+(\d{1,2})\s*$", re.I | re.M)
_MAX_ORDER_BLOCKS = 10


def detect_order_blocks(body: str) -> list[str] | None:
    """Split a message body into per-order text segments when it contains
    2+ explicit "Order N" block headers. Returns None (meaning: use the
    existing single-pass parse, unchanged) when fewer than 2 headers are
    found, or more than _MAX_ORDER_BLOCKS - this function is purely
    additive and never overrides the current path for a normal
    single-order email."""
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: 8 passed

- [ ] **Step 5: Full regression check**

Run: `python -m compileall -q services/email_parser.py`
Run: `pytest -q` (with `INBOX_CERTIFICATION_DATABASE_URL` unset)
Expected: no failures, same counts as before plus the 8 new tests.

- [ ] **Step 6: Commit**

```bash
git add services/email_parser.py tests/test_order_block_splitting.py
git commit -m "feat: detect explicit multi-order block headers in an email body"
```

---

### Task 2: Customer prose fallback

**Files:**
- Modify: `services/email_parser.py:916-917` (add the fallback call right after the existing `Contact Company` -> `Customer` block)
- Test: `tests/test_order_block_splitting.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `_customer_from_prose(text: str) -> str` — the first matched company name, stripped of a trailing period, or `""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_order_block_splitting.py`:

```python
from services.email_parser import _customer_from_prose, parse_email_text


def test_customer_from_prose_matches_trailing_for_phrase():
    text = "Please enter these two separate import orders for Apex Retail."
    assert _customer_from_prose(text) == "Apex Retail"


def test_customer_from_prose_no_trailing_period():
    text = "Booking Number: A\nPlease ship this for Continental Industries Group\n"
    assert _customer_from_prose(text) == "Continental Industries Group"


def test_customer_from_prose_no_match_returns_empty():
    assert _customer_from_prose("No company mentioned here at all.") == ""


def test_parse_email_text_uses_prose_fallback_when_no_customer_label():
    body = (
        "Please enter these two separate import orders for Apex Retail.\n"
        "Booking Number: APEX-260810\n"
    )
    parsed = parse_email_text("", body)
    assert parsed["Customer"] == "Apex Retail"


def test_parse_email_text_label_still_wins_over_prose_fallback():
    body = "Customer: Real Customer Inc\nPlease enter this for Apex Retail.\n"
    parsed = parse_email_text("", body)
    assert parsed["Customer"] == "Real Customer Inc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: FAIL — `ImportError: cannot import name '_customer_from_prose'`.

- [ ] **Step 3: Add `_customer_from_prose` and wire it in**

Add this function in `services/email_parser.py`, right after `_all_container_numbers` (or anywhere above `parse_email_text`, which starts at line 872 — place it directly above that function):

```python
_CUSTOMER_PROSE_RE = re.compile(r"\bfor\s+([A-Z][\w&,.\- ]{2,40})\.?\s*$", re.M)


def _customer_from_prose(text: str) -> str:
    """Fallback for a company name stated in prose ("...orders for Apex
    Retail.") when no Customer: label exists. Only consulted when the
    label-based lookup and signature-derived company both come up empty -
    a narrow pattern, not general name extraction."""
    match = _CUSTOMER_PROSE_RE.search(text or "")
    return match.group(1).strip().rstrip(".") if match else ""
```

In `parse_email_text`, find this existing block (currently lines 916-917):

```python
    if (not parsed["Customer"] or _is_own_company_value(parsed["Customer"])) and parsed["Contact Company"]:
        parsed["Customer"] = parsed["Contact Company"]
```

Add immediately after it:

```python
    if not parsed["Customer"]:
        parsed["Customer"] = _customer_from_prose(combined)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: 13 passed (8 from Task 1 + 5 new)

- [ ] **Step 5: Full regression check**

Run: `python -m compileall -q services/email_parser.py`
Run: `pytest -q` (with `INBOX_CERTIFICATION_DATABASE_URL` unset)
Expected: no failures.

- [ ] **Step 6: Commit**

```bash
git add services/email_parser.py tests/test_order_block_splitting.py
git commit -m "feat: recognize a customer name stated in prose when no label exists"
```

---

### Task 3: Per-block record preparation

**Files:**
- Modify: `services/operations_inbox_service.py:17-22` (import `detect_order_blocks`)
- Modify: `services/operations_inbox_service.py` (add `_prepare_operations_email_records`, placed right after `_prepare_operations_email_record`, which currently ends at line 4309-4310, immediately before `def _insert_operations_email_message` at line 4312)
- Test: `tests/test_order_block_splitting.py` (append)

**Interfaces:**
- Consumes: `detect_order_blocks` (Task 1).
- Produces: `_prepare_operations_email_records(message: dict) -> list[dict]` — one prepared record per detected block (each record has the same shape `_prepare_operations_email_record` already returns, plus `_order_block_index`/`_order_block_count`), or a single-element list unchanged from today when no split is detected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_order_block_splitting.py`:

```python
from unittest import mock

from services import operations_inbox_service


def test_prepare_records_returns_single_record_when_no_split():
    message = {"subject": "Test", "body": "Booking Number: A\n"}
    with mock.patch.object(
        operations_inbox_service,
        "_prepare_operations_email_record",
        return_value={"parsed": {}, "triage": {}},
    ) as mocked:
        records = operations_inbox_service._prepare_operations_email_records(message)
    assert len(records) == 1
    mocked.assert_called_once_with(message)


def test_prepare_records_calls_once_per_detected_block_with_scoped_body():
    message = {
        "subject": "Two New Import Bookings",
        "body": (
            "Order 1\nBooking Number: APEX-260810\n\n"
            "Order 2\nBooking Number: APEX-260811\n"
        ),
        "attachments": [{"filename": "x.pdf", "content": b"stub"}],
    }
    calls = []

    def fake_prepare(block_message):
        calls.append(block_message)
        return {"parsed": {}, "triage": {}}

    with mock.patch.object(
        operations_inbox_service,
        "_prepare_operations_email_record",
        side_effect=fake_prepare,
    ):
        records = operations_inbox_service._prepare_operations_email_records(message)

    assert len(records) == 2
    assert records[0]["_order_block_index"] == 0
    assert records[1]["_order_block_index"] == 1
    assert records[0]["_order_block_count"] == 2
    assert records[1]["_order_block_count"] == 2
    assert "APEX-260810" in calls[0]["body"]
    assert "APEX-260811" not in calls[0]["body"]
    assert "APEX-260811" in calls[1]["body"]
    assert "APEX-260810" not in calls[1]["body"]
    # Original message dict is never mutated in place.
    assert message["body"] == (
        "Order 1\nBooking Number: APEX-260810\n\n"
        "Order 2\nBooking Number: APEX-260811\n"
    )
    # Attachments only attach to block 0.
    assert calls[0]["attachments"] == message["attachments"]
    assert calls[1]["attachments"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: FAIL — `AttributeError: module 'services.operations_inbox_service' has no attribute '_prepare_operations_email_records'`.

- [ ] **Step 3: Add the import and the new function**

In `services/operations_inbox_service.py`, change the import block (currently lines 17-22):

```python
from services.email_parser import (
    _append_note,
    detect_container_quantity_mismatch,
    extract_latest_email_body,
    parse_email_text,
)
```

to:

```python
from services.email_parser import (
    _append_note,
    detect_container_quantity_mismatch,
    detect_order_blocks,
    extract_latest_email_body,
    parse_email_text,
)
```

Add this function right after `_prepare_operations_email_record` (search for `def _prepare_operations_email_record` — add immediately after its closing `return {...}` block, before `def _insert_operations_email_message`):

```python
def _prepare_operations_email_records(message: dict) -> list[dict]:
    """Returns one prepared record per detected order block (see
    services.email_parser.detect_order_blocks), or a single-element list -
    today's unchanged behavior - when the message has no multi-order
    split. Each record is produced by the existing, unmodified
    _prepare_operations_email_record(), called once per block; the only
    difference per call is the "body" (and, for block 1+, "attachments")
    field of the message dict it's given."""
    raw_body = safe_str(message.get("body"))
    blocks = detect_order_blocks(raw_body)
    if not blocks:
        return [_prepare_operations_email_record(message)]

    records = []
    for index, block_text in enumerate(blocks):
        block_message = dict(message)
        block_message["body"] = block_text
        if index > 0:
            block_message["attachments"] = []
        record = _prepare_operations_email_record(block_message)
        record["_order_block_index"] = index
        record["_order_block_count"] = len(blocks)
        records.append(record)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: 15 passed (13 from Tasks 1-2 + 2 new)

- [ ] **Step 5: Full regression check**

Run: `python -m compileall -q services/email_parser.py services/operations_inbox_service.py`
Run: `pytest -q` (with `INBOX_CERTIFICATION_DATABASE_URL` unset)
Expected: no failures.

- [ ] **Step 6: Commit**

```bash
git add services/operations_inbox_service.py tests/test_order_block_splitting.py
git commit -m "feat: prepare one record per detected order block"
```

---

### Task 4: Row identity assignment and insert-loop wiring

**Files:**
- Modify: `services/operations_inbox_service.py` (add `_assign_split_row_identity`, placed right after `enforce_container_quantity_mismatch_review`, which currently ends at line 312, before the `# Compatibility aliases` section at line 315)
- Modify: `services/operations_inbox_service.py:4312-4451` (split `_insert_operations_email_message` into a per-row insert function plus a thin per-block loop)
- Modify: `services/operations_inbox_service.py` (in `sync_operations_email_engine`, update the `touched_conversations` update to cover every split row's conversation key, not just the first)
- Test: `tests/test_order_block_splitting.py` (append)

**Interfaces:**
- Consumes: `_prepare_operations_email_records` (Task 3), `_email_sync_unique_message_id` (existing).
- Produces: `_assign_split_row_identity(records: list[dict], base_message_id: str) -> list[dict]` — no-op passthrough when `len(records) <= 1`; otherwise mutates and returns the same list with `message_id`/`thread_id` set per the row-identity scheme. `_insert_operations_email_record_row(message: dict, record: dict) -> dict` — the existing single-row insert body, now taking `record` explicitly. `_insert_operations_email_message(message: dict) -> dict` — same public signature and return shape as today (plus a new `conversation_keys` key and `split_row_count`), now inserting 1-to-N rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_order_block_splitting.py`:

```python
from services.operations_inbox_service import _assign_split_row_identity


def test_assign_split_identity_is_noop_for_single_record():
    records = [{"message_id": "orig-id", "thread_id": "orig-thread", "parsed": {}, "triage": {}}]
    result = _assign_split_row_identity(records, "base-id")
    assert result[0]["message_id"] == "orig-id"
    assert result[0]["thread_id"] == "orig-thread"


def test_assign_split_identity_sets_suffixed_ids_for_two_blocks():
    records = [
        {"parsed": {"Booking Number": "APEX-260810"}, "triage": {}},
        {"parsed": {"Booking Number": "APEX-260811"}, "triage": {}},
    ]
    result = _assign_split_row_identity(records, "base-id")
    assert result[0]["message_id"] == "base-id"
    assert result[1]["message_id"] == "base-id::order-2"
    assert result[0]["thread_id"] == "base-id"
    assert result[1]["thread_id"] == "base-id"


def test_assign_split_identity_handles_more_than_two_blocks():
    records = [{"parsed": {}, "triage": {}} for _ in range(4)]
    result = _assign_split_row_identity(records, "base-id")
    assert [r["message_id"] for r in result] == [
        "base-id", "base-id::order-2", "base-id::order-3", "base-id::order-4",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: FAIL — `ImportError: cannot import name '_assign_split_row_identity'`.

- [ ] **Step 3: Add `_assign_split_row_identity`**

Add this function in `services/operations_inbox_service.py`, right after `enforce_container_quantity_mismatch_review` (search for `def enforce_container_quantity_mismatch_review` — add immediately after its closing `return corrected`, before the `# Compatibility aliases used by the Streamlit page.` comment):

```python
def _assign_split_row_identity(records: list[dict], base_message_id: str) -> list[dict]:
    """Assigns row-identity fields (message_id, thread_id) when more than
    one record was produced (a detected multi-order split). A single-record
    result is returned completely untouched - order_intake's unique index
    on source_message_id is never an issue for today's single-order path,
    so nothing about it changes.

    Block 0 keeps the real base_message_id (source_message_id) so the
    single rerun-dedupe check in sync_operations_email_engine, which is
    keyed on that same base id, still finds it and skips the whole email
    on rerun. Block N>=1 gets a synthetic suffix to satisfy order_intake's
    unique index on source_message_id. Every record's thread_id is forced
    to base_message_id so a query for "all rows from this email" is
    email_thread_id = base_message_id. Pure - no DB/IO."""
    if len(records) <= 1:
        return records

    for index, record in enumerate(records):
        record["message_id"] = base_message_id if index == 0 else f"{base_message_id}::order-{index + 1}"
        record["thread_id"] = base_message_id
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: 18 passed (15 from Tasks 1-3 + 3 new)

- [ ] **Step 5: Refactor `_insert_operations_email_message` into a per-row insert plus a thin loop**

In `services/operations_inbox_service.py`, find the existing function (currently lines 4312-4451):

```python
def _insert_operations_email_message(message: dict) -> dict:
    record = _prepare_operations_email_record(message)

    subject = record["subject"]
    sender = record["sender"]
    received_at = record["received_at"]
    latest_body = record["latest_body"]
    direction = record["direction"]
    message_id = record["message_id"]
    parsed = record["parsed"]
    classification = record["classification"]
    triage = record["triage"]
    saved_attachments = record["saved_attachments"]
    conversation_key = record["conversation_key"]
    thread_id = record["thread_id"]
    normalized_subject = record["normalized_subject"]
    conversation_status = record["conversation_status"]
    source = _email_sync_source_for_direction(direction)

    execute(
        """
        insert into order_intake (
```

Replace the function signature and its first block (everything from `def _insert_operations_email_message(message: dict) -> dict:` down to `source = _email_sync_source_for_direction(direction)`, inclusive) with:

```python
def _insert_operations_email_record_row(message: dict, record: dict) -> dict:
    subject = record["subject"]
    sender = record["sender"]
    received_at = record["received_at"]
    latest_body = record["latest_body"]
    direction = record["direction"]
    message_id = record["message_id"]
    parsed = record["parsed"]
    classification = record["classification"]
    triage = record["triage"]
    saved_attachments = record["saved_attachments"]
    conversation_key = record["conversation_key"]
    thread_id = record["thread_id"]
    normalized_subject = record["normalized_subject"]
    conversation_status = record["conversation_status"]
    source = _email_sync_source_for_direction(direction)

    execute(
        """
        insert into order_intake (
```

Leave the entire `insert into order_intake (...) values (...)` statement and its parameter dict (everything between that `execute(` line and its closing `)` — the whole SQL block plus params) completely unchanged.

Immediately after that `execute(...)` call, find the existing return block:

```python
    return {
        "message_id": message_id,
        "conversation_key": conversation_key,
        "thread_id": thread_id,
        "direction": direction.lower() if direction else "inbound",
        "attachments_saved": len(saved_attachments),
        "llm_required": bool(triage.get("llm_required") or triage.get("llm_review_required")),
        "store_only": bool(triage.get("store_only")),
        "work_level": triage.get("work_level"),
        "work_queue": triage.get("work_queue"),
    }
```

Leave this return block exactly as-is (it now closes `_insert_operations_email_record_row` instead of `_insert_operations_email_message`), and add the new orchestrator function right after it:

```python
def _insert_operations_email_message(message: dict) -> dict:
    records = _prepare_operations_email_records(message)
    base_message_id = _email_sync_unique_message_id(message)
    records = _assign_split_row_identity(records, base_message_id)

    results = [_insert_operations_email_record_row(message, record) for record in records]

    primary = results[0]
    return {
        **primary,
        "conversation_keys": [result["conversation_key"] for result in results],
        "split_row_count": len(results),
    }
```

- [ ] **Step 6: Update `sync_operations_email_engine`'s conversation-tracking to cover every split row**

In `services/operations_inbox_service.py`, find (inside `sync_operations_email_engine`, currently around line 4627-4628):

```python
                if inserted.get("conversation_key"):
                    touched_conversations.add(inserted["conversation_key"])
```

Change to:

```python
                touched_conversations.update(
                    key for key in inserted.get("conversation_keys") or [inserted.get("conversation_key")] if key
                )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_order_block_splitting.py -v`
Expected: 18 passed (unchanged from Step 4 — this step's changes are exercised at the DB-integration level in Task 5, not by these pure-function tests).

- [ ] **Step 8: Full regression check**

Run: `python -m compileall -q services/email_parser.py services/operations_inbox_service.py`
Run: `pytest -q` (with `INBOX_CERTIFICATION_DATABASE_URL` unset)
Expected: no failures, same counts as before plus the 18 tests in `tests/test_order_block_splitting.py`.

- [ ] **Step 9: Commit**

```bash
git add services/operations_inbox_service.py tests/test_order_block_splitting.py
git commit -m "feat: insert one order_intake row per detected order block"
```

---

### Task 5: Harness aggregation, CASE-010 certification, and cleanup

**Files:**
- Modify: `tests/integration/operations_inbox/harness.py:308-403` (`capture_actual_result` — query and aggregation across multiple rows)
- Modify: `tests/fixtures/operations_inbox/CASE-010/expected.json` (only if the live run's `actual.json` differs — see Step 3)
- Modify: `tests/fixtures/operations_inbox/CASE-010/verification.md`
- Create: `tests/integration/operations_inbox/test_case_010_multi_order_split.py`

**Interfaces:**
- Consumes: `email_thread_id`/`source_message_id` row-identity scheme (Task 4), `parsed["Booking Number"]`/`parsed["Container Numbers"]`/`parsed["Customer"]` per row (Tasks 1-3).
- Produces: nothing further downstream — this is the last task in the plan.

- [ ] **Step 1: Replace `capture_actual_result`'s query and aggregation**

In `tests/integration/operations_inbox/harness.py`, find the entire existing function (currently lines 308-403):

```python
def capture_actual_result(fixture: Fixture) -> dict:
    """Read back the order_intake row(s) created for this case and translate
    them into the expected.json schema. This mapping is intentionally
    centralized here so per-case tuning (as real cases are certified) happens
    in one place instead of scattering ad hoc field lookups."""
    import db_client

    df = db_client.read_df(
        "select * from order_intake where source_message_id = :message_id order by id asc",
        {"message_id": _stable_message_id(fixture.message)},
    )

    if df.empty:
        return {field_name: None for field_name in EXPECTED_SCHEMA_FIELDS} | {"_row_count": 0}

    rows = [row.to_dict() for _, row in df.iterrows()]
    primary = rows[0]
    parsed = primary.get("parsed_data") or {}
    if isinstance(parsed, str):
        parsed = json.loads(parsed) if parsed else {}

    container_number = parsed.get("Container Number") or ""
    container_numbers_list = parsed.get("Container Numbers") or []
    containers = container_numbers_list or ([container_number] if container_number else [])
    booking_number = parsed.get("Booking Number") or ""

    # For a multi-container booking, "Container Qty" (a stated count, e.g.
    # "4 X 40HC") is known before any physical container numbers are -
    # container_count must reflect that stated quantity, not silently
    # collapse to len(containers)==0/1, per the "never default an unknown
    # quantity to 1, never treat the quantity as a container number" rule.
    try:
        container_qty = int(str(parsed.get("Container Qty") or "").strip())
    except ValueError:
        container_qty = None
    container_count = container_qty if container_qty else (len(containers) or None)

    decision = "Create New Order"
    if primary.get("matched_load_id"):
        decision = "Update Existing Order"
    elif primary.get("llm_review_required") and "mismatch" in str(primary.get("action_required") or "").lower():
        decision = "Human Review Required"

    actual = {
        "intent": primary.get("request_type"),
        "service_flow": parsed.get("TYPE") or parsed.get("Service Flow") or None,
        "queue": primary.get("work_queue"),
        "decision": decision,
        "existing_load_match": primary.get("matched_load_id"),
        "booking_number": booking_number,
        "order_numbers": [booking_number] if booking_number else [],
        "container_count": container_count,
        "containers": containers,
        "customer": parsed.get("Customer") or None,
        "pickup": _sparse(
            {
                "terminal": parsed.get("Port"),
                "empty_pickup": parsed.get("Empty Pickup"),
                "customer_pickup": parsed.get("Customer Pickup"),
                "customer_pickup_address": parsed.get("Customer Pickup Address"),
            }
        ),
        "delivery": _sparse(
            {
                "warehouse": parsed.get("Warehouse"),
                "address": parsed.get("Address"),
            }
        ),
        "dates": _sparse(
            {
                "delivery_need_date": parsed.get("Delivery Need Date"),
                "last_free_day": parsed.get("LFD"),
                "pickup_date": parsed.get("Pickup Date"),
                "document_cutoff": parsed.get("Document Cutoff"),
            }
        ),
        "references": _sparse(
            {
                "reference_number": parsed.get("Reference Number"),
                "container_size": parsed.get("Size"),
                "contact_name": parsed.get("Contact Name"),
                "contact_email": parsed.get("Contact Email"),
                "contact_phone": parsed.get("Contact Phone"),
            }
        ),
        "missing_required_fields": [],
        # True whenever a dispatcher decision is still pending (no order/update
        # committed yet) - AI never creates or changes operational records
        # without confirmation, so this is the general "not yet approved" gate,
        # not just the narrower low-confidence llm_review_required flag.
        "requires_human_review": bool(primary.get("llm_review_required"))
        or (str(primary.get("review_status") or "") == "Open" and not primary.get("linked_load_id")),
        "_row_count": len(rows),
        "_row_ids": [row.get("id") for row in rows],
    }
    return actual
```

Replace it entirely with:

```python
def _row_parsed_data(row: dict) -> dict:
    parsed = row.get("parsed_data") or {}
    if isinstance(parsed, str):
        parsed = json.loads(parsed) if parsed else {}
    return parsed


def _row_containers_and_count(parsed: dict) -> tuple[list[str], int | None]:
    container_number = parsed.get("Container Number") or ""
    container_numbers_list = parsed.get("Container Numbers") or []
    containers = container_numbers_list or ([container_number] if container_number else [])

    # For a multi-container booking, "Container Qty" (a stated count, e.g.
    # "4 X 40HC") is known before any physical container numbers are -
    # container_count must reflect that stated quantity, not silently
    # collapse to len(containers)==0/1, per the "never default an unknown
    # quantity to 1, never treat the quantity as a container number" rule.
    try:
        container_qty = int(str(parsed.get("Container Qty") or "").strip())
    except ValueError:
        container_qty = None
    container_count = container_qty if container_qty else (len(containers) or None)
    return containers, container_count


def capture_actual_result(fixture: Fixture) -> dict:
    """Read back the order_intake row(s) created for this case and translate
    them into the expected.json schema. This mapping is intentionally
    centralized here so per-case tuning (as real cases are certified) happens
    in one place instead of scattering ad hoc field lookups.

    A case whose email contains multiple detected order blocks (CASE-010)
    produces more than one order_intake row, linked by a shared
    email_thread_id - order_numbers/containers/container_count are
    aggregated across every row from this email; the remaining fields are
    read from the first row only (intent/service_flow/customer/queue are
    expected to agree across every row from one split email)."""
    import db_client

    df = db_client.read_df(
        """
        select * from order_intake
        where email_thread_id = :message_id or source_message_id = :message_id
        order by id asc
        """,
        {"message_id": _stable_message_id(fixture.message)},
    )

    if df.empty:
        return {field_name: None for field_name in EXPECTED_SCHEMA_FIELDS} | {"_row_count": 0}

    rows = [row.to_dict() for _, row in df.iterrows()]
    primary = rows[0]
    parsed = _row_parsed_data(primary)

    order_numbers: list[str] = []
    containers: list[str] = []
    container_counts: list[int] = []
    for row in rows:
        row_parsed = _row_parsed_data(row)
        booking = row_parsed.get("Booking Number") or ""
        if booking:
            order_numbers.append(booking)
        row_containers, row_count = _row_containers_and_count(row_parsed)
        containers.extend(row_containers)
        if row_count:
            container_counts.append(row_count)

    booking_number = order_numbers[0] if order_numbers else ""
    container_count = sum(container_counts) if container_counts else None

    decision = "Create New Order"
    if primary.get("matched_load_id"):
        decision = "Update Existing Order"
    elif primary.get("llm_review_required") and "mismatch" in str(primary.get("action_required") or "").lower():
        decision = "Human Review Required"

    actual = {
        "intent": primary.get("request_type"),
        "service_flow": parsed.get("TYPE") or parsed.get("Service Flow") or None,
        "queue": primary.get("work_queue"),
        "decision": decision,
        "existing_load_match": primary.get("matched_load_id"),
        "booking_number": booking_number,
        "order_numbers": order_numbers,
        "container_count": container_count,
        "containers": containers,
        "customer": parsed.get("Customer") or None,
        "pickup": _sparse(
            {
                "terminal": parsed.get("Port"),
                "empty_pickup": parsed.get("Empty Pickup"),
                "customer_pickup": parsed.get("Customer Pickup"),
                "customer_pickup_address": parsed.get("Customer Pickup Address"),
            }
        ),
        "delivery": _sparse(
            {
                "warehouse": parsed.get("Warehouse"),
                "address": parsed.get("Address"),
            }
        ),
        "dates": _sparse(
            {
                "delivery_need_date": parsed.get("Delivery Need Date"),
                "last_free_day": parsed.get("LFD"),
                "pickup_date": parsed.get("Pickup Date"),
                "document_cutoff": parsed.get("Document Cutoff"),
            }
        ),
        "references": _sparse(
            {
                "reference_number": parsed.get("Reference Number"),
                "container_size": parsed.get("Size"),
                "contact_name": parsed.get("Contact Name"),
                "contact_email": parsed.get("Contact Email"),
                "contact_phone": parsed.get("Contact Phone"),
            }
        ),
        "missing_required_fields": [],
        # True whenever a dispatcher decision is still pending (no order/update
        # committed yet) - AI never creates or changes operational records
        # without confirmation, so this is the general "not yet approved" gate,
        # not just the narrower low-confidence llm_review_required flag.
        "requires_human_review": any(bool(row.get("llm_review_required")) for row in rows)
        or (str(primary.get("review_status") or "") == "Open" and not primary.get("linked_load_id")),
        "_row_count": len(rows),
        "_row_ids": [row.get("id") for row in rows],
    }
    return actual
```

Note this is provably unchanged for every existing single-row case: `rows` has exactly one element, so `order_numbers` degenerates to `[booking_number]` or `[]` (identical to today's literal), `containers`/`container_count` use the exact same per-row math as before, and `any(...)` over one element equals the old `bool(primary.get(...))`.

- [ ] **Step 2: Verify the harness changes compile and the existing suite is unaffected**

Run: `python -m compileall -q tests/integration/operations_inbox/harness.py`
Run: `pytest -q` (with `INBOX_CERTIFICATION_DATABASE_URL` unset)
Expected: no failures.

- [ ] **Step 3: Run CASE-010 against the real scratch database**

Run:
```bash
export INBOX_CERTIFICATION_DATABASE_URL="postgresql://calitrans_test:calitrans_test_pw_2026@localhost:5433/calitrans_inbox_cert"
python scripts/run_inbox_case.py CASE-010
```
Expected: inspect the printed field diffs and the freshly-written `tests/fixtures/operations_inbox/CASE-010/actual.json`. `expected.json` was written from the business requirement before any code existed (`order_numbers: ["APEX-260810", "APEX-260811"]`, `container_count: 2`, `containers: ["HLXU3000001", "HLXU3000002"]`, `customer: "Apex Retail"`, `queue: "New Orders"`, `decision: "Create New Order"`, `requires_human_review: true`) — if `RESULT: PASSED` already, skip to Step 5. If any field differs, read the diff carefully: a difference on `intent`/`service_flow`/`queue` most likely means the per-block triage/classification produced something other than "New Booking"/"Import"/"New Orders" for one of the blocks and needs investigation (not a silent `expected.json` edit) before proceeding.

- [ ] **Step 4: Reconcile any real diff**

If Step 3 did not pass cleanly, diagnose using the printed diff and `actual.json` before changing anything:
- A wrong `customer` almost always means the `_customer_from_prose` fallback (Task 2) isn't firing — check that no earlier fallback (signature-derived `Contact Company`, e.g. from the sender's `@example.com` domain) is claiming the field first.
- A wrong `order_numbers`/`containers` almost always means `detect_order_blocks` (Task 1) isn't slicing correctly for this exact fixture body, or `_prepare_operations_email_records` (Task 3) isn't passing the sliced text through — re-run the relevant unit test from Task 1/3 against the literal fixture body (`tests/fixtures/operations_inbox/CASE-010/email.txt`) to isolate which layer is wrong.
- Only update `expected.json` if, after investigation, the business requirement itself was ambiguous and the actual result is the more correct interpretation - document the reasoning in `verification.md`'s eventual acceptance audit either way.

- [ ] **Step 5: Re-run CASE-010 to confirm it passes**

Run:
```bash
python scripts/run_inbox_case.py CASE-010
```
Expected: `RESULT: PASSED`, `exact_record_pass: True`, `Duplicate-protection result: PASS` (row count stays at 2 after the rerun, not 1 and not 4).

Run it a second, fully independent time:
```bash
python scripts/run_inbox_case.py CASE-010
```
Expected: `RESULT: PASSED` again (determinism check).

- [ ] **Step 6: Write the permanent regression test**

Create `tests/integration/operations_inbox/test_case_010_multi_order_split.py`:

```python
"""Permanent regression test for CASE-010 - Two Separate Orders in One
Email. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a scratch
database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import run_case

CASE_ID = "CASE-010"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_010_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_010_creates_exactly_two_rows():
    report = run_case(CASE_ID)
    assert report.actual["_row_count"] == 2


def test_case_010_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run == 2


def test_case_010_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["order_numbers"] == second.actual["order_numbers"] == [
        "APEX-260810", "APEX-260811",
    ]


def test_case_010_preserves_both_bookings_and_both_containers():
    """Locks in the case's hard rule: neither order's Booking Number or
    Container Number may be dropped, and neither must bleed into the
    other's row."""
    report = run_case(CASE_ID)
    assert report.actual["order_numbers"] == ["APEX-260810", "APEX-260811"]
    assert report.actual["containers"] == ["HLXU3000001", "HLXU3000002"]
    assert report.actual["container_count"] == 2


def test_case_010_stays_in_new_orders_queue_not_review():
    """A clean split is a routine multi-order email, not a flagged
    problem - it must not be silently routed to the Review queue the way
    CASE-007's mismatch is."""
    report = run_case(CASE_ID)
    assert report.actual["queue"] == "New Orders"
    assert report.actual["decision"] == "Create New Order"
    assert report.actual["requires_human_review"] is True
```

- [ ] **Step 7: Run the targeted regression tests 3 times**

Run three times:
```bash
pytest -q tests/integration/operations_inbox/
```
Expected: all tests pass (including the 6 new CASE-010 tests) every time, same total count each run.

- [ ] **Step 8: Full suite and compileall**

Run:
```bash
unset INBOX_CERTIFICATION_DATABASE_URL
pytest -q
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core scripts tests
```
Expected: the pre-Task-1 baseline count plus the 18 new tests from Tasks 1-4 (`tests/test_order_block_splitting.py`), plus the certification suite's skip count increased by 6 for CASE-010's new regression tests, zero failures, clean compile.

- [ ] **Step 9: Rewrite `verification.md` as an acceptance audit**

Replace the "NOT ACCEPTED" content in `tests/fixtures/operations_inbox/CASE-010/verification.md` with an acceptance audit in the same format as `tests/fixtures/operations_inbox/CASE-007/verification.md` (field-by-field table, required-checks-from-the-case-spec section confirming each one now holds, database records showing 2 distinct rows with their `id`/`source_message_id`/`email_thread_id`/`conversation_key` values, regression test list, git diff summary listing the features added across Tasks 1-4, and a final `**ACCEPTED**` decision).

- [ ] **Step 10: Commit**

```bash
git add tests/integration/operations_inbox/harness.py \
        tests/fixtures/operations_inbox/CASE-010 \
        tests/integration/operations_inbox/test_case_010_multi_order_split.py
git commit -m "test: certify CASE-010 (two separate orders in one email)"
```

---

## Self-Review Notes

- **Spec coverage:** block detection with the 2-header trigger and 10-block cap (Task 1), customer prose fallback (Task 2), per-block record preparation reusing `_prepare_operations_email_record` unmodified (Task 3), row-identity assignment satisfying the unique index on `source_message_id` plus the shared `email_thread_id` link and the insert-loop refactor (Task 4), harness aggregation across rows plus the corrected (no Review-queue) surfacing plus fixture/regression test (Task 5) — every design-doc section has a task. The Review-queue-flagging correction from the design doc is reflected in Task 5's `capture_actual_result` (no new `decision` branch for the split case) and in the Global Constraints.
- **Placeholder scan:** no TBD/TODO; every step has literal code or an exact command with expected output. Step 4 of Task 5 is intentionally a diagnostic decision tree rather than fixed code, since it only runs if the live DB run surfaces something Tasks 1-4's unit tests didn't catch — the same shape CASE-007's plan used for its own live-run reconciliation step.
- **Type consistency:** `detect_order_blocks(body: str) -> list[str] | None` (Task 1) is consumed by `_prepare_operations_email_records` (Task 3) exactly as declared. `_prepare_operations_email_records(message: dict) -> list[dict]` (Task 3) is consumed by `_insert_operations_email_message` (Task 4). `_assign_split_row_identity(records: list[dict], base_message_id: str) -> list[dict]` (Task 4) matches its one call site in the same task. `_insert_operations_email_record_row(message: dict, record: dict) -> dict` preserves the exact same return shape `_insert_operations_email_message` returned before this plan, so its one call site (`sync_operations_email_engine`, unchanged apart from Task 4 Step 6) keeps working against `inserted.get("attachments_saved")` / `"llm_required"` / `"store_only"` / `"direction"` etc.
