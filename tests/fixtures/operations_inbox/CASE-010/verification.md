# Operations Inbox Case Acceptance Audit — CASE-010

Two Separate Orders in One Email.

## Finding: required capability does not exist

`expected.json` was written from the case spec's business requirement
before running anything, per the hard rule "Expected output was defined
before code changes."

Actual behavior does not meet it, and like CASE-007, the gap is a missing
capability, not a small bug: **the automated intake pipeline
(`_insert_operations_email_message`) always creates exactly one
`order_intake` row per email message.** There is no code anywhere that
detects "this message actually contains N separate bookings" and splits
it into multiple drafts/rows - `parse_email_text` (and every specialized
parser checked in earlier cases) extracts a single Customer, a single
Booking Number, a single Container Number, etc., always taking whichever
one it finds first.

## Expected vs actual

| Field | Expected (business requirement) | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Import | Import | yes |
| **order_numbers** | **[APEX-260810, APEX-260811]** | **[APEX-260810]** | **NO** |
| **container_count** | **2** | **1** | **NO** |
| **containers** | **[HLXU3000001, HLXU3000002]** | **[HLXU3000001]** | **NO** |
| customer | Apex Retail | "Example" (sender-domain fallback - no `Customer:` label in this fixture either) | NO |
| pickup/delivery/dates/references (Order 1 fields) | none expected here (belongs to the split-out Order 1 draft) | populated with Order 1's own fields | N/A - see below |

- Critical-field accuracy: 25% (1 of 4 critical fields correct - only
  `intent`)
- Exact-record result: **FAIL**
- Container-count accuracy: 0%
- Container-number accuracy: 0%

## Required checks (from the case spec) — none met

- Correctly identify two booking numbers: **fails** - only
  `APEX-260810` survives; `APEX-260811` is nowhere in `parsed_data`.
- Create two distinct drafts: **fails** - exactly one `order_intake` row is
  ever created, by design of the current pipeline.
- Associate each container with the correct booking: **fails** - there is
  only one container in the output at all.
- Preserve shared customer information: **fails** for a different, more
  basic reason - `Customer:` isn't explicitly labeled in this email at all
  (the subject/intro sentence says "Apex Retail" in prose, which the
  current label-based parser never reads), so this would also need a
  Customer alias/prose-extraction fix even before order-splitting is
  addressed.
- Create two orders after approval / no duplicates on rerun: not
  reachable - there's only one order to begin with.

## Reruns performed anyway (infrastructure still verified)

- Duplicate rerun: row count unchanged (1 before, 1 after) - the *insert*
  path is idempotent even though its content silently drops Order 2.
- Independent CLI run: deterministic (same wrong output) - a repeatable
  gap, not flakiness.

## No regression test added

Same reasoning as CASE-007: there is no correct behavior to lock in yet.
Asserting today's single-order output as "expected" would encode a known
defect as correct, which this framework exists to prevent.

## What would be required to pass (not done - out of scope for this task)

1. A detector for "this message contains multiple distinct
   booking/order blocks" (e.g. repeated `Booking Number:` labels, or an
   explicit `Order 1` / `Order 2` structure like this fixture's).
2. A loop that creates one `order_intake` row (or one booking-level draft,
   consistent with CASE-006's multi-container draft model) per detected
   order block, all linked to the same source email/thread.
3. Per-block field scoping so Order 2's `Terminal`/`Address`/`Delivery
   Need Date` don't bleed into Order 1's fields (the current single-pass
   label parser would need to operate per-block, not on the whole body at
   once).
4. A `Customer` alias/heuristic that reads a company name stated in prose
   ("...for Apex Retail") when no `Customer:` label exists - a smaller,
   separate gap that would also affect this case even after splitting is
   built.

This is a real feature (order-splitting), not a bug fix - out of scope
for "test the functions already implemented"; flagging for a separate
scoping/planning conversation.

## Decision

**NOT ACCEPTED**

Blocked on missing capability (multi-order detection and splitting within
one email), not on a defect fixable within this session's scope. Fixture,
`expected.json`, and this audit are kept as the reference case for
whenever that capability is planned and built.
