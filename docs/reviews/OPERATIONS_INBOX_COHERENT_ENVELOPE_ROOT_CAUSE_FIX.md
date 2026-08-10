# Operations Inbox — Coherent-Envelope Root-Cause Fix

Date: 2026-08-05

> **Correction (2026-08-05, label-block boundary pass):** a fourth review
> (Codex H1) found the block scanner itself could still start midway
> through a contiguous operational block: when an operational-only label
> (Equipment/Booking Number/Port/Pickup Date/...) appeared *before* a
> From:/De: line in the same block, `_find_reply_marker`'s forward-only
> scan began at the From: line and saw only the trailing envelope-shaped
> suffix, misreading it as a coherent reply header and clipping the
> operational-label-first content. This affected both top-level messages
> and forwarded bodies. See
> `docs/reviews/OPERATIONS_INBOX_LABEL_BLOCK_BOUNDARY_CORRECTION.md` for
> the backward-anchoring fix, plus two further findings (M1: a
> segmentation collapse could silently fall back to parsing the raw body;
> M3: the nested-forward depth limit could return empty text). This
> document's claims are left as originally written; read them as
> superseded wherever the two disagree.

> **Further correction (2026-08-06):** a blank-line variant of the same
> class of defect, a real audit-content quarantine gap (collapsed raw
> body re-entering classification/tokens via `str(parsed)`/`f"...{parsed}"`
> blobs at ~10 call sites), and a field-order-independent quote-lane
> defect were found and fixed in
> `fix/operations-inbox-segmentation-quarantine-final`. See
> `docs/reviews/OPERATIONS_INBOX_SEGMENTATION_QUARANTINE_FINAL.md`.

## Why this pass exists

Three prior correction cycles on `services/message_scope.py` each fixed one
observed email shape by adding an isolated per-line signal check, and each
time a structurally adjacent shape reproduced the same underlying defect in
a different guise:

1. A bare `Date:` label next to an operational lane was misdetected as
   reply history → fixed with an operational-proximity veto.
2. Forwarded-header stripping only removed the first line → fixed with
   coherent-block consumption.
3. The veto over-fired when an envelope had no email address and `Sent:`/
   `Cc:`/`Bcc:` was also absent, but domain vocabulary appeared in the
   Subject value → fixed with an envelope-only-label escape hatch keyed on
   `Sent:`/`Cc:`/`Bcc:` presence.
4. **This pass's starting defect**: a `From:`/`To:`/`Subject:` envelope
   with **none** of `Sent:`/`Cc:`/`Bcc:`/email present at all (Defect A)
   still leaked in full, because nothing in the prior design recognized a
   coherent 3-label envelope as an envelope in its own right — it only
   ever recognized specific *signals* (an email address, a `Sent:` label,
   a `Cc:`/`Bcc:` label), never the general *structural* pattern.

Each cycle patched a symptom instead of the underlying model. This pass
replaces the model itself.

## Root-cause analysis

The previous design (`is_reply_header_line()` + line-by-line
`_forwarded_active_content()`) evaluated each candidate line using an
**8-line look-ahead window** containing a mix of header lines and whatever
body content happened to follow, and decided "is this a header?" from a
sequence of independent signal checks (email address? `Sent:`/`Subject:`/
`Date:` label? operational vocabulary anywhere in the window? 3+ label
hits?) evaluated in a fixed priority order. This has two structural
problems:

1. **No concept of a block.** Each line was evaluated (or re-evaluated,
   with a shrinking window) independently, so evidence that should belong
   to "the whole envelope" (e.g. "this run has a `Sent:` label") could be
   lost or double-counted depending on which line was currently being
   inspected.
2. **No separation between a label's *presence* and its *value's
   content*.** `_OPERATIONAL_PROXIMITY_RE` searched the raw window text,
   which included `Subject: Quote Request`'s *value* — so a Subject line's
   own wording was read as evidence about the block's nature, when a
   Subject value is metadata *describing* the envelope, never part of the
   active body.

