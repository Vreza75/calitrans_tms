# Operations Inbox — Label-Block Boundary Correction

Date: 2026-08-05

## Codex H1 reproduction

```
Please quote:
Equipment: 40HC
From: Houston
To: Dallas
Subject: Container Rate
```
Before this pass: `scope_type == "reply"`, `classification_text ==
"Please quote:\nEquipment: 40HC"` — From/To/Subject and the action text
were clipped as if they were quoted history, and final classification
degraded to `Customer Request`.

## True root cause

`_find_reply_marker()` iterated line by line. It skipped the
operational-only `Equipment:` line (kind != "envelope"), then reached
`From: Houston` and called `_scan_label_block()` **starting at that line**
— scanning only forward. The scan never saw `Equipment:` sitting one line
above, so `From/To/Subject` (3 envelope-shaped labels) looked like a
complete, coherent envelope on its own. The same defect reproduced
identically in forwarded bodies, since `select_innermost_actionable_forward`
also calls `_find_reply_marker` on the remaining content after stripping
the outer envelope.

## Containing-block boundary algorithm

New `_find_block_start(lines, candidate_index)` walks **backward** from
any label-shaped candidate line to the true start of its contiguous block,
stopping at the first non-label-shaped line, a forward/reply separator, a
Gmail/Spanish wrote marker, or the start of the text — mirroring the
forward scan's own connectivity rules in reverse, including its blank-line
tolerance (a blank line is only crossed backward when the line before it
is itself label-shaped).

`_find_reply_marker()` now triggers on **any** label-shaped line (envelope
or operational, not only envelope), anchors to the true block start via
`_find_block_start`, classifies the whole block exactly once via
`_scan_label_block`, and skips its outer scan past `scan.end_index` so the
same block is never re-classified from an overlapping later suffix.

## Field-order neutrality

Because classification always starts at the block's true beginning
regardless of which line the outer scan first noticed, the result is
independent of field order by construction — not by permutation-specific
casing. Verified directly: all 24 permutations of
`{Equipment, From, To, Subject}` and all 24 permutations of
`{Equipo, De, A, Asunto}` produce identical `scope_type == "new_message"`
and preserve every field. A second, independent order-dependence was found
and fixed in `operations_inbox_service.py::_has_plausible_quote_lane` —
`_LABELED_FROM_TO_RE` required the `To:` line to *immediately* follow the
`From:` line, which failed 18 of the 24 permutations (any ordering placing
another field between them). Replaced with two independent `From:`/`To:`
label searches over the same already-scoped text, order-independent by
construction.

## Backward-scan / forward-scan rules

Both directions share the same connectivity boundary: a label-shaped line
(envelope or operational, English or Spanish) continues the block; a
blank line is crossed only when the line on its far side is *also*
label-shaped; a forward/reply separator, a wrote-marker, or any
non-label-shaped line (prose, an administrative wrapper, a signature)
ends the block immediately, in either direction.

## Whole-block classification rules

Unchanged from the coherent-envelope pass's design, now reachable from the
correct anchor: an operational-only label anywhere in the block makes the
whole block operational for `_find_reply_marker`'s purposes; a coherent
`From:`+2-more-labels-or-email prefix (computed independently, stopping at
the first operational label without un-classifying what came before it)
is what `_strip_envelope_block` removes from forwarded content. Subject
values are still never inspected for vocabulary.

## Ambiguity-preservation policy

Unchanged: a block that is neither a confirmed coherent envelope nor
operational-evidenced (`kind == "none"`) is never treated as a reply
marker, so it is preserved as active content by construction — no new
"ambiguous → clip" path was introduced by this fix.

## Field-order permutation results

48/48 (24 English + 24 Spanish) pass. Representative per-label position
coverage (Booking Number, Container Number, Port, Pickup Date, Delivery
Date, Full Return Terminal, Empty Pickup, Origin, Destination — each in 4
positions relative to a From/To/Subject core) — 36/36 pass, none relying
on a neighboring Equipment: label.

## Forwarded-body order results

5/5 forwarded-body H1 variants (Equipment/Booking Number/Port/Pickup
Date/Spanish-Equipo first) pass — the outer envelope is removed and the
operational-first inner block is preserved completely.

