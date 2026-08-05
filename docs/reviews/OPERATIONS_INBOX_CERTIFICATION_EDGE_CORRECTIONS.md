# Operations Inbox Certification — Edge-Correction Pass

Date: 2026-08-04

> **Correction (2026-08-04, edge-rework pass):** a second independent Codex
> review of this document's own branch found that several claims here
> ("never absorbs an unrelated field", "history-aware", the identity-field
> "fully tested" claim) were true only for the specific fixture inputs
> tested at the time, not for the underlying production paths generally.
> Four real HIGH-severity gaps survived this pass's fixes: quote intent from
> quoted history could still combine with active-message details; an active
> operational `From:`/`To:` lane could still be clipped as if it were quoted
> email history; multiline address extraction could still absorb fields this
> document didn't test against (Empty Pickup, Pickup Date, a bare signature
> name/email); and Full Return Terminal - despite passing CASE-006 - was
> silently dropped by attachment reconciliation and never reached load
> creation/update at all. See
> `docs/reviews/OPERATIONS_INBOX_CERTIFICATION_EDGE_REWORK.md` for the full
> second-pass investigation, root causes, and fixes. The claims below are
> left as originally written (an accurate record of what that pass actually
> verified) rather than silently rewritten - read them as superseded by the
> rework document wherever the two disagree.

Follow-up correction pass after an independent Codex review of
`fix/operations-inbox-certification-regressions` (HEAD `9cd8c7b`) found
remaining HIGH/MEDIUM issues. This document records each finding, the
reproduction, the root cause, the fix, and the tests added. See
`docs/reviews/OPERATIONS_INBOX_CERTIFICATION_INVESTIGATION.md` for the
original certification investigation (now corrected — see its CASE-010
addendum).

## Codex findings addressed

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | HIGH | Valid explicitly labeled letter-only references rejected (`Order: ABC`, `Referencia: ABC`) | Fixed |
| 2 | HIGH | Lowercase/uppercase/mixed-case quote lanes misrouted | Fixed |
| 3 | HIGH | Full Return Terminal value discarded instead of mapped to its dedicated field | Fixed |
| 4 | MEDIUM | Delivery Address loses to Warehouse Address in some orderings; multi-line addresses lose continuation lines | Fixed |
| 5 | MEDIUM | Attachment identity behavior documented as "fill blank only" but actually protects only valid existing values | Clarified + fully tested |
| 6 | MEDIUM | Investigation doc incorrectly claimed CASE-010 unsupported/skipped | Corrected |

## Reference Number: root cause and fix

**Root cause 1 (regex bug):** the previous fix's `explicit_context` check used
`orden?` intending to match English "order" or Spanish "orden" — `orden?`
is actually `"orde"` + optional `"n"`, so it matches `"orde"`/`"orden"` and
**never** matches `"order"`. Every English `"Order: ABC"`-style label was
silently rejected as a result.

**Root cause 2 (missing Spanish support):** `"referencia"` was never
included in the `explicit_context` keyword list at all (only the *candidate
generation* pattern had it), so `"Referencia: ABC"` generated a candidate
but validation always rejected it.

**Root cause 3 (too strict once widened):** naively requiring a real
separator (`:`/`#`/`-`) for every label broke `"Referencia No. ABC"`-style
labels that use a number-designator word instead of punctuation. Fixed by
treating "keyword + number-designator word (`number`/`no.`/`#`)" as an
independently sufficient signal, separate from "keyword + real separator" —
matching how `"PO No 12345"` and similar labels are actually written.

**Regression this reopened, and its fix:** the designator-word leniency
above reopened the original `str(parsed)`-rescanning bug (`"'Reference
Number': ''"` capturing `"Number"` as a fake value), because "Reference" +
"Number" now satisfied the designator-word branch even with no real
separator following. Fixed at the true source — candidate *generation* —
by only trusting a bare-whitespace separator immediately after an explicit
designator word, never after the bare keyword alone. This eliminates the
fake candidate entirely rather than relying on validation to catch it.