The fix: define envelope-vs-operational as a **structural property of a
contiguous block of labels**, computed once per block, never per line, and
never by inspecting a label's value for keyword content — only by which
*labels* are present.

## Previous heuristic limitations (superseded)

| Prior mechanism | Limitation |
|---|---|
| `has_sent_subject_date or has_email_value → True` | Any one signal was independently sufficient — a coherent 3-label block with none of these specific signals was invisible to the design |
| `_OPERATIONAL_PROXIMITY_RE.search(window)` | Searched raw window text including label *values*, not just labels — Subject's own wording counted as operational evidence |
| `has_envelope_only_label` (Sent/Cc/Bcc only) | A targeted patch for the *previous* verification pass's specific failing input, not a general envelope signal — a `Subject:`-and-nothing-else envelope was still unrecognized |
| Line-by-line cursor with shrinking window | Lost the `From:` line from its own look-ahead the moment it was consumed — literally what caused Defect 2 in the prior pass |

## New block-level design

`services/message_scope.py` now has:

- **`_classify_label_line(line)`** — classifies a *single line's label*
  (never its value) as `"envelope"`, `"operational"`, or `None`.
- **`_scan_label_block(lines, start_index)`** — scans the maximal
  contiguous run of label-shaped lines (blank-line tolerant, per the rule
  below) as one unit, returning both:
  - `kind`: `"operational"` if *any* operational-only label appears
    anywhere in the run; else `"envelope"` if a `From:`/`De:` label plus
    2+ other envelope-shaped labels (or an email address) are present;
    else `"none"`. Used by `_find_reply_marker` — reply-marker rejection
    must veto on *any* operational signal anywhere, to protect real
    content from being clipped as history.
  - `envelope_prefix_end` / `envelope_prefix_is_coherent`: the envelope
    block ends at the first operational-only label *without*
    retroactively un-classifying the coherent prefix before it. Used by
    `_strip_envelope_block` — stripping a forwarded envelope should strip
    exactly the envelope, even when an operational field is glued directly
    onto the end of it with no blank line.
- **`select_innermost_actionable_forward(raw_text)`** — replaces
  single-level `_forwarded_active_content()`; descends through directly
  nested forward wrappers (bounded by `_MAX_FORWARD_NESTING_DEPTH = 5`)
  until reaching actionable content.
- **`_is_administrative_line(line)`** — normalizes unicode dashes/repeated
  punctuation, matches a zero-intent phrase pattern, and independently
  vetoes on an explicit operational-action-term list.

## Envelope rules

A block is a coherent **email envelope** when it has a `From:`/`De:` label
and either an email address in any label's value, or 2 or more *other*
envelope-shaped labels (`Sent`/`To`/`Subject`/`Date`/`Cc`/`Bcc`, or
`Enviado`/`Para`/`Asunto`/`Fecha`) — 3 total labels including `From`. A
bare `From:`+`To:` pair (2 labels, no email) is **not** enough — this is
the original defect this module exists to fix, now a direct structural
consequence of the 3-label/email threshold rather than a special case. An
operational-only label anywhere in the block always wins over however many
envelope-shaped labels are present. A second `From:`/`De:` line ends the
block without being consumed — a genuine envelope is never repeated.

## Operational-block rules

A block is **operational** the moment it contains any of: `Equipment`,
`Equipo`, `Container Number(s)`, `Booking Number`, `Pickup Date`,
`Delivery Date`, `Port`, `Terminal`, `Full Return`/`Full Return Terminal`,
`Empty Return`, `Empty Pickup`, `Reference Number`, `LFD`, `Cutoff` (plus
Spanish equivalents) — matched only against a line's own label, never
against another label's value. This directly implements Phase 6's
requirement to separate `envelope_subject_value` from `active_body_text`.

## Nested-forward rules

