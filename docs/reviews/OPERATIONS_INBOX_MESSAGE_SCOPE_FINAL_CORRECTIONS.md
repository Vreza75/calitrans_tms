# Operations Inbox — Message-Scope Final Corrections

Date: 2026-08-05

> **Third correction (2026-08-05, label-block boundary pass):** the block
> scanner introduced by the coherent-envelope rework could still start
> classification midway through a contiguous block when an operational
> label preceded a From:/De: line (Codex H1) - see
> `docs/reviews/OPERATIONS_INBOX_LABEL_BLOCK_BOUNDARY_CORRECTION.md`.

> **Correction (2026-08-05, verification/stabilization pass):** an
> independent verification pass re-tested both fixes below with adversarial
> inputs not covered by this document's own test list and found the
> defect-1 fix (the operational-block veto) over-fired on a genuine
> forwarded envelope whenever the sender had no inline email address (a
> bare display name, e.g. `From: Customer`) **and** the envelope's own
> `Subject:` value happened to contain domain vocabulary (`Subject: Quote
> Request`, `Subject: Rate`) - extremely plausible in this business, since
> customer subject lines are almost always about exactly that. This caused
> the *entire* envelope (`Sent:`/`To:`/`Subject:`) to leak into
> `classification_text` again, for a class of input this document did not
> test. See
> `docs/reviews/OPERATIONS_INBOX_MESSAGE_SCOPE_FINAL_VERIFICATION.md` for
> the reproduction, root cause, and fix. This document's claims below are
> left as originally written; read them as superseded wherever the two
> disagree.
>
> **Second correction (2026-08-05, coherent-envelope root-cause pass):**
> both the defect-1 fix described below and its own follow-up fix above
> were themselves incremental patches, each addressing one observed input
> shape rather than the underlying structural cause - a further no-Sent,
> no-email, no-Cc/Bcc `From:`/`To:`/`Subject:` envelope (Defect A) still
> leaked in full. This has been replaced entirely by a block-level
> structural redesign; see
> `docs/reviews/OPERATIONS_INBOX_COHERENT_ENVELOPE_ROOT_CAUSE_FIX.md`.

A third independent Codex review of `fix/operations-inbox-certification-edge-rework`
(HEAD `98e1d8e`) concluded the branch architecture was generally sound but
found two HIGH-severity defects in `services/message_scope.py` that must be
fixed before merge. This document records the reproduction, root cause,
fix, and validation for both. It also documents two pre-existing,
out-of-diff risks the same review flagged, and corrects prior reporting
counts.

## Codex's two findings addressed

1. **HIGH** — an operational `From:`/`To:` lane block containing a
   `Date:`/`Sent:`/`Subject:`-style label nearby was still misdetected as
   reply-header metadata and discarded in full (including
   Equipment/Rate/etc.), because `is_reply_header_line()` returned `True`
   for a `Sent:`/`Subject:`/`Date:` label or an email address *before* the
   operational-proximity veto was ever evaluated.
2. **HIGH** — forwarded-header block stripping in
   `_forwarded_active_content()` only removed the first header line
   (`From:`); `Sent:`/`To:`/`Subject:`/`Cc:`/`Bcc:`/`Date:` leaked verbatim
   into `scope.classification_text` for every `forwarded_only` message,
   because the line-by-line cursor loop re-ran the full evidence gate with
   a shrinking look-ahead window that no longer contained the `From:` line
   after the first line was consumed.

## Defect 1 — reproduction

```python
body = "Please quote:\n\nFrom: Houston\nTo: Dallas\nDate: August 10\nEquipment: 40HC\n"
build_message_scope(body).scope_type        # "reply"        (should be "new_message")
build_message_scope(body).active_text       # "Please quote:" (From/To/Date/Equipment all lost)
classify_customer_request("Quote", body)    # "Customer Request" (should be "Quote Request")
```
Also reproduced with a `Subject:` label in place of `Date:`, and with the
Spanish equivalent (`De:`/`A:`/`Fecha:`/`Equipo:`).

## Defect 1 — root cause

In `is_reply_header_line()`, the check
```python
if has_sent_subject_date or has_email_value:
    return True, label
```
ran *before* the existing operational-proximity check
(`_OPERATIONAL_PROXIMITY_RE.search(window)`), which was only ever reached in
the fallback branch. A bare `Date:` or `Subject:` line anywhere in the
8-line look-ahead window was therefore conclusive on its own, regardless of
how clearly operational the rest of the block was.