**Fix locations:** `services/operations_field_service.py`:
- `generate_field_candidates`'s Reference Number pattern (added `pedido`,
  restructured the separator group).
- `validate_field_value`'s Reference Number `explicit_context` (fixed
  `orden?` → `orde[rn]`, added `referencia`, added the designator-word
  branch, kept "order"/"orden"/"pedido" restricted to `:`/`#` — never a
  bare `-` — since `"<X> Order - <description>"` is a common subject-line
  title separator).

### Valid English forms confirmed

`Reference:`, `Reference Number:`, `Reference No.:`, `Reference #:`, `Ref:`,
`PO:`, `Order:`, `Order #:`, `Order No.:`, `Order # ` (bare, no colon),
`Shipment:` — all accept letter-only, numeric, alphanumeric, dash, slash,
period, and underscore values (`ABC`, `ABC-123`, `SO217089A/C25749C`,
`REF_2026`, `A.B.C`, `12345`).

### Valid Spanish forms confirmed

`Referencia:`, `Referencia No. ` (bare), `No. de Referencia:`, `Orden:`,
`Pedido:`.

### False-positive protection confirmed (still rejected)

`"The order has been entered."`, `"All other order information remains
unchanged."`, `"Please enter the order tomorrow."`, `"Reference the
previous email."`, `"Reference the prior message."`, `"The shipment has
arrived."`, `"New Import Order - Attached Booking Document."`, `"Order -
Attached Document."`, and the stringified-dict case
(`"'Reference Number': ''"`).

Tests: `tests/test_operations_field_service.py` (21 valid-form cases + 8
prose/garbage-rejection cases, parametrized).

## Quote-lane detection: root cause and fix

**Root cause:** the previous fix's lane regex required Title-Case words
(`\b[A-Z][a-zA-Z]+...`) specifically to avoid false positives like `"John to
Maria"`/`"Monday to Friday"` — but real customer messages routinely arrive
all-lowercase or all-caps, so `"please quote houston to dallas 40hc"` never
matched. Simply removing the case requirement would reopen the false-positive
problem, since `"John to Maria"` is structurally identical to `"Houston to
Dallas"` — no regex can distinguish a place name from a person's name by
capitalization alone.

**Fix — proximity-based plausibility, not capitalization:** a `"<X> to <Y>"`
(or Spanish `"<X> a <Y>"`, or `"<X> → <Y>"`) phrase is only trusted as an
actual lane when the **same sentence** also contains a quote/rate-intent word
(`quote`, `rate`, `pricing`, `cotizar`, `tarifa`, ...) or an equipment/size
token (`40HC`, `20FT`, ...). Real freight lanes in this domain co-occur with
one of those signals in the same clause; incidental phrases like `"John to
Maria, FYI"`, `"Monday to Friday"`, `"8 AM to 5 PM"`, `"reply to customer"`,
and `"send to accounting"` do not. A stop-word list (days, AM/PM, reply/
send/forward, common roles) adds a second layer of defense. Case-insensitive
throughout.

**History-awareness:** the sentence-scoping check first strips quoted/
forwarded history via the existing `extract_latest_email_body` helper (the
same one the production sync pipeline already uses upstream), so a quote
request that exists only inside a quoted reply never counts as active detail
for the current message — verified with a realistic `"On ... wrote:" / "> "`
quoted-reply fixture.

**Two supporting gaps closed in intent scoring** (`QUOTE_INTENT_TERMS` /
`operations_intent_scores`, which decides whether a message is a quote
request *candidate* at all, upstream of the detail-scoring above):
- Added `"need pricing"` (required example `"Need pricing from houston to
  dallas."` had no matching phrase before).
- Added `"cotizar"` (required example `"Por favor cotizar..."` uses this
  verb form; only `"cotizacion"`/`"pueden cotizar"` existed before).
- Added a shorthand pattern (`_QUOTE_LANE_SHORTHAND_RE`) for `"Quote <X> to/
  →/a <Y>"`, since that phrasing never matches any of the fixed
  `QUOTE_INTENT_TERMS` phrases.

