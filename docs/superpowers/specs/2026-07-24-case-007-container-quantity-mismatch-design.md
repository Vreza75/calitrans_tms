# CASE-007: Container Quantity Mismatch Detection

## Context

Operations Inbox certification (`docs/operations_inbox_certification/`)
found CASE-007 cannot pass today, and it's a missing capability, not a
bug: `tests/fixtures/operations_inbox/CASE-007/verification.md` documents
that a booking email declaring "4 containers" but listing only 3 physical
container numbers is silently processed as if there were 1 container
(`_first_container()` keeps only the first match), with no warning and no
block. See that file and `tests/fixtures/operations_inbox/CASE-007/email.txt`
for the exact reference fixture.

Verified by reading the code:

- `services/email_parser.py`'s `parse_email_text()` only ever extracts a
  single `Container Number` via `_first_container()` - no function
  anywhere returns a list of container numbers from one message.
- `Container Qty` (`LABEL_ALIASES["Container Qty"]`) is label-driven only
  (`"Number Of Cntrs"`, `"Container Qty"`, etc.) - a full sentence like
  `"Total quantity: 4 containers."` matches none of them.
- CASE-006 (`RICGX1235800`, quantity 4, zero container numbers given yet)
  is an already-ACCEPTED, correct case: `container_qty` stated with no
  numbers is the *normal* pre-assignment state, not a mismatch. Any
  mismatch rule must not flag that case.
- `services/operations_email_triage_service.py`'s
  `enforce_authoritative_booking_triage()` /
  `_request_type_from_rules()` already implement a "correction pass"
  shape - a function that runs after triage and overrides specific
  fields when a condition is met (see
  `docs/CODE_REVIEW_PLAYBOOK.md` §38 for a full account of that
  precedence chain and its pitfalls). This design reuses that shape
  rather than inventing a new one.
- `services/operations_inbox_service.py`'s `operations_work_queue_for_row()`
  already has a `"Review"` queue value
  (`if request_type in {"Needs Classification","Other",""} or confidence<50:
  return "Review"`) - no new queue/status enum is needed.
- Nothing in this codebase hard-blocks `create_load_from_inbox_item()` for
  any reason today; every "human review required" case relies on queue
  routing + `llm_review_required`, not a function-level guard.

## Decisions (from brainstorming)

- **Quantity detection**: keep the existing label-based `Container Qty`
  field as the primary source. Add sentence-pattern fallback regexes
  (`"total quantity: N containers"`, `"N containers total"`,
  `"quantity: N"`) used only when no label matched. Digits only - spelled-
  out numbers ("four") are an explicit non-goal.
- **Container number list**: add a new `"Container Numbers"` list field
  (all `[A-Z]{4}\d{7}` matches, deduped, in order). The existing singular
  `"Container Number"` field is untouched - it keeps holding the first
  match, so nothing that already reads it breaks.
- **Mismatch rule**: `found == 0` → normal, not-yet-assigned (CASE-006).
  `found == declared` → fully specified, normal. `0 < found < declared`
  **or** `found > declared` → mismatch, needs review.
- **Surfacing**: reuse existing fields only - `llm_review_required=true`,
  `work_queue="Review"`, and a specific `action_required` message
  (e.g. `"Quantity mismatch: 4 declared, 3 container numbers found -
  confirm before creating order."`). No new schema/enum.
- **Enforcement**: flagging only. No hard block added to
  `create_load_from_inbox_item()` - matches how every other
  human-review-required case already works in this codebase.

## Design

### 1. Parser additions (`services/email_parser.py`)

```python
def _container_qty_from_sentence(text: str) -> str:
    """Fallback quantity extraction for free-text phrasing that isn't a
    labeled field, e.g. "Total quantity: 4 containers." Only consulted
    when the label-based Container Qty lookup found nothing."""
    patterns = [
        r"\btotal\s+quantity\s*:?\s*(\d{1,2})\s+containers?\b",
        r"\b(\d{1,2})\s+containers?\s+total\b",
        r"\bquantity\s*:?\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


def _all_container_numbers(text: str) -> list[str]:
    """Every distinct container-number-shaped token in the message, in
    the order first seen. Unlike _first_container(), does not stop at
    the first match - this is what lets a mismatch between a stated
    quantity and the actual listed numbers be detected at all."""
    seen: dict[str, None] = {}
    for match in re.finditer(r"\b[A-Z]{4}\d{7}\b", text.upper()):
        seen.setdefault(match.group(0), None)
    return list(seen.keys())
```

Wire into `parse_email_text()`:

- After the existing `Container Qty` alias lookup: `if not parsed["Container
  Qty"]: parsed["Container Qty"] = _container_qty_from_sentence(combined)`.
