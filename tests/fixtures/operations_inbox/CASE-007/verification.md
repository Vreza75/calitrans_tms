# Operations Inbox Case Acceptance Audit — CASE-007

Container Quantity Mismatch.

## Finding: required capability does not exist

`expected.json` was written from the case spec's business requirement
(what a correct implementation must do), before running anything, per the
hard rule "Expected output was defined before code changes."

Actual pipeline behavior does not meet it, and the gap is not a small
bug fix like CASE-001..006 — it requires two capabilities that do not
exist anywhere in the current codebase:

1. **A list of container numbers from free text.** `services/email_parser.py`
   only ever extracts a *single* container number
   (`_first_container()` -> `parsed["Container Number"]`). There is no
   function anywhere in `services/` or `ai_agents/` that returns multiple
   container numbers from one message.
2. **Quantity-vs-detected-count mismatch detection and blocking.** No
   code compares a stated quantity ("Total quantity: 4 containers")
   against how many container numbers were actually found, and no
   decision path routes such a mismatch to a blocked/needs-review state
   distinct from a normal new booking.

## Expected vs actual

| Field | Expected (business requirement) | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Import | Import | yes |
| queue | New Orders | New Orders | yes |
| **decision** | **Human Review Required** | **Create New Order** | **NO** |
| booking_number | QTY-260807 | QTY-260807 | yes |
| **container_count** | **4** | **1** | **NO** |
| **containers** | **[TEMU2000001, TEMU2000002, TEMU2000003]** | **[TEMU2000001]** | **NO** |
| customer | Summit Furniture Imports | Summit Furniture Imports | yes |
| requires_human_review | true | true | yes (coincidental - true only because no order has been approved yet, not because a mismatch was detected or displayed) |

- Critical-field accuracy: 71.4% (5 of 7)
- Overall field accuracy: 75%
- Exact-record result: **FAIL**
- Container-count accuracy: 0%
- Container-number accuracy: 0%

## Required checks (from the case spec) — none met

- Do not invent the fourth container number: technically true, but only
  because the system doesn't attempt to reconcile quantity vs. count at
  all - it silently keeps whatever `_first_container()` happened to grab
  and drops the other two listed numbers entirely (`TEMU2000002`,
  `TEMU2000003` are nowhere in `parsed_data`).
- Do not create only three containers without warning: the actual gap is
  worse - it silently proceeds as if there were **one** container, not
  three, and issues no warning of any kind.
- Display the quantity mismatch clearly: not implemented - `decision`
  reads `"Create New Order"`, identical to a normal single-container case
  like CASE-001.
- Preserve all three valid container numbers: **fails** - only the first
  survives in `parsed_data`; the other two are discarded with no error,
  no note, and no `Dispatcher Notes` mention.
- Request confirmation or manual correction: not implemented - nothing
  distinguishes this record from a clean single-container booking in the
  Inbox queue today.

## Reruns performed anyway (infrastructure still verified)

- Duplicate rerun: row count unchanged (1 before, 1 after) - the *insert*
  path is still idempotent even though the *content* is wrong.
- Two independent CLI runs: deterministic (same wrong output both times) -
  the failure is a real, repeatable gap, not flakiness.

## No regression test added

Per the acceptance rules, a case may not be marked Passed without a
permanent regression test - and there is nothing correct to lock in here.
Adding a test asserting today's `container_count: 1` would encode a known
defect as "expected," which is exactly what this framework exists to
prevent. No regression test is committed for this case.

## What would be required to pass (not done - out of scope for this task)

1. A container-number-list extractor (multiple `[A-Z]{4}\d{7}` matches
   from one message, not just the first).
2. A stated-quantity extractor recognizing free-text phrasing like
   `"Total quantity: N containers"` (today's `Container Qty` aliases -
   `Number Of Cntrs`/`Container Qty`/etc. - don't match this wording
   either, a second, smaller gap in the same case).
3. A comparison step: if declared quantity != detected container count,
   set `decision = "Human Review Required"`, block automatic order
   creation, and surface the mismatch (e.g. in `action_required` or a
   dedicated queue/status) instead of silently defaulting to whichever
   count the weak fallback found.

This is a real feature, not a bug fix - out of scope for "test the
functions already implemented"; flagging for a separate scoping/planning
conversation rather than building it here.

## Decision

**NOT ACCEPTED**

Blocked on missing capability (multi-container-number extraction +
quantity-mismatch detection/blocking), not on a defect fixable within
this session's scope. Fixture, `expected.json`, and this audit are kept
as the reference case for whenever that capability is planned and built.