`select_innermost_actionable_forward` strips the envelope after each
forward separator, then checks whether the very next non-blank line is
*itself* another forward separator with nothing actionable in between — if
so, it descends into the nested forward instead of stopping on a bare
envelope; otherwise it stops, having found actionable content. Bounded at
5 iterations (documented in-code) against malformed/adversarial input; no
real email chain nests this deep.

## Administrative-wrapper rules

A wrapper phrase (after dash/whitespace/punctuation normalization) is
administrative-only only when it (a) matches a recognized zero-intent
pattern (`fwd`, `see attached[ below]`, `fyi[ ,-]?[ ]?thanks`, `favor
atender`, `para su atención`, ...) **and** (b) contains no operational
action term (`cancel`/`update`/`change`/`create`/`book`/`quote`/`rate`/
`deliver`/`pickup`/`return`/`move`/`revise`/`hold`/`release` + Spanish
equivalents). Both conditions are independently checked — condition (b) is
a deliberate second safeguard on top of the strict full-line-match
requirement that already rejected most actionable extensions implicitly.

## Caller audit (Phase 9)

`classify_customer_request` and `operations_intent_scores`
(`operations_inbox_service.py:2630-2696`) each build their own
`scope = build_message_scope(body)` and use `scope.classification_text`
consistently — `has_quote_details` receives that same scoped text as a
parameter, never re-deriving it. `email_parser.py`'s
`_clip_quoted_thread`/`extract_quoted_email_history` use `scope.active_text`
and `scope.quoted_text or scope.forwarded_text` respectively — safe, since
`active_text` and `classification_text` are identical by construction in
every `scope_type` branch (verified by reading `build_message_scope`, not
assumed). No caller reconstructs or overrides `classification_text`, and
no caller mixes raw body text with scoped details in the same decision,
**except** the pre-existing, out-of-diff
`operations_email_triage_service.py::is_booking_confirmation()` (documented
separately below, not touched — the redesign did not require touching it).

## Tests failing before correction (RED)

10 of 41 initial decision-table/exact tests failed against the pre-refactor
`services/message_scope.py`:
```
row3_from_to_subject_without_sent
row4_from_to_subject_container_delivery_no_sent
row5_from_to_subject_rate_request_no_sent
row16_envelope_without_sent
row20_two_nested_forwarded_messages
test_no_sent_envelope_fully_stripped
test_second_operational_from_survives_no_sent_envelope
test_nested_forward_preserves_innermost_actionable_request_not_a_separator
test_administrative_wrapper_positive_is_forwarded_only[See attached below]
test_administrative_wrapper_positive_is_forwarded_only[FYI — thanks]
```
Exactly reproduces Defect A, Defect B, Defect D, and 2 of the 8
administrative-wrapper positive requirements.

## Tests passing after correction (GREEN)

All 118 tests across `test_message_scope_final_corrections.py` (38),
`test_message_scope_and_field_boundaries.py` (33), and
`test_message_scope_coherent_envelope.py` (47, this pass) pass. The
initial block-level implementation itself needed two further corrections
discovered by red-team probing before all 118 were green (see below) — the
suite was not declared complete after the first run turning green.

## Red-team probes performed (Phase 10)

24 fresh adversarial probes not copied from any implementation test were
run via an uncommitted script (random combinations of From/To/Subject/
Sent/Date/Equipment/Booking, blank lines, missing labels, alternate order,
Spanish labels, 2-3 nested forwards, operational vocabulary in Subject vs.
body only, person/location/department names in envelope values). Two
produced genuinely wrong output and were converted into permanent
regression tests with the general rule corrected:

1. **An operational-only label glued directly onto an envelope's own label
   run with no blank line** (`"Subject: Port Update\nPort: Bayport
   Container Terminal"`) flipped the *whole* block — envelope included —
   to "operational" and stripped nothing, violating Invariant 1. Fixed by
   splitting `_scan_label_block`'s result into a whole-block `kind` (for
   reply-marker rejection) and a separate envelope-prefix result (for
   envelope stripping) — see "New block-level design" above.
