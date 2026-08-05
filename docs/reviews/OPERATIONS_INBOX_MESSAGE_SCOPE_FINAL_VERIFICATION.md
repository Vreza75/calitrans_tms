# Operations Inbox — Message-Scope Final Verification & Stabilization

Date: 2026-08-05

> **Correction (2026-08-05, coherent-envelope root-cause pass):** a further
> review found the nested-forward-collapsing-to-a-separator defect this
> document's stabilization fix left unaddressed is **destructive, not
> cosmetic** as originally characterized here (§"Two-nested-forward-blocks
> cosmetic artifact") - it discards the innermost actionable operational
> request, not just a bare separator string.
>
> This document's own test-count table also has an arithmetic error: the
> row "Focused message-scope total (both files together) | 58 | +13 | 71"
> mixed two different kinds of count. The correct reconciliation is **64 +
> 7 = 71 collected cases** - at this document's own starting point (HEAD
> `5d884cd`), `test_message_scope_final_corrections.py` had 31 functions/31
> collected cases and `test_message_scope_and_field_boundaries.py` had 27
> functions/33 collected cases (1 parametrized function × 7), for **64
> collected cases total** (not 58 - that was the *function* count, 31+27,
> not the collected-case count). This pass then added 7 new functions (no
> parametrization) to `test_message_scope_final_corrections.py`, making it
> 38/38, for **71 collected cases total** (64 + 7, not 58 + 13).
>
> The envelope-veto fix in this document is superseded by a structural
> redesign in `docs/reviews/OPERATIONS_INBOX_COHERENT_ENVELOPE_ROOT_CAUSE_FIX.md`;
> the underlying behavior it fixed remains fixed, now for a general
> structural reason rather than a specific signal check. This document's
> claims are left as originally written; read them as superseded wherever
> the two disagree.

An independent verification pass of `fix/operations-inbox-message-scope-final-corrections`
(HEAD `5d884cd`) did not assume the prior implementation report was correct
and independently reproduced both prior defects, ran fresh adversarial
inputs against the current implementation, and inspected the code directly.
One new defect was found and fixed (a narrow regression within the same
defect-1 fix). Two pre-existing, out-of-diff issues were reconfirmed and
remain documented, not fixed, per this pass's explicit scope.

## Verification method

Both prior defects were re-reproduced from scratch (not by re-running the
existing test suite) using a standalone script exercising `build_message_scope`
and `classify_customer_request` directly, then the full existing test suite
was inspected for coverage gaps before any adversarial probing began.

## Defect 1 (operational lane clipped) — reconfirmed fixed

Re-verified directly:
```python
body = "Please quote:\n\nFrom: Houston\nTo: Dallas\nDate: August 10\nEquipment: 40HC\n"
build_message_scope(body).scope_type            # "new_message"
build_message_scope(body).classification_text   # contains all four lines
classify_customer_request("Quote", body)         # "Quote Request"
```
Also reconfirmed for: Pickup Date, Delivery Date + "Rate requested" (no
explicit "quote" phrase — content is preserved, though the final label is
correctly "Customer Request" since there's no quote-intent term, which is
expected and not a defect), Subject-label variant, the Spanish
De/A/Fecha/Equipo form, and a Container+Booking-Number lane. All preserve
the operational content; none are clipped as history.

## Defect 2 (forwarded header leak) — reconfirmed fixed, plus a harder case

Re-verified the original reproduction, and a **harder variant not in the
original test list** — an envelope carrying both `Cc:` and a trailing
`Date:` line after `Subject:` in the same block — which also correctly
strips completely, confirming the coherent-block-extraction fix generalizes
beyond the originally tested field combinations.

## New defect found by this verification pass

**Finding — the defect-1 operational-block veto over-fired on a genuine
forwarded envelope with no inline email address whose `Subject:` value
happened to contain domain vocabulary.**

Reproduction:
```python
body = (
    "FYI\n\n-----Original Message-----\n"
    "From: Customer\nSent: Monday\nTo: Operations\nSubject: Quote Request\n\n"
    "Please confirm receipt.\nNo action is required.\n"
)
build_message_scope(body).classification_text
# 'From: Customer\nSent: Monday\nTo: Operations\nSubject: Quote Request\n\n'
# 'Please confirm receipt.\nNo action is required.'   <- entire envelope leaked
```
Root cause: `is_reply_header_line()`'s operational-block veto
(`has_to_label and has_operational_evidence and not has_email_value`) keys
`has_operational_evidence` off *any* domain-vocabulary word anywhere in the
8-line window — including inside a `Subject:` line's own value. A real
envelope's `Subject:` almost always contains exactly this vocabulary in
this business ("Rate Request", "Quote Request", "Booking Confirmation",
...). When the sender has no inline email address (a bare display name,
common after HTML-to-text normalization strips angle brackets, or simply
how some clients render a forwarded header), `has_email_value` is `False`,
so the veto fired and rejected a completely genuine, coherent envelope.

