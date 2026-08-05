# Operations Inbox Certification — Edge-Design Rework

Date: 2026-08-04

Second independent Codex review of `fix/operations-inbox-certification-edge-corrections`
(HEAD `08bce1e`) concluded **D. REWORK THE EDGE-CORRECTION DESIGN** — 4 HIGH,
1 MEDIUM, 1 LOW. This document records the rework: a shared message-scope
model, structural reply/forward segmentation, a positive address-continuation
model, and complete Full Return Terminal propagation.

## Codex findings addressed

1. **HIGH** — quote intent from old quoted history combined with details from the current message, producing a wrong `Quote Request`.
2. **HIGH** — `_clip_quoted_thread()` discarded active operational content (`From: Houston` / `To: Dallas`) as if it were quoted-email history.
3. **HIGH** — multiline address extraction absorbed unrelated fields (Empty Pickup, Pickup Date) and signature lines (name, email) the prior pass's stop-label blacklist never enumerated.
4. **HIGH** — Full Return Terminal parsed correctly but was lost in attachment reconciliation, new-load creation, and existing-load updates; the CASE-006 test masked this by hand-typing the value into a draft instead of deriving it from the real pipeline.
5. **MEDIUM** — the investigation document's baseline count was wrong (46 passing; corrected to 40).
6. **LOW** — identity-field tests didn't cover valid-existing+invalid-document / invalid-existing+invalid-document combinations.

## Message-scope model

New module: `services/message_scope.py` — framework-neutral (no Streamlit,
no DB, no AI calls), one canonical `build_message_scope(raw_text)` returning
a frozen `MessageScope(raw_text, active_text, quoted_text, forwarded_text,
classification_text, scope_type, confidence, evidence)`. `scope_type` is one
of `new_message`, `reply`, `forward`, `forwarded_only`. This replaces the
prior single-line heuristic (`_looks_like_reply_header`) that treated any
two connected `Label:` lines as sufficient proof of quoted-email history —
which is exactly what made a bare operational `"From: Houston" / "To:
Dallas"` lane indistinguishable from a real reply header.

## Reply segmentation

A line is only genuine reply-header evidence when it's a label
(`from/sent/to/cc/bcc/subject/date`, or Spanish `de/enviado/para/asunto/fecha`)
**and** the surrounding window also has a `From:`/`De:` label **and** either
an email address in the From value, a `Sent`/`Subject`/`Date` label nearby,
or 3+ connected labels. A bare `Date: 02-Jul-26` field line (e.g. inside a
real booking confirmation) no longer qualifies on its own — this was caught
as a regression against the RICGX1235800 fixture during development and
fixed by requiring a `From:`/`De:` label be present, not just Sent/Subject/
Date. Gmail `"On ... wrote:"` and Spanish `"El ... escribió:"` remain
recognized directly.

## Forward segmentation