- New field, computed unconditionally (cheap, always safe to compute):
  `parsed["Container Numbers"] = _all_container_numbers(combined)`. Add
  `"Container Numbers"` to `FIELDS` so it's always present in the parsed
  dict (defaults to `[]`, not `""`, since `FIELDS` init as `{field: "" for
  field in FIELDS}` - handle this one field specially right after that
  init line).

### 2. Mismatch detection (`services/email_parser.py`)

```python
def detect_container_quantity_mismatch(parsed: dict) -> dict | None:
    """Pure function, no DB/IO. Returns None when there's no mismatch to
    flag - a stated quantity with zero container numbers yet (the normal
    pre-assignment state, e.g. RICGX1235800/CASE-006) is NOT a mismatch."""
    try:
        declared = int(str(parsed.get("Container Qty") or "").strip())
    except ValueError:
        return None
    if declared <= 0:
        return None

    found_numbers = parsed.get("Container Numbers") or []
    found = len(found_numbers)

    if found == 0 or found == declared:
        return None

    return {
        "declared": declared,
        "found": found,
        "message": (
            f"Quantity mismatch: {declared} declared, {found} container "
            f"number{'s' if found != 1 else ''} found - confirm before "
            f"creating order."
        ),
    }
```

### 3. Correction pass (`services/operations_inbox_service.py`)

```python
def enforce_container_quantity_mismatch_review(parsed: dict, triage: dict | None) -> dict:
    """Same shape as enforce_authoritative_booking_triage(): runs after
    triage and overrides specific fields when a condition is met."""
    corrected = dict(triage or {})
    mismatch = detect_container_quantity_mismatch(parsed)
    if mismatch is None:
        return corrected

    corrected.update({
        "llm_review_required": True,
        "work_queue": "Review",
        "action_required": mismatch["message"],
        "triage_reason": mismatch["message"],
    })
    return corrected
```

Wire into `_prepare_operations_email_record()`, immediately after the
existing `enforce_authoritative_booking_triage()` call - same pattern,
one more correction pass in the chain.

### 4. Harness updates (`tests/integration/operations_inbox/harness.py`)

- `capture_actual_result()`: `containers` prefers
  `parsed.get("Container Numbers")` when non-empty, falling back to the
  existing singular-field logic (`[container_number] if container_number
  else []`) - CASE-000 through CASE-006/008/009's expected output is
  single-element lists, byte-identical to the current fallback, so this
  is additive.
- `decision` gains a third branch, checked before the existing
  `matched_load_id` check: if `detect_container_quantity_mismatch`-style
  evidence is present (i.e. `llm_review_required` is true **and**
  `action_required` contains `"mismatch"`), set `decision = "Human Review
  Required"`. Otherwise unchanged (`"Update Existing Order"` /
  `"Create New Order"`).

### 5. CASE-007 fixture updates

- Re-run against the real scratch DB once the above lands; update
  `tests/fixtures/operations_inbox/CASE-007/expected.json` to the
  now-correct values (`container_count: 4`, `containers:
  [TEMU2000001, TEMU2000002, TEMU2000003]`, `decision: "Human Review
  Required"`).
- Add the permanent regression test file (mirrors CASE-001..009's
  pattern): passes-clean, rerun-no-duplicates, deterministic-across-runs,
  plus a dedicated `test_case_007_...` asserting the mismatch is
  detected and none of the 3 valid container numbers are dropped.
- Delete the `NOT ACCEPTED` framing from `verification.md`, replace with
  the acceptance audit once it passes.

## Non-goals (explicit)

- Spelled-out quantities ("four containers").
- Any change to `create_load_from_inbox_item()` - no hard block.
- Any UI change in `pages_app/operations_inbox.py`.
- Fixing `classify_customer_request()`'s own separate booking-confirmation
  short-circuit (see `docs/CODE_REVIEW_PLAYBOOK.md` §38) - out of scope
  for this case; the mismatch correction pass runs after triage
  regardless of what the earlier classification step decided.

## Testing

- Pure-function unit tests (no DB), added to
  `tests/test_operations_container_qty_confirmation.py` or a new
  `tests/test_container_quantity_mismatch.py`:
  - RICGX1235800-style: qty 4, 0 found → `None` (no mismatch).
  - CASE-007: qty 4, 3 found → mismatch dict with `declared=4, found=3`.
  - Fully specified: qty 4, 4 found → `None`.
  - Over-supplied: qty 3, 4 found → mismatch dict.
  - No stated quantity at all → `None` (nothing to compare against).
- `python -m compileall` + full suite (`pytest -q`) after each change,
  same as every prior certification case in this session - zero
  regressions is the bar, not "no new failures I notice."
- Live run: `python scripts/run_inbox_case.py CASE-007` against the
  scratch DB, twice independently, plus the standard 3x targeted +
  duplicate-rerun protocol before marking ACCEPTED.