Correction: added `has_envelope_only_label` — `Sent:`/`Enviado:`/`Cc:`/
`Bcc:` are pure email-transport vocabulary that no operational freight lane
ever carries (nobody writes "Sent:" or "Cc:" as a field of a quote-lane
request). Its presence is coherent envelope evidence independent of whether
an email address is present, and now suppresses the veto. Verified this
does not reintroduce the original defect: none of the original defect-1
adversarial cases (a bare `From:`/`To:`/`Date:`/`Equipment:` lane) contain
a `Sent:`/`Cc:`/`Bcc:` label, so the veto still fires correctly for all of
them.

Also verified the same fix correctly handles the `Cc:`-only variant (no
`Sent:` at all, envelope proven solely by `Cc:` alongside `From:`/`To:`/
`Subject:`) and the exact administrative-only + bare-sender-name +
operational-Subject combination this verification pass's Part 7 adversarial
matrix specified.

## Administrative-only phrase additions

Verified the full Part 8 adversarial phrase list. Two phrases carry **zero
independent operational intent** and are extremely high-frequency in real
forwarded mail, so both were added narrowly with regression tests:
- bare `"Fwd"` (a pure forwarding abbreviation, often left as the entire
  top-level body by some clients or user habits);
- `"See attached"` / `"Please see attached"` (purely administrative —
  points at an attachment, carries no independent request).

A trailing `", thanks"` / `" thanks"` sign-off after `"FYI"` is now also
tolerated (`"FYI, thanks"`), since "thanks" alone adds no independent
intent.

**Deliberately not added**, per the "no independent operational intent"
principle: `"Please advise"` (can itself be a request for the recipient's
own judgment/decision), `"Can you handle this"` and `"Process this"` (both
carry a directive that could plausibly be the actual ask), and `"Para
procesar"` (same reasoning as "Process this"). A regression test
(`test_please_advise_remains_top_level_authoritative`) locks in that
`"Please advise"` is deliberately *not* administrative-only, so a future
change doesn't silently broaden the list past this line.

## Reconfirmed correct (no changes needed)

- Real Outlook reply recognition (From/Sent/To/Subject, From/To/Subject
  without Sent, From/Sent/To without Subject, From+email-only) — all
  correctly `scope_type == "reply"`, history excluded.
- Gmail `"On ... wrote:"` and Spanish `"El ... escribió:"` — unaffected.
- Spanish De/Enviado/Para/Asunto reply headers — unaffected.
- Forwarded-only classification for 15 header-shape/order/separator/phrase
  variants (English, Spanish, Cc+Bcc, blank lines between fields, field
  order variation, Gmail-style, nested reply-inside-forward, long
  underscore separator) — zero leakage in all 15, after the fix.
- Second-operational-From-line preservation — verified for English,
  Spanish, missing-Subject, and missing-Sent variants; the envelope is
  removed and the forwarded message's own operational lane survives in all
  four.
- Forwarded-Subject-intent-leak prevention — verified for Subject:
  Cancellation/body-is-booking, Subject: New Booking/body-is-update,
  Subject: Rate Request/no-lane-details, and Subject: Delivery Update/
  body-is-quote; none let the envelope Subject's own wording override the
  actual forwarded body's content.
- Top-level precedence — cancellation-over-forwarded-booking,
  update-over-forwarded-quote, quote-over-forwarded-booking, and
  new-booking-over-forwarded-cancellation all keep the top-level text
  authoritative (`scope_type == "forward"`).

## Two-nested-forward-blocks cosmetic artifact (not a defect, not fixed)

A message containing **two** consecutive `-----Original Message-----`
blocks leaves the second block's separator line itself (the literal string
`-----Original Message-----`, not any header label) trailing in
`classification_text`, because the "second `From:` ends the block" rule
correctly stops before the second envelope's `From:` line, and the nested-
reply-marker clip (which looks for a genuine `wrote:`/header line, not a
second forward separator) then clips at that second envelope's `From:`
line, leaving the bare separator string in between. This is cosmetic: the
separator string matches no classification keyword, carries no header
label, and does not affect any test's final classification outcome. Not
fixed in this pass — two nested forward blocks are a rare shape, and
suppressing this would require treating a second forward separator as its
own clip boundary, a design change beyond this pass's two named defects.

## Person-name lane false positive — reconfirmed, out of scope, not fixed