2. **Blank-line tolerance bridging into unrelated body content** that
   happened to also be label-shaped (`"...Subject: New Booking\n\nBooking
   Number: ABC123..."`) folded the body's `Booking Number:` line into the
   same block as the envelope via the blank-line-skip path, producing the
   same whole-block misclassification. Fixed by requiring the line
   immediately after a blank-line gap to itself be envelope-shaped (not
   merely label-shaped) for the gap to be tolerated.

One further probe (two coherent envelope blocks concatenated with **no**
forward separator, reply marker, or any transition between them) produced
an empty `classification_text` — this is not a realistic email-client
output (every real client inserts some boundary between distinct messages)
and is not required by any decision-table row; it is documented as a known
limitation with a regression test pinning down the current (safe-failing,
not silently-wrong) behavior rather than fixed. See "Remaining known
risks."

22 of the 24 probes produced correct output on the first pass; the results
of all 24 are reflected in the regression matrix below and in
`tests/test_message_scope_coherent_envelope.py`'s Phase-10 section.

## Regression matrix

| # | Scenario | Expected scope | Expected active content | Expected classification | Result |
|---|---|---|---|---|---|
| 1 | From/Sent/To/Subject + email | reply | top-level only | — | PASS |
| 2 | From/Sent/To/Subject, no email | reply | top-level only | — | PASS |
| 3 | From/To/Subject, no Sent | reply | top-level only | — | PASS |
| 4 | Fwd: From/To/Subject: Container Delivery, no Sent | forwarded_only | forwarded body only | — | PASS |
| 5 | Fwd: From/To/Subject: Rate Request, no Sent | forwarded_only | forwarded body only | Quote Request | PASS |
| 6 | From/To/Date/Equipment | new_message | all 4 fields | Quote Request | PASS |
| 7 | From/To/Subject/Equipment | new_message | all 4 fields | — | PASS |
| 8 | From/To/Booking/Container Number | new_message | all 4 fields | — | PASS |
| 9 | Spanish De/Para/Asunto envelope | reply | top-level only | — | PASS |
| 10 | Spanish De/A/Fecha/Equipo operational | new_message | all 4 fields | — | PASS |
| 11 | Envelope + 2nd operational From | forwarded_only | 2nd block only | Quote Request | PASS |
| 12 | Envelope with blank lines | forwarded_only | body only | — | PASS |
| 13 | Envelope alternate field order | forwarded_only | body only | — | PASS |
| 14 | Envelope with Cc/Bcc | forwarded_only | body only | — | PASS |
| 15 | Envelope without Subject | forwarded_only | body only | — | PASS |
| 16 | Envelope without Sent | forwarded_only | body only | — | PASS |
| 17 | Gmail wrote | reply | top-level only | — | PASS |
| 18 | Spanish escribió | reply | top-level only | — | PASS |
| 19 | One forwarded message | forwarded_only | body only | — | PASS |
| 20 | Two nested forwarded messages | forwarded_only | innermost body, no separator | Quote Request | PASS |
| 21 | Top-level cancellation + forwarded booking | forward | top-level only | — | PASS |
| 22 | Administrative-only + forwarded quote | forwarded_only | body only | — | PASS |
| 23 | Person-name From/To | (content preserved) | — | not asserted (out of scope, see below) | PASS (scope), documented gap (classification) |
| 24 | Weekday From/To | — | — | not Quote Request | PASS |
| 25 | Business-hour From/To | — | — | not Quote Request | PASS |
| RT-1 | Operational label glued to envelope, no blank | forwarded_only | body only | — | PASS (fixed) |
| RT-2 | Full Return Terminal glued to envelope | forwarded_only | body only | — | PASS (fixed) |
| RT-3 | Mixed-language envelope (En + Asunto) | forwarded_only | body only | — | PASS |
| RT-4 | Three nested forwards | forwarded_only | innermost body | — | PASS |
| RT-5 | Nested forward, no operational innermost content | forwarded_only | prose preserved | — | PASS |
| RT-6 | Two envelopes, no separator between | (documented limitation) | — | — | KNOWN LIMITATION (safe-failing) |