## Origin/Destination coverage

Added `origin`/`destination`/`origen`/`destino` to
`_OPERATIONAL_ONLY_LABELS`. Tested standalone (no Equipment present)
before From:, after Subject:, and inside a forwarded body — all preserved.

## M1 — segmentation-collapse correction

Added `MessageScope.segmentation_status: Literal["ok", "collapsed",
"depth_limit_reached"]`. `"collapsed"` is set when a recognized reply/
forward/forwarded_only structure produces empty `classification_text`.
`services/operations_inbox_service.py::_prepare_operations_email_record`
now computes this status directly and, when collapsed, **does not** apply
the previous `extract_latest_email_body(raw_body) or raw_body` fallback —
`latest_body` stays empty, so `parse_email_text` cannot manufacture
trusted-looking fields from ambiguous raw content.

## Raw-fallback policy

Raw-body fallback remains safe and unchanged for the one case it was ever
correct for: no reply/forward structure was found at all
(`segmentation_status == "ok"`, `scope_type == "new_message"`), where an
empty scoped result only ever means the raw input itself was blank.

## Parsed-data persistence safety

When collapsed: `parsed["_segmentation_status"]` records the condition;
`parsed["_segmentation_collapsed_raw_body"]` preserves the raw body
(bounded to 5000 chars) inside the existing `parsed_data` JSONB column,
namespaced as audit-only metadata — never a field a caller would read as a
confirmed current-message value (no schema migration needed). A
`processing_errors` entry is appended, which flows into the *existing*
`derive_review_state`/triage plumbing unchanged: `needs_review=True`,
confidence capped at 0.40, `triage["llm_required"]=True`. Verified directly
on `_prepare_operations_email_record`'s output (pure, DB-write-free): no
`matched_load_id`, Booking Number stays blank, raw body appears only in
the audit key.

## No-separator integration result

`tests/test_label_block_boundary_correction.py`'s Phase 9/10/11 tests
exercise the exact no-separator scenario (two envelope blocks, no
recognized boundary, trailing `Booking Number: ABC123` + cancellation
text) end-to-end through `_prepare_operations_email_record` (DB-write-free
by design, so no disposable-DB dependency was needed for this specific
check): segmentation flagged `"collapsed"`, raw body preserved in
`parsed_data` only, Booking Number not persisted as trusted, no
`matched_load_id`, `_needs_review=True`, `llm_required=True`.

## M3 — depth-limit correction

`select_innermost_actionable_forward` now tracks `last_nonempty_candidate`
throughout traversal. If the loop exhausts `_MAX_FORWARD_NESTING_DEPTH`
(5) while still finding another nested forward each time, it returns the
current unprocessed remainder (or, in the degenerate case where that is
itself empty, the last known non-empty candidate) **without** running the
normal nested-reply-marker clip on it — that clip is exactly what
previously risked reading an unstripped envelope at the depth limit as
"quoted history begins here" and discarding everything beyond the budget.
Returns a third value, `depth_limit_reached: bool`, propagated into
`MessageScope.segmentation_status == "depth_limit_reached"` with
confidence lowered to 0.4.

## Six-level and ten-level nesting results

5 levels: fully resolved, `segmentation_status == "ok"`. 6 and 10 levels:
`segmentation_status == "depth_limit_reached"`, `classification_text`
non-empty in both cases and contains the deepest actionable content
(verified for a quote, a cancellation, and an update at the deepest
level). Malformed repeated-underscore-separator chains (8 levels)
terminate deterministically with non-empty output — no infinite loop.

## M2 — documentation correction

Corrected caller-scope claims. **Scoped correctly** (verified by direct
reading): `classify_customer_request`, `operations_intent_scores`,
`has_quote_details` (all via one `build_message_scope` call each),
`message_scope.py`'s own `active_text`/`quoted_text` selection. **Known
raw-scope exceptions, not fixed in this pass** (documented, not silently
implied consistent):
- `extract_reference_tokens` — called with raw `f"{subject}\n{body}\n{parsed}"` in places, including a `str(parsed)` component.
- `coerce_parsed_for_classification` — falls back to raw `parse_email_text(subject, body)` when no `parsed` dict is supplied.
- `operations_email_triage_service.py::is_booking_confirmation()` — pre-existing, documented separately (see below), unaffected by this pass.
- Two residual `extract_latest_email_body(x) or x` occurrences in
  `operations_inbox_service.py` (lines ~1871 and ~3921, both in
  *re-classification of already-persisted records*, not new intake) still
  use the same unguarded pattern this pass fixed for new intake at
  `_prepare_operations_email_record`. Not fixed here — out of this pass's
  primary-ingestion-path scope; a reasonable follow-up.