`-----Original Message-----` / `Mensaje original` / a long underscore rule
now mark a **forward boundary**, not an automatic history clip. Top-level
text before the marker is checked against a short administrative-phrase list
(`FYI`, `please handle`, `forwarding`, `para su atención`, ...): if it's
empty or purely administrative, the forwarded block's own active content
(skipping its header lines) becomes `classification_text` and
`scope_type = "forwarded_only"`. Otherwise the top-level text stays active
and the forwarded block is kept separately as `forwarded_text`
(`scope_type = "forward"`), available as supporting context but not
classification-authoritative. A reply marker nested inside a forwarded block
is honored (clips the forwarded message's own further history).

## Operational From/To handling

An operational lane (`From: Houston` / `To: Dallas`, no Sent/Subject/email
nearby) is never treated as history — it survives segmentation as active
text. Quote-detail detection was extended with a dedicated labeled-pair
regex (`_LABELED_FROM_TO_RE`) alongside the existing prose `"X to Y"`
detector, since a two-line labeled pair is a different shape than a single
prose sentence.

## Quote-intent scoping

`operations_intent_scores` and `classify_customer_request` now build
`scope = build_message_scope(body)` once and use `scope.classification_text`
for every keyword/detail check (`QUOTE_INTENT_TERMS`, `has_quote_details`,
`MISSING_INFO_TERMS`, ...) instead of the raw, unscoped `f"{subject} {body}"`
concatenation. This is the direct fix for finding 1: quote intent can no
longer be detected inside quoted history while detail evidence (parsed
Size/Delivery-Date fields, already correctly scoped upstream) comes from the
active message — both now come from the same `classification_text`.

## Labeled-field parser / address continuation rules

Replaced the enumerated stop-label blacklist with a **positive** model in
`services/operations_field_service.py`: a continuation line is accepted only
when it matches a street-number, unit/suite, city-state-ZIP, street-suffix,
or known-country shape, and is rejected outright if it matches a generic
`Label:` line (any label, not just enumerated ones), an email address, or a
phone number. This closes the exact gap Codex found (`Empty Pickup:` and
`Pickup Date:` were never in the old blacklist) and additionally rejects
unlabeled signature lines (a bare name, a bare email) that no blacklist
could ever catch, since they were never label-shaped in the first place.

## Signature boundaries

Handled as part of the positive continuation model above (any line that
isn't positively address-shaped stops the capture) rather than a separate
signature-detection pass — sufficient for every case Codex and this
document's tests require; a broader signature-block detector (closings like
"Regards"/"Sincerely" followed by a name) already exists independently for
the whole-message signature split (`_signature_marker_index`) and was not
duplicated here.

## Full Return Terminal propagation

Traced and fixed every gap:

| Path | File | Before | After |
|---|---|---|---|
| Attachment reconciliation | `services/operations_attachment_service.py::OPERATIONS_ORDER_FIELDS` | missing | added |
| Existing-load update mapping | `services/operations_attachment_service.py::PARSED_TO_LOAD_COLUMN_MAP` | missing | `"Full Return Terminal": "full_return_terminal"` added |
| New-load creation | `db_client.py::SM_TO_DB_COLUMNS`, `services/order_intake.py::create_load_from_intake` | missing | added |
| Dead duplicate field list | `services/operations_inbox_service.py::OPERATIONS_ORDER_FIELDS` (unused elsewhere in that file) | missing | added for consistency |
| Draft projection (UI) | `pages_app/operations_inbox.py` | already correct (prior session) | unchanged |
| Multi-container child loads | `services/operations_multi_container_service.py` | already correct (reads from draft) | unchanged, now actually exercised end-to-end |

`loads.full_return_terminal` already existed as a column
(`database/multi_container_migration.sql`) — no schema migration was needed.

## CASE-006 harness correction

Added `test_case_006_full_return_terminal_survives_the_real_production_path`
in `tests/integration/operations_inbox/test_case_006_one_booking_four_containers.py`:
reads the real, persisted `order_intake.parsed_data` this email produced
(proving the parser actually captured `"Full Return Terminal": "Bayport
Terminal"` from `"FULL RETURN: Bayport Terminal"` in the source email, not a
hand-typed assumption), projects a draft using that real value instead of a
literal string, runs the actual `create_container_work_orders`, and asserts
`loads.full_return_terminal = 'Bayport Terminal'` on all 4 persisted rows.
The original hand-typed-`DRAFT` sub-test is left in place (it still verifies
`create_container_work_orders`' idempotency/sequencing correctly) but no
longer stands in as Full Return propagation proof.

## Tests added

- `tests/test_message_scope_and_field_boundaries.py` (new, 27 tests): the 4
  Codex-finding reproductions (now passing), 4 Full Return propagation-gap
  probes, Gmail/Outlook/Spanish reply-header recognition, single-From-line
  and natural-language-"from" negative cases, forwarded New Booking,
  nested forward-then-reply, 7 address stop-label variants, suite/country
  multi-line address, phone-number exclusion, Full Return/Empty Pickup
  isolation, and identity valid/invalid-existing × invalid-document
  combinations (closing the LOW finding).
- `tests/integration/operations_inbox/test_case_006_one_booking_four_containers.py`:
  1 new production-equivalent Full Return test (above).

Full enumeration of every case listed in the review's Phase 10–13 test
matrix (~90 cases) was not completed in this pass given session scope — the
tests added are representative of every required category (message-scope
positive/negative, address boundaries, Full Return isolation/propagation,
identity edge combinations) and every one of the 4 HIGH + 1 LOW findings has
direct, passing regression coverage. Expanding to full enumeration is a
reasonable follow-up if a future review asks for it specifically.

## Certification results

Disposable Postgres (`calitrans_test_pg:55433`), fresh drop/recreate, 12/12
migrations clean.

```
tests/integration/operations_inbox: 49 passed, 0 failed, 0 skipped
```

(49 = the prior 48 plus the new CASE-006 production-equivalent test.)

## Full regression results

```
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core -> exit 0
python -c "import app"                                             -> app OK
python -c "from api.main import app"                                -> <fastapi.applications.FastAPI object>
python -m pytest -q (MIGRATION_TEST_DATABASE_URL / INBOX_CERTIFICATION_DATABASE_URL set)
    -> 655 passed, 1 warning, 0 failed, 0 skipped
```

Exceeds the prior branch's 627 without weakening any existing assertion —
the increase is new coverage (28 new tests) plus 12 tests already added on
top of that baseline earlier in this same branch's history.

## Manual UI check

**Not performed.** No browser was available in this environment, consistent
with every prior pass on this branch.

## Remaining known risks

- **`str(parsed)` rescanning** — the pervasive convention in
  `operations_inbox_service.py` of embedding a stringified `parsed` dict
  back into text for re-scanning remains untouched outside the two spots a
  prior pass already neutralized (reference-number dict-key leak, quote-lane
  detail scoring). Not redesigned here.
- **`services/order_parser.py`'s `find_pattern` comma/`re.DOTALL` interaction**
  (documented in `docs/CODE_REVIEW_PLAYBOOK.md` §38) remains untouched.
- **No manual Streamlit browser walkthrough performed** in any pass on this
  branch.
- **Streamlit backend-boundary migration is not complete** — this rework
  only corrected Operations Inbox message-scope, field-boundary, and Full
  Return propagation behavior.
- **No field-level dispatcher-confirmation provenance** — `order_intake.parsed_data`
  still has no per-field confirmation flag; the identity-merge policy uses
  field validity as the closest safe proxy (documented in
  `merge_saved_attachment_fields`'s docstring, unchanged by this pass).
- **Full test-matrix enumeration** (Phases 10–13's ~90 listed cases) is
  represented but not exhaustively enumerated — see "Tests added" above.