No row is marked "assumed" — every row above has a corresponding automated
test in `tests/test_message_scope_coherent_envelope.py`.

## Documentation corrections (Phase 12)

- `docs/reviews/OPERATIONS_INBOX_MESSAGE_SCOPE_FINAL_VERIFICATION.md`:
  corrected to state the nested-forward collapse is destructive (loses the
  innermost actionable request), not cosmetic; corrected its own focused
  test-count arithmetic (64 + 7 = 71 collected cases, not 58 + 13 = 71 —
  58 was a function count, not a collected-case count).
- `docs/reviews/OPERATIONS_INBOX_MESSAGE_SCOPE_FINAL_CORRECTIONS.md`:
  added a correction note pointing to this document; the defect-1 fix and
  its own prior follow-up were both incremental patches superseded by this
  pass's structural redesign.
- This document is new.
- No historical document was rewritten to pretend earlier defects never
  existed — both prior documents keep their original claims, with
  correction notes at the top.

## Disposable database

Container `calitrans_test_pg`, port `55433`, confirmed not production
(local Docker, `postgresql://postgres:testpass@localhost:55433/...`).
Scratch database `calitrans_cer_scratch` dropped/recreated fresh for each
of two independent certification runs.

## Migration results

12/12 migrations applied cleanly; rerun confirmed idempotent (0 newly
applied, 12 already applied); schema verified (27 tables, 85 indexes, 3
triggers) — PASS.

## Certification results

```
Normal order, fresh DB (run 1):        49 passed in 113.55s
Normal order, independent fresh DB (run 2): 49 passed in 116.35s
Reverse file order (same fresh state): 49 passed in 111.90s
CASE-006 independently:                 6 passed in 21.16s
CASE-010 independently:                 6 passed in 20.53s
```
No new tests were added under `tests/integration/operations_inbox/` in
this pass, so the certification total is correctly unchanged at 49.

## Focused suite results

```
tests/test_message_scope_final_corrections.py:      38 collected, 38 passed
tests/test_message_scope_and_field_boundaries.py:    33 collected, 33 passed
tests/test_message_scope_coherent_envelope.py:       47 collected, 47 passed
Combined (single pytest invocation, no double-counting): 118 collected, 118 passed
```

## Full pytest result

```
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core -> exit 0
python -c "import app"                          -> app OK
python -c "from api.main import app"            -> <fastapi.applications.FastAPI object>
git diff --check                                -> exit 0
python -m pytest -q (both env vars set)         -> 746 passed, 1 warning, 0 failed, 0 skipped
```
746 = 699 (prior full-suite baseline) + 47 (new collected cases in
`test_message_scope_coherent_envelope.py`) exactly — matches Phase 14's
requirement precisely, with no unrelated test-discovery discrepancy.

## Exact test-count reconciliation (Phase 12/23)

| Metric | Value |
|---|---|
| Test functions in `test_message_scope_coherent_envelope.py` | 17 |
| Parametrized functions in that file | 3 (`test_decision_table_row`: 21 cases; `test_administrative_wrapper_positive_is_forwarded_only`: 8 cases; `test_administrative_wrapper_negative_stays_authoritative`: 4 cases) |
| Collected cases in that file | 47 (14 non-parametrized + 21 + 8 + 4) |
| Test functions/cases added to `test_message_scope_and_field_boundaries.py` this pass | 0 (untouched) |
| Total newly collected cases this pass | 47 |
| Focused message-scope total (3 files combined, single invocation) | 118 |
| `test_message_scope_final_corrections.py` alone | 38 collected |
| `test_message_scope_and_field_boundaries.py` alone | 33 collected |
| Certification total | 49 (unchanged) |
| Full-suite total | 746 (699 + 47) |