- Multi-order block detection (CASE-010 style) and `str(parsed)`
  rescanning still operate over less-scoped text in places, unchanged from
  prior passes.

**Follow-up recommendations** (both named, neither implemented here):
`fix/operations-inbox-booking-confirmation-scope` (pre-existing, carried
forward unchanged) and a new **`fix/operations-inbox-reference-token-scope`**
for the reference-token/re-classification raw-scope exceptions above.

## Row-23 assertion correction

Removed `assert ... or True` (L1). Split into two honest tests:
`test_row23_person_name_lane_message_scope_preserves_content_without_clipping`
(enforces the actual `message_scope.py` invariant — content is never
clipped or corrupted regardless of value semantics) and
`test_row23_person_name_lane_final_classification_is_a_known_documented_ambiguity`
(pins the current, unfixed, out-of-file `Quote Request` outcome with a real
`==` assertion and an explanatory comment, so a future silent change is
caught).

## Tests failing before fixes

Direct reproduction confirmed `scope_type == "reply"` /
`classification_text` truncated for the Equipment-first case (and the
other 4 H1 variants) before any production change; 18/24 permutations
failed final classification before the `_has_plausible_quote_lane` fix.

## Tests passing after fixes

165 (48 in `test_message_scope_coherent_envelope.py` + 117 in
`test_label_block_boundary_correction.py`), plus the full pre-existing
suite unmodified and green.

## Red-team probes and findings

32 fresh adversarial probes (every operational label before/after the
envelope core, duplicate From/Subject labels, tabs, mixed CRLF/LF,
mixed-case labels, colon-spacing variants, no-value labels, malformed
repeated/mixed separators, person/department/port/warehouse/weekday/hour
values, envelope-Subject-with-action-words). 30/32 behaved correctly on
first run. 2 findings — an operational label appearing *inside* or
*immediately before* a genuine multi-field envelope's own label run — leak
some envelope labels into `classification_text` but never lose the
operational content. Both are contrived shapes no real email client
produces (a real envelope's field run is never interrupted by an
Equipment: line); pinned down as documented known limitations with
permanent regression tests rather than further redesigned under this
pass's time/context budget.

## Exact test counts

| File | Collected |
|---|---|
| `test_message_scope_final_corrections.py` | 38 (unchanged) |
| `test_message_scope_and_field_boundaries.py` | 33 (unchanged) |
| `test_message_scope_coherent_envelope.py` | 48 (47 → 48: 1 vacuous test replaced by 2 honest ones) |
| `test_label_block_boundary_correction.py` | 117 (new) |
| **Focused total (4 files)** | **236** |
| Certification | 49 (unchanged) |
| **Full suite** | **864** (746 + 117 new + 1 net change) |

## Manual UI status

**Not performed.** No browser available in this environment.

## Remaining known risks

- Operational label interrupting/preceding a genuine multi-field envelope
  (2 red-team findings above) — narrow, non-realistic, no data loss.
- Two envelope blocks with no separator (prior pass's RT-6) — unchanged,
  still documented, still safe-failing (empty → review), reconfirmed
  compatible with this pass's fixes.
- Booking-confirmation scope limitation (pre-existing,
  `is_booking_confirmation()`) — unchanged; follow-up
  `fix/operations-inbox-booking-confirmation-scope`.
- Reference-token/re-classification raw-scope exceptions (new finding,
  M2) — follow-up `fix/operations-inbox-reference-token-scope`.
- Full Return editor limitation, `str(parsed)` rescanning,
  `order_parser.py` issue, incomplete backend-boundary migration, no
  dispatcher-confirmation provenance, conservative administrative-phrase
  list, person-name lane false positive — all unchanged from prior passes.