**Fix locations:** `services/operations_inbox_service.py` —
`_has_plausible_quote_lane`, `_sentence_has_plausible_lane`,
`_lane_words_are_plausible`, `has_quote_details` (now strips quoted history
for *all* its free-text checks, not just the lane check), `QUOTE_INTENT_TERMS`,
`_QUOTE_LANE_SHORTHAND_RE`.

### Valid lanes confirmed (case-insensitive, English + Spanish)

Title-case, lowercase, uppercase, and mixed-case `"quote Houston to Dallas
40HC"`; `"Rate request from Houston to Dallas."`; `"Need pricing from
houston to dallas."`; `"Quote Houston → Dallas."`; `"Por favor cotizar
Houston a Dallas."`; `"Necesito tarifa de Houston a Dallas."`.

### False lanes confirmed rejected

`"John to Maria"`, `"Monday to Friday"`, `"8 AM to 5 PM"`, `"reply to
customer"`, `"send to accounting"` — all in messages that also contain real
quote language elsewhere, proving the sentence-scoping (not just absence of
quote intent) is what protects them.

### Quoted-history / classification-priority protection confirmed

- A quote request that exists only in quoted/forwarded history, with no
  active quote intent in the newest message → does not route to Quote
  Request.
- An Existing Load Update (delivery-date change) whose quoted history
  happens to contain an old `"Please quote..."` line → stays `Booking
  Update`, not `Quote Request`.
- A New Booking with no quote intent anywhere → stays `New Booking`.

Tests: `tests/test_operations_classification.py` (15 new cases).

## Full Return Terminal: dedicated field mapping

**Root cause:** the prior fix correctly *removed* `"full return"` from the
`Port` candidate pattern (stopping the misclassification), but never gave it
anywhere else to go — the value was simply discarded.

**Fix:** `"Full Return Terminal"` is now a first-class field end-to-end:
- `services/email_parser.py`: added to `FIELDS`; new `LABEL_ALIASES` entry
  (`Full Return Terminal`, `Full Return`, `Return Terminal`, `Empty Return
  Terminal`, `Empty Return`).
- `services/operations_field_service.py`: added to `SHARED_OPERATION_FIELDS`;
  new candidate pattern (parallel to the Port pattern, but exclusively for
  these labels); added to the `{"Warehouse", "Port", "Terminal"}` location
  validation group.
- `tests/integration/operations_inbox/harness.py`: added `full_return_terminal`
  to `EXPECTED_SCHEMA_FIELDS` and `capture_actual_result` (additive — every
  existing case's `expected.json` is untouched since `compare()` only checks
  keys present in `expected`).
- `tests/fixtures/operations_inbox/CASE-006/expected.json`: added
  `"full_return_terminal": "Bayport Terminal"` — the fixture's own real
  source text (`"FULL RETURN: Bayport Terminal"`) is retained and asserted,
  not discarded to `{}`.
- `pages_app/operations_inbox.py` already read `parsed.get("Full Return
  Terminal")` for the draft-projection UI (`full_return_terminal` column) —
  it was already wired to consume this field, just nothing had ever
  populated it. No changes needed there.

Confirmed the value never populates `Port` or the shared `Terminal` field,
across all five supported label variants.

Tests: `tests/test_operations_field_service.py` (7 cases),
`tests/test_email_parser_multi_container.py` (1 end-to-end case using the
real RICGX1235800 fixture body), and CASE-006's own certification test
(now asserts `full_return_terminal` reaches the persisted `order_intake`
row, not just the in-memory parse).

## Address precedence and multi-line addresses

**Root cause (precedence):** the `Address` field's candidate pattern folded
`"delivery address"`, generic `"address"`, and `"warehouse address"` into
one pattern with identical confidence — `select_field_candidates`'s `max()`
tie-break picks whichever label appears **first** in the document, which is
order-dependent, not semantically deterministic.