## Defect 1 — correction

`services/message_scope.py::is_reply_header_line()` now evaluates a "strong
operational-block veto" first: a `From:`+`To:` lane carrying domain
vocabulary (equipment/quote/rate/container/port/pickup/delivery/booking/
terminal/...) with **no real email address anywhere in the window** is
rejected as a reply header immediately, before the Sent/Subject/Date/email
shortcut runs. A real email address remains an override in the other
direction — genuine Outlook/Gmail/Spanish header blocks carry an actual
sender/recipient email address essentially always, which is exactly what
distinguishes them from a bare operational lane that merely happens to sit
next to a `Date:` or `Subject:` line. `_OPERATIONAL_PROXIMITY_RE` gained
`equipo` (Spanish "equipment") and the destination-label check gained a
bare `A:` alternative, so the Spanish lane shape is covered the same way as
English. No fixture-specific string matching was used; the veto is keyed
on label shape and a fixed domain-vocabulary list already used elsewhere in
this module, not on any customer, city, or fixture value.

## Defect 2 — reproduction

```python
body = (
    "FYI\n\n-----Original Message-----\n"
    "From: Customer Name <customer@example.com>\n"
    "Sent: Monday, August 4, 2026\n"
    "To: Operations <ops@example.com>\n"
    "Subject: Rate Request\n\n"
    "Please quote Houston to Dallas.\nEquipment: 40HC\n"
)
build_message_scope(body).classification_text
# "Sent: Monday, August 4, 2026\nTo: Operations <ops@example.com>\nSubject: Rate Request\n\n"
# "Please quote Houston to Dallas.\nEquipment: 40HC"
```
Only the `From:` line was stripped; `Sent:`/`To:`/`Subject:` all leaked
through. Reproduced identically for the Spanish `Mensaje original` /
`De:`/`Enviado:`/`Para:`/`Asunto:` form.

## Defect 2 — root cause

`_forwarded_active_content()`'s cursor loop called `is_reply_header_line(lines, cursor)`
again for each subsequent line, but that function requires a `From:`/`De:`
label to be present *inside its own forward-looking window* — which no
longer includes the `From:` line once the loop has already advanced past
it. So `has_from_label` failed on the very next line (`Sent:`), the loop
broke immediately, and everything from `Sent:` onward — including the
forwarded message's real operational body — was kept as "not yet stripped."

## Defect 2 — correction

Replaced the shrinking-window re-check with coherent block extraction.
Once an anchor line proves a genuine header block exists (via the existing
full evidence gate, unchanged), a new consumption loop advances through
every following line that is itself header-label-shaped by cheap shape-only
check (`_is_header_label_line`, checking against the same
`_REPLY_HEADER_LABELS`/`_SPANISH_REPLY_HEADER_LABELS` sets, not the full
evidence gate) or blank, tolerating clients that insert blank lines between
header fields. The block ends at the first non-header line, **or at a
second `From:`/`De:` line** — a genuine envelope never repeats its own
`From:` line, so encountering a second one means the forwarded message's own
operational content (e.g. its own `From: Houston` / `To: Dallas` lane) has
started, not more envelope metadata. This was verified directly: a
forwarded envelope followed by the forwarded message's own operational
From/To lane correctly keeps the lane and strips only the envelope.

Also added `"Favor atender"` to the administrative-only phrase list
(alongside the existing `"favor revisar"` / `"para su atención"`), matching
the Spanish administrative phrases this fix's own regression tests exercise
for `forwarded_only` detection.

## Reply-header decision hierarchy (post-fix)

1. Explicit Gmail (`"On ... wrote:"`) / Spanish (`"El ... escribió:"`)
   markers — checked first, unchanged.
2. Strong operational-block evidence (`From:`+`To:` lane + domain
   vocabulary + no email address) — vetoes reply-header classification.
3. Coherent email-header block evidence (Sent/Subject/Date label, or a real
   email address in the From value) — accepted as reply-header metadata.
4. Fallback: 3+ connected metadata labels.

## Forwarded-header block behavior (post-fix)

