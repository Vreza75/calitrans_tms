# Send Dispatch Text on "Mark Ready to Dispatch"

## Context

The Orders Management "Ready to Dispatch" panel (`_render_ready_to_dispatch_panel`
in `pages_app/orders_management.py`, built earlier this branch) already shows a
live preview of the driver dispatch message and looks up the driver's phone
from the existing Drivers roster (`services/driver_roster_service.py`), but
the phone number was purely informational — nothing sends the message.

This spec adds: clicking "Mark Ready to Dispatch" actually sends that message
as a text to the driver's phone via Twilio, before updating the load's
Driver/Truck/Chassis/Status fields.

No existing SMS/texting integration exists anywhere in this codebase
(confirmed by search). Outbound email already exists
(`services/customer_status_email_service.py`, direct SMTP via
`config.get_secret` credentials) and establishes the credential-handling
pattern this follows, but SMS requires a different transport entirely.

## Provider and dependency choice

Twilio, called via the REST API directly over `requests` (already a project
dependency — `requirements.txt`) rather than adding the `twilio` PyPI SDK.
A single `POST` to `https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json`
with HTTP Basic Auth (`AccountSid` / `AuthToken`) and form fields `To`, `From`,
`Body` is the entire integration surface — adding a full SDK dependency for
one API call is unnecessary weight.

**New secrets** (via the existing `config.get_secret` pattern, never committed):
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER` — the Twilio phone number messages are sent from, E.164 format

## New module: `services/driver_sms_service.py`

Mirrors the pure-function-plus-thin-I/O-wrapper split already established by
`build_search_blob` (services/dispatch_workflow_service.py) and
`find_driver_in_roster` (services/driver_roster_service.py) earlier this branch.

### `format_phone_e164(phone: str) -> str | None`

Pure function, no I/O, fully unit-testable.

- Strips all non-digit characters.
- If the result is exactly 10 digits, returns `f"+1{digits}"`.
- If the result is 11 digits and starts with `1`, returns `f"+{digits}"`.
- If the input is already in `+1XXXXXXXXXX` form (11 digits after stripping
  the leading `+`), returns it normalized the same way.
- Returns `None` for anything else (blank, too short, too long, obviously
  not a plausible US number) — the caller treats `None` as "cannot send,
  show an error," never crashes or silently drops the message.

This assumes US/Canada numbers (`+1`), consistent with CaliTrans being a
Houston-area drayage company with a US driver roster. Not internationalized;
out of scope for this spec.

### `send_sms(to_phone: str, message: str) -> tuple[bool, str]`

Thin I/O wrapper. Reads `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` /
`TWILIO_FROM_NUMBER` via `config.get_secret`. POSTs to Twilio's Messages API.

- Returns `(True, message_sid)` on a 2xx response.
- Returns `(False, error_description)` on any failure (missing secrets, HTTP
  error, network error, non-2xx Twilio response) — never raises out of this
  function; every failure path is a clean `(False, reason)` so the caller can
  show it to the dispatcher without a stack trace.

Not unit tested directly (network I/O against a paid, real-world SMS
provider) — automated tests must never call the real Twilio API, since that
would send an actual text to a real phone number on every test run. Verified
manually during UAT using a phone number the user controls. This is a
deliberate departure from `list_active_drivers` (Task 2 of the prior plan),
which was safely smoke-tested live because it's a read-only DB query with no
external side effect.

## Panel changes (`_render_ready_to_dispatch_panel`)

**Driver Phone becomes required.** Previously optional (informational only);
now the "Mark Ready to Dispatch" button is disabled unless Driver Name, Truck
Assigned, Chassis, **and** Driver Phone are all non-blank.

**On click**, in this order:

1. Normalize the phone field via `format_phone_e164`. If it returns `None`,
   show `st.error("...")` naming the problem, do not send anything, do not
   change the load's status. The dispatcher fixes the phone number and
   clicks again.
2. Call `send_sms(normalized_phone, edited_message)`, where `edited_message`
   is the **captured return value** of the existing `st.text_area(...)` call
   that currently displays `generated_message`. Today that call's return
   value is discarded (`st.text_area(...)` is called but not assigned), so
   any edits the dispatcher makes to the previewed message are silently
   lost. This spec fixes that by capturing it (`edited_message =
   st.text_area(...)`), mirroring the exact pattern the Dispatch Board's own
   load workspace already uses for the same message (`edited_message =
   st.text_area("Dispatch Message", value=generated_message, ...)` in
   `pages_app/dispatch_board.py`) — so what's sent is whatever the
   dispatcher actually sees and can edit, not a second, freshly-regenerated
   copy of the message.
3. **If `send_sms` succeeds:**
   - Log the sent message via the existing `_insert_dispatch_message(load_id,
     "driver_dispatch_sms", "outbound", normalized_phone, message)` — the
     same audit trail (`dispatch_messages` table) the Dispatch Board's load
     workspace already writes to for `driver_dispatch_message` /
     `driver_dispatch_message_copy_ready`, so all driver communication stays
     in one place regardless of which page sent it.
   - Update the load: `DispatchDatabaseClient().update_row_fields(...)` with
     Driver Name / Truck Assigned / Chassis / Status = "Ready to Dispatch" /
     Dispatcher Notes — same payload as today, unchanged.
   - Refresh and show `st.success("Text sent to {driver_name} and load
     marked Ready to Dispatch.")`.
4. **If `send_sms` fails:** show `st.error` with the returned reason. Do
   **not** update the load's status or fields, and do not log a message in
   `dispatch_messages` (a failed send is not a sent message — nothing to
   audit as sent). The button remains clickable so the dispatcher can retry
   after fixing whatever's wrong (bad number, provider outage).

Phone is still never persisted to `loads` — it continues to come from the
roster lookup each time the panel renders, exactly as before this spec.

## Explicitly out of scope

- No international phone number support (US/Canada `+1` only).
- No retry/backoff logic for transient Twilio failures — a failed send is
  surfaced to the dispatcher, who manually retries by clicking again.
- No opt-out/STOP compliance handling (Twilio's own carrier-level STOP
  handling still applies at the account level; this spec doesn't add
  application-level suppression list logic).
- No changes to the Dispatch Board's own existing driver-message UI.

## Testing plan

- `tests/test_driver_sms_service.py`: unit tests for `format_phone_e164`
  covering: plain 10-digit string, dashes/parens/spaces formatting, already
  E.164, 11-digit with leading 1, too short, too long, non-numeric/blank/None
  input. `send_sms` is not unit tested (would require calling the real
  Twilio API); its error-handling paths (missing secrets, non-2xx response)
  are reviewed by code inspection instead.
- Manual acceptance test (requires a real Twilio account and a phone number
  the user controls to receive the test text): open the Ready to Dispatch
  panel for a test order, confirm the button is disabled until phone is
  filled in, fill in an invalid phone and confirm the error path (no send,
  no status change), fill in a valid phone you control and click Mark Ready
  to Dispatch, confirm the text arrives with the expected message content,
  confirm the load's status flipped to Ready to Dispatch and the message
  appears in the load's Driver Communication Thread on the Dispatch Board.