**Fix:** split into three separate candidate calls with distinct confidence
tiers (`delivery_address_label` > `address_label` > `warehouse_address_label`),
so precedence is explicit and independent of label order in the source text.
Pickup Address remains fully separate (unchanged from the prior fix).

**Root cause (multi-line):** the pattern only ever captured `[^\r\n]+` — a
single line — so a delivery address split across a label line and one or
more continuation lines (city/state/ZIP, a suite line) lost everything after
the first line.

**Fix:** `_address_pattern()` now captures continuation lines after the
label line, stopping at the next recognized field label (a large, explicit
stop-list — Port, Terminal, Contact, Phone, Email, Notes, dates, Full
Return, etc.) or a blank line — so it never absorbs an unrelated field.
Captured multi-line values are joined with `", "` (not raw newlines and not
a bare-space collapse, which would glue the street and city together) to
match this pipeline's existing single-line comma-separated address
convention. This same continuation-capture logic is shared by all three
Address-precedence tiers, so Warehouse Address and generic Address are
multi-line-capable too.

Confirmed: precedence holds regardless of label order and across all four
service flows (Local Import, Local Export, Import, Export — the address
mechanism is flow-agnostic by design, so this is one underlying fix, not
four separate ones). Multi-line capture confirmed for two-line, three-line
(with a suite line), first-line-plus-continuation, stopping before the next
label, and stopping at a blank line.

Tests: `tests/test_operations_field_service.py` (8 precedence cases + 5
multi-line cases).

## Identity field merge policy