Reconfirmed: `"Please quote:\nFrom: John Smith\nTo: Maria Garcia\n"` still
classifies as `"Quote Request"`. This is **not** in `services/message_scope.py`
(this branch's file) — it lives in `services/operations_inbox_service.py`'s
`_lane_words_are_plausible`/`_LABELED_FROM_TO_RE` (a different, unmodified
file, from an earlier session's work, not part of this branch's diff and
not part of either named defect). Per this pass's explicit change policy
(smallest correction only for defect 1/defect 2), and per the instruction
not to build a broad personal-name detector, this is left unfixed and
documented here. A conservative, low-risk fix (requiring the lane's own
words to resemble a known transportation-location shape, or the presence
of a corroborating field like terminal/port/warehouse/address/city-state)
is possible but belongs in a separate pass touching
`operations_inbox_service.py`, not this message-scope branch.

## Booking-confirmation pre-existing risk — reconfirmed

Reconfirmed both documented scenarios still reproduce, unchanged:
```python
# current cancellation + old quoted booking-confirmation history
classify_customer_request("Cancel", body_with_old_booking_confirmation_history)
# -> "New Booking" (should be "Cancellation")

# same-message update citing both booking and container identifiers
classify_customer_request("Update", "Please update the delivery date for booking ABC123 to Aug 10, container MSCU1234567.")
# -> "New Booking" (should be "Booking Update")
```
`services/operations_email_triage_service.py::is_booking_confirmation()` is
not in this branch's diff (nor the parent `fix/operations-inbox-certification-edge-rework`'s
diff), and behaves identically at parent SHA `98e1d8e` — confirmed by
inspection: the function has not changed across any of the branches in
this series. **This is not caused by this branch.** Recommended follow-up,
as a separate branch: **`fix/operations-inbox-booking-confirmation-scope`**
— scope `is_booking_confirmation()`'s body argument through
`build_message_scope()` before use. Do not read this pass's fixes as
eliminating history contamination system-wide; they apply specifically to
the `classify_customer_request()`/`operations_intent_scores()`/
`has_quote_details()` paths.

## Full Return editor risk — reconfirmed

Reconfirmed `"Full Return Terminal"` remains absent from
`config.py::EDITABLE_COLUMNS` and
`db_client.py::ORDER_EDITOR_EDITABLE_APP_COLUMNS`. Not modified in this
pass (no failing test in this branch depends on it, and the instruction was
explicit not to touch the editor here). Parsing, attachment reconciliation,
and load create/update propagation remain fixed and unaffected by this
gap; generic manual-editor support remains a separate, tracked UI
follow-up.

## Test-count reconciliation

| Metric | Prior branch (5d884cd) | This pass | Total |
|---|---|---|---|
| Test functions in `test_message_scope_final_corrections.py` | 31 | +7 | 38 |
| Collected cases in that file (no parametrization) | 31 | +7 | 38 |
| Test functions in `test_message_scope_and_field_boundaries.py` | 27 | +0 | 27 |
| Collected cases in that file (1 parametrized function × 7 cases) | 33 | +0 | 33 |
| Total newly collected cases (both files, this pass) | — | 7 | 7 |
| Focused message-scope total (both files together) | 58 | +13 | 71 |
| Certification total | 49 | +0 | 49 |
| Full-suite total | 692 | +7 | 699 |

The reported prior-pass counts (31/6/37/58/33/692) were independently
reconfirmed exact at the start of this pass, before any new changes. This
pass added exactly 7 new test functions (3 for the newly found envelope
regression, 4 for the administrative-phrase additions), all in
`test_message_scope_final_corrections.py`; `test_message_scope_and_field_boundaries.py`
was not touched in this pass.

## Certification results

Disposable Postgres (`calitrans_test_pg:55433`), fresh drop/recreate,
12/12 migrations clean and idempotent on rerun, schema verified.

```
Normal order, fresh DB:                     49 passed
Normal order, independent fresh DB:         49 passed
Reverse file order (same fresh state):      49 passed
CASE-006 independently:                      6 passed
CASE-010 independently:                      6 passed
```

## Full pytest results

```
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core -> exit 0
python -c "import app"                          -> app OK
python -c "from api.main import app"            -> <fastapi.applications.FastAPI object>
git diff --check                                -> exit 0
python -m pytest -q (both env vars set)         -> 699 passed, 1 warning, 0 failed, 0 skipped
```
The warning is the same pre-existing, unrelated `StarletteDeprecationWarning`.

## Manual UI status

**Not performed.** No browser was available in this environment, consistent
with every prior pass on this branch.

## Remaining known risks

Unchanged from the prior pass, plus the two newly reconfirmed items above:

- **Booking-confirmation scope limitation** — reconfirmed; follow-up
  `fix/operations-inbox-booking-confirmation-scope`.
- **Full Return editor limitation** — reconfirmed; separate UI follow-up.
- **Person-name lane false positive** — newly documented in this pass;
  lives in `operations_inbox_service.py`, out of this branch's scope.
- **Two-nested-forward-blocks cosmetic separator artifact** — newly
  documented in this pass; harmless, not classification-affecting.
- **`str(parsed)` rescanning**, **`order_parser.py` comma/`re.DOTALL`
  issue**, **no manual Streamlit walkthrough**, **incomplete Streamlit
  backend-boundary migration**, **no field-level dispatcher-confirmation
  provenance**, **address positive-model false negatives for non-US/
  industrial forms**, **administrative-phrase list still conservative by
  design for phrases with plausible independent intent** — all unchanged
  from the prior pass.