Given a forward separator followed by any subset of
`From:`/`Sent:`/`To:`/`Cc:`/`Bcc:`/`Subject:`/`Date:` (in any order, with or
without blank lines between fields), the entire connected block is removed
before the forwarded operational body is returned. A `From:`/`To:`
operational lane appearing in the forwarded body *after* the envelope block
is preserved, distinguished from the envelope by the "second `From:` ends
the block" rule above.

## Operational From:/To:/Date: behavior (post-fix)

Verified directly: `From: Houston` / `To: Dallas` / `Date: August 10` /
`Equipment: 40HC` now stays `scope_type == "new_message"`, all four lines
survive in `classification_text`, and `classify_customer_request(...)`
returns `"Quote Request"`. Genuine Outlook/Gmail/Spanish reply detection is
unaffected — verified against the same fixture set the prior rework tested,
plus the RICGX1235800 booking fixture's bare `Date:` line (no `From:`
nearby), which still correctly stays `new_message`.

## Forwarded-only classification behavior (post-fix)

Verified: a forwarded-only quote request (`FYI` + envelope + operational
body) now classifies as `"Quote Request"` with zero header leakage into
`classification_text`. A forwarded envelope whose `Subject:` line itself
says `"Quote Request"` but whose forwarded body has no active quote
details correctly does **not** classify as `"Quote Request"` (the leaked
label no longer reaches `classification_text` at all now, and even before
this fix, `has_quote_details`'s separate detail-gate already caught this
case — both layers now agree). A meaningful top-level cancellation ahead of
a forwarded new-booking block remains authoritative (`scope_type ==
"forward"`, `classification_text` is the top-level cancellation text).

## Tests added

- `tests/test_message_scope_final_corrections.py` (new, 31 tests): 8
  defect-1 reproductions/adversarial cases (From/To + Date, Pickup Date,
  Delivery Date+Rate, Subject, Container Number, Booking Number, Port,
  Spanish), 3 non-regression checks (real Outlook+Date still reply,
  From+email still reply, bare Date without From still not history), the
  RICGX1235800 fixture check, 3 lane-plausibility non-regressions
  (person-name pair, weekday pair, time-of-day pair), 1 no-quote-intent
  check, and 15 defect-2 reproductions/adversarial cases (complete English
  header strip, complete Spanish header strip, header block missing
  Cc/Bcc, missing Sent, missing Subject, blank lines between fields, new
  booking / cancellation / existing-load-update forwarded bodies, nested
  reply-inside-forward, operational lane surviving after envelope headers,
  forwarded Subject carrying false intent signal with no active details,
  no-leaked-metadata-contributes-intent-signals, and top-level-cancellation-
  stays-authoritative).
- `tests/test_message_scope_and_field_boundaries.py` (+6 tests): closes the
  prior LOW finding's remaining gap — valid-existing-protected and
  invalid-existing-and-invalid-document-yields-no-valid-value for Contact
  Email, Contact Phone, and Contact Company (previously only Contact Name
  had this coverage). All 6 pass unmodified against the existing, unchanged
  `merge_saved_attachment_fields` — confirms the field-agnostic policy does
  generalize, without any behavior change.

All new tests were confirmed RED against the pre-fix code before either fix
was applied (defect-1 tests: 3 of the 18 non-forwarded cases failed
pre-fix; defect-2 tests: all 12 forwarded-related cases failed pre-fix),
then GREEN after each corresponding fix, per this repository's TDD
convention.

## Booking-confirmation scope limitation (pre-existing, documented, not fixed here)

An earlier independent review found that `services/operations_email_triage_service.py::is_booking_confirmation()`
evaluates raw, unscoped body text and is checked before any message-scope-
aware classification runs (`classify_customer_request()` returns
`"New Booking"` immediately if it returns `True`, and the same function
gates `_request_type_from_rules()` and
`operations_inbox_service.py::enforce_authoritative_booking_triage()`).
This means:

- a current cancellation/update whose thread contains old quoted
  booking-confirmation-shaped content can still misclassify as
  `"New Booking"`;
- a current update that simply cites both a booking number and a container
  number can misclassify as `"New Booking"` regardless of history.

**This is not caused by this branch** — `operations_email_triage_service.py`
is not in this branch's diff and behaves identically on the base branch.
Message-scope contamination is fixed for the `classify_customer_request()`/
`operations_intent_scores()`/`has_quote_details()` paths this and the prior
rework pass touched — **not for every classifier entry point**. Do not read
"contamination eliminated" as a system-wide claim; it applies specifically
to the Quote Request / lane-detection paths these two passes corrected.