**Finding:** the docstring said identity fields (Contact Name/Email/Phone/
Company) were "always fill-blank-only regardless of force" — the actual
code (from the prior session's fix) preserves a **valid** existing value and
replaces a **blank or invalid** one, which is not literally "fill-blank-only".
This was a documentation-accuracy gap, not a code defect — verified by
tracing the implementation, not by guessing.

**Policy adopted (clarified, not changed):**
- blank existing value → may be filled by a valid document value.
- non-blank, **valid** existing value → always protected, regardless of
  `force`.
- non-blank, **invalid** existing value → treated the same as blank; may be
  replaced by a valid document value.

**Known limitation, documented explicitly:** `order_intake.parsed_data` has
no field-level dispatcher-confirmed flag, so "valid existing value" is used
as the closest safe proxy for "already trustworthy." There is no way at this
layer to distinguish a dispatcher-confirmed identity from an automatically-
parsed-but-valid one. If field-level confirmation metadata is added to this
structure later, this policy should be tightened to require it explicitly.

**Fix location:** `services/operations_attachment_service.py`'s
`merge_saved_attachment_fields` docstring rewritten to state the actual
policy precisely (no code logic changed — the previous session's carve-out
was already implementing this correctly).

Tests: `tests/test_operations_merge_saved_attachment_fields.py` — all four
identity fields (previously only Contact Name was tested) × blank-existing,
whitespace-only-existing, valid-existing-protected-from-different-valid-
document, invalid-existing-replaced × `force=True`/`force=False` (32
parametrized cases, all passing).

## CASE-010 documentation correction

`docs/reviews/OPERATIONS_INBOX_CERTIFICATION_INVESTIGATION.md` previously
stated CASE-010 was "intentionally unimplemented" and its 6 tests were
"skipped by design." **This was wrong.** Root cause: one specific `pytest`
invocation during that investigation was run in a shell call without
`INBOX_CERTIFICATION_DATABASE_URL` set, which made CASE-010's opt-in-gated
tests skip — an artifact of that one command, not a real property of the
code. CASE-010's multi-order-splitting capability was already implemented
and accepted in an earlier session (see
`tests/fixtures/operations_inbox/CASE-010/verification.md`, decision
**ACCEPTED**) and was already passing 6/6 at that document's own starting
commit, before any of its fixes.

Verified directly this session: fresh disposable database, migrations
applied, `pytest -q tests/integration/operations_inbox` with the environment
variable correctly set → **48 collected, 48 passed, 0 skipped** (CASE-000
through CASE-010 inclusive), reproduced 3 times (normal order twice, reverse
order once) with identical results every time, plus two independent
`python scripts/run_inbox_case.py CASE-010` CLI runs (both `RESULT: PASSED`,
`exact_record_pass: True`, `duplicate_protection: PASS`, row count 2 before
and after rerun).

The investigation document has been corrected in place (a dated correction
note at the top, the baseline table, the final-results table, and §16 all
now state CASE-010 passes). No executable behavior was changed to make this
correction — the document was brought into agreement with the tests, not the
other way around.

## actual.json handling

Confirmed the existing convention (tracked diagnostic snapshots, regenerated
by `harness.py::run_case` on every run) and did not regenerate anything
beyond what these fixes legitimately changed. Every regenerated
`actual.json` in this pass is either:
- a genuine field-value change matching a real fix (CASE-006 now surfaces
  `full_return_terminal`), or
- the single additive `"full_return_terminal": null` line that every other
  case picked up automatically once the field was added to the harness's
  shared schema (harmless, expected, since `compare()` only checks keys
  present in each case's own `expected.json`).

Verified: no timestamps, no local file paths, no secrets, no environment-
specific IDs in any regenerated `actual.json` (row IDs are deterministic —
always `1` — since the harness truncates with `RESTART IDENTITY` before
every run). `expected.json` was never replaced by `actual.json` content
automatically; every `expected.json` change (CASE-000's casing, CASE-006's
new field) was made deliberately with source evidence, as its own commit.

## Certification results

Disposable Postgres (`calitrans_test_pg`, port `55433`), dropped/recreated
fresh, 12/12 migrations applied cleanly and confirmed idempotent on rerun.

```
tests/integration/operations_inbox: 48 collected, 48 passed, 0 failed, 0 skipped
```

Reproduced 3 times from independently reset databases (normal order twice,
reverse order once) — identical every time. CASE-010 run twice independently
via the CLI — both passed, deterministic.

## Full regression results

```
python -m compileall .                    -> exit 0, no errors
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core -> exit 0
python -c "import app"                    -> app OK
python -c "from api.main import app"      -> <fastapi.applications.FastAPI object>
python -m pytest -q                       -> 627 passed, 1 warning, 0 failed, 0 skipped
```

The one warning is the same pre-existing, unrelated `StarletteDeprecationWarning`
noted in the original investigation. 627 exceeds the prior branch's 524
without weakening any existing assertion — the increase is entirely new
test coverage (reference-number valid/invalid forms, quote-lane valid/
invalid/history cases, Full Return mapping, address precedence/multi-line,
and the full four-field identity merge matrix).

## Manual UI check

**Not performed.** No browser was available in this environment. Per the
task's instruction, this is stated explicitly rather than treating the
passing automated suite or a server 200 response as a substitute for visual
verification.

## Remaining known risks

- **`str(parsed)` rescanning:** several call sites in
  `services/operations_inbox_service.py` (`classify_customer_request`,
  `action_required_for_request`, others) embed a stringified `parsed` dict
  back into free text for re-scanning via the same label-matching pipeline.
  This is a pre-existing, pervasive convention, not something this pass
  redesigned — it was only neutralized for the two places it caused actual
  wrong output (the reference-number dict-key leak, fixed at the candidate-
  generation source; and quote-lane detail scoring, fixed by stripping
  quoted history and any free-text checks inside `has_quote_details`
  specifically). A full redesign of this convention would be a much larger,
  separate effort.
- **`services/order_parser.py`'s `find_pattern` comma/`re.DOTALL`
  interaction** (documented in an earlier session's
  `docs/CODE_REVIEW_PLAYBOOK.md` §38) remains untouched — out of scope,
  does not affect any certified case.
- **No manual browser check performed** — see above.
- **The Streamlit backend-boundary migration is not complete.** This pass
  only corrected Operations Inbox parsing/classification/field-mapping
  behavior; it did not touch or advance the Streamlit/FastAPI boundary
  work. The recommended next architecture task remains: rewire the
  Streamlit Operations Inbox queue read to the `application/`
  SQL-pagination service behind a feature flag.