## Manual UI status

**Not performed.** No browser was available in this environment, consistent
with every prior pass on this branch.

## Remaining known risks

- **Two envelope blocks with no separator between them** (red-team finding
  RT-6) produces an empty `classification_text` rather than preserving the
  trailing operational content. Not a realistic email-client output (no
  real client omits a transition marker between distinct messages) and not
  required by any decision-table row. Fails safe — empty text routes to
  low-confidence manual review rather than a wrong confident
  classification — rather than fixed. Pinned down by a permanent
  regression test so future changes can't silently make it worse.
- **Booking-confirmation scope limitation** (pre-existing,
  `operations_email_triage_service.py::is_booking_confirmation()`) —
  unchanged, out of this branch's diff, confirmed unaffected by this
  redesign. Follow-up: `fix/operations-inbox-booking-confirmation-scope`.
- **Full Return editor limitation** (pre-existing,
  `config.py::EDITABLE_COLUMNS` / `db_client.py::ORDER_EDITOR_EDITABLE_APP_COLUMNS`)
  — unchanged, not touched.
- **Person-name lane false positive** (`operations_inbox_service.py`'s
  `_lane_words_are_plausible`) — unchanged, out of this branch's file
  scope; `build_message_scope` itself correctly preserves the content
  either way (row 23 in the regression matrix), the false-positive lives
  entirely in the downstream classification-detail scorer in a different
  file.
- **`str(parsed)` rescanning**, **`order_parser.py` comma/`re.DOTALL`
  issue**, **incomplete Streamlit backend-boundary migration**, **no
  field-level dispatcher-confirmation provenance**, **address
  positive-model false negatives for non-US/industrial forms**,
  **administrative-phrase list still conservative by design for phrases
  with plausible independent intent** (`"Please advise"`, `"Can you handle
  this"`, `"Process this"`, `"Para procesar"`) — all unchanged from prior
  passes.

## Self-audit (Phase 15)

1. Can a no-Sent `From:/To:/Subject:` envelope still leak? **No** —
   verified by rows 3-5, 16, and `test_no_sent_envelope_fully_stripped`.
2. Can Subject operational vocabulary affect envelope-vs-operational
   classification? **No** — vocabulary is only ever matched against a
   line's own label, never another label's value; verified by every row
   using a Subject containing Quote/Rate/Booking/Container/Delivery/Port/
   Terminal wording.
3. Can a second operational From be removed? **No** — verified by row 11
   and `test_second_operational_from_survives_no_sent_envelope`, including
   Spanish and blank-line variants.
4. Can a nested forward collapse to a separator? **No** — verified by row
   20 and 3 additional red-team-derived tests (2 and 3 levels of nesting,
   innermost with no operational content).
5. Can an administrative wrapper suppress an actionable instruction?
   **No** — verified by the 4 negative-wrapper parametrized cases plus a
   red-team Spanish variant (`"Favor atender y cancelar..."`).
6. Can a real operational From/To/Date/Equipment block be clipped? **No**
   — verified by row 6 and the full original defect-1 test suite (58
   pre-existing tests, unmodified, still green).
7. Do all callers use the same classification scope? **Yes** — verified by
   direct code reading (Phase 9), not assumed.
8. Do any tests pass only because they assert final classification without
   checking `classification_text`? **No** — every decision-table row
   asserts `classification_text` content directly; only rows 23-25 (a
   pre-existing, documented, out-of-file-scope limitation) assert
   classification-level behavior only, and that limitation is explicitly
   called out rather than hidden.
9. Are any completion claims broader than the tests prove? **No** — the
   one known limitation (RT-6) is explicitly documented as unresolved, not
   claimed fixed; the person-name/booking-confirmation/Full-Return-editor
   limitations are explicitly scoped as out of this file/branch, not
   claimed eliminated.