**Follow-up recommendation: "Scope booking-confirmation triage to active
message content"** — scope `is_booking_confirmation()`'s body argument
through `build_message_scope()` before use, in a dedicated follow-up pass.

## Full Return editor limitation (pre-existing, documented, not fixed here)

A prior independent review found `"Full Return Terminal"` is present in
every parsing/attachment-reconciliation/load-create/load-update mapping
this and the prior rework pass fixed, but is still absent from
`config.py::EDITABLE_COLUMNS` and
`db_client.py::ORDER_EDITOR_EDITABLE_APP_COLUMNS` — the generic Order
Detail Editor's allowlists. This means a dispatcher cannot manually
correct this field on an already-created load through the standard editor
UI, even though it now propagates correctly through every automated path.
Not fixed in this pass (no failing test in this branch depends on it); left
as a tracked, separate UI follow-up.

## Reporting-count corrections

| Metric | Prior rework value | This pass |
|---|---|---|
| Branch-only commits (this pass) | — | 5: `01013be`, `23fa9c3`, `eb0994d`, `10e137d`, plus this docs commit |
| Changed files (this pass) | — | 5: `services/message_scope.py`, 2 test files, 2 docs files (1 new) |
| New test functions (this pass) | — | 37 (31 in the new file + 6 identity tests) |
| Newly collected cases (this pass) | — | 37 (no parametrization added) |
| Certification cases | 49 | 49 (unchanged — no new tests under `tests/integration/operations_inbox/` in this pass) |
| Full pytest (prior baseline) | 655 passed | 692 passed (655 + 37), 0 failed, 0 skipped |

The prior rework's own counts (7 commits, 12 changed files, 28 net new
collected cases, 49 certification, 655 full pytest) are unchanged and were
independently re-verified as still accurate at the start of this pass.

## Certification results

Disposable Postgres (`calitrans_test_pg:55433`), fresh drop/recreate twice,
12/12 migrations clean and idempotent on rerun, schema verified (27 tables,
85 indexes, 3 triggers).

```
Normal order, fresh DB (run 1):            49 passed in 82.97s
Normal order, independent fresh DB (run 2): 49 passed in 85.25s
Reverse file order (same fresh state):      49 passed in 84.04s
CASE-006 independently:                     6 passed in 14.00s
CASE-010 independently:                     6 passed in 12.56s
Message-scope tests independently:         58 passed in 3.71s
```
No new tests were added under `tests/integration/operations_inbox/` in this
pass, so the certification total is unchanged at 49.

## Full pytest results

```
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core -> exit 0
python -c "import app"                          -> app OK
python -c "from api.main import app"            -> <fastapi.applications.FastAPI object>
git diff --check                                -> exit 0
python -m pytest -q (both env vars set)         -> 692 passed, 1 warning, 0 failed, 0 skipped
```
The warning is the pre-existing, unrelated `StarletteDeprecationWarning`
(httpx/starlette testclient), not introduced by this pass.

## Manual UI status

**Not performed.** No browser was available in this environment, consistent
with every prior pass on this branch.

## Remaining known risks

- **Booking-confirmation scope limitation** — see above; tracked follow-up
  "Scope booking-confirmation triage to active message content."
- **Full Return editor limitation** — see above; tracked as a separate UI
  follow-up.
- **`str(parsed)` rescanning** — unchanged from the prior rework pass;
  still present at two spots in `operations_inbox_service.py`, not
  classification-scoring-relevant at either location.
- **`services/order_parser.py`'s comma/`re.DOTALL` interaction** — unchanged,
  not touched by this pass.
- **No manual Streamlit browser walkthrough** — unchanged.
- **Streamlit backend-boundary migration incomplete** — out of scope,
  unchanged.
- **No field-level dispatcher-confirmation provenance** — unchanged.
- **Address positive-model false negatives** (non-US postal formats,
  industrial forms like `Warehouse 12`/`Plant 4`) — unchanged from the prior
  rework pass; fails safe (omits, does not corrupt), not addressed in this
  narrow pass since it is outside the two named defects.
- **Administrative-phrase list still narrower than a full adversarial set**
  — `"see attached"`, `"can you handle this"`, `"process this"` are still
  not recognized as administrative-only; this conservatively keeps
  top-level text authoritative rather than causing data loss, and was out
  of scope for this pass's two named defects.
