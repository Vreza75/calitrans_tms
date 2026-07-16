# Communications Engine — Phase 1: Provider-Agnostic Foundation

## Context

The user requested a broad "Communications Hub" feature: a provider-based
architecture (Motive for driver messaging, Email, Twilio, WhatsApp, internal
notifications) with a common interface, a new "Communications" tab in every
Load Workspace, automatic status-based driver messages, an AI suggestion
assistant, a combined conversation timeline, and an admin settings page.

That request spans several independent subsystems and is too large for one
spec. This document covers only **Phase 1: the provider-agnostic foundation**
— the piece everything else depends on, and the only piece that requires no
external Motive API access (confirmed not yet available).

Build order agreed with the user, for reference (each future phase gets its
own spec):

1. **This spec** — provider-agnostic foundation + read-only combined timeline
2. Motive provider adapter (blocked on Motive API credentials/docs)
3. Load Workspace "Communications" tab becomes the primary send/reply surface
4. Auto-generated status-based messages + AI assistant (translate, summarize,
   suggested replies)
5. Admin settings page (provider/automation toggles)
6. Future: attachments, WhatsApp, GPS, voice notes

### Existing state (confirmed by codebase search)

This codebase already has more of the "unified log" pattern than the
original request assumed — this phase formalizes what's already emerging
rather than building from scratch:

- **`dispatch_messages` table** (`database/schema.sql:135-144`) is already a
  de-facto unified log for driver/internal communications. Every driver
  dispatch message, the newly-merged Twilio SMS
  (`services/driver_sms_service.py`), customer-status quick-update emails
  (`services/customer_status_email_service.py`), and internal notes all flow
  through one function, `_insert_dispatch_message()`
  (`services/dispatch_data_service.py:69-82`), keyed by `load_id`.
- **A "Send via Motive" button already exists**, disabled, in
  `pages_app/dispatch_board.py:421-428` — placeholder for this work.
- **Driver roster already captures `motive_id` / `motive_password`**
  (`pages_app/admin.py:368-369`).
- **A second, separate log already exists**: `load_communications`
  (`database/operations_email_workflow_migration.sql:105-117`), written by
  `services/communication_service.py`, used by the Gmail Operations Inbox
  (case/intake-linked customer emails, keyed by `load_id`/`intake_id`/
  `case_id`/`conversation_key`).

Per CLAUDE.md, the Gmail Operations Inbox and its `load_communications`
table must not be broken or restructured as part of this work. This phase
therefore does **not** merge the two tables — see "Data merge decision"
below.

## Architecture overview

```
services/communications/
├── __init__.py
├── base.py                    # CommunicationProvider Protocol + shared types
├── email_provider.py          # thin wrapper around services/email_client.py
├── twilio_provider.py         # thin wrapper around services/driver_sms_service.py
├── motive_provider.py         # stub — raises NotConfiguredError until Phase 2
└── communications_service.py  # routes send_message() by channel; get_load_timeline()
```

### Provider interface — `typing.Protocol`, not a class hierarchy

This codebase's service modules are consistently flat functions
(`driver_sms_service.py`, `driver_roster_service.py`,
`dispatch_data_service.py` — no classes except `DispatchDatabaseClient`).
The provider interface follows that convention: a `typing.Protocol` defines
the expected shape structurally, and each provider is a plain module of
functions, not a class. No inheritance is required; a new provider module
just needs functions matching the Protocol's signatures.

```python
# services/communications/base.py
from typing import Protocol, TypedDict

class SendResult(TypedDict):
    success: bool
    provider_message_id: str | None
    error: str | None

class CommunicationProvider(Protocol):
    def send_message(self, recipient: str, body: str, **kwargs) -> SendResult: ...
    def get_status(self, provider_message_id: str) -> str: ...
    def get_delivery_receipts(self, provider_message_id: str) -> dict: ...
```

`get_conversation()` / `mark_read()` / `receive_message()` from the original
request are inbound/bidirectional concerns. Email and Twilio don't support
fetching an inbound conversation in this codebase today (Gmail replies flow
through the separate Operations Inbox sync, not this Protocol). Phase 1
does **not** add these three methods to the Protocol — adding methods no
current provider implements would just create dead interface surface.
They're deferred to Phase 2 (Motive), where they're actually needed and can
be designed against Motive's real API shape.

### Database — extend `dispatch_messages` in place

Recommended and confirmed with the user: extend the existing table rather
than create a new one or rename it. Lower risk, keeps every existing call
site working, matches CLAUDE.md's "use one canonical" rule.

```sql
-- database/communications_foundation_migration.sql
alter table dispatch_messages add column if not exists provider text not null default 'internal';
alter table dispatch_messages add column if not exists delivery_status text;
alter table dispatch_messages add column if not exists read_status text;
alter table dispatch_messages add column if not exists attachments jsonb;
alter table dispatch_messages add column if not exists metadata jsonb;
alter table dispatch_messages add column if not exists provider_message_id text;
```

Idempotent (`if not exists` on every column), additive only, no data
migration, no rewrite of existing rows (they default to `provider =
'internal'`, which is accurate for their historical meaning).

`_insert_dispatch_message()` gains an optional `provider: str = "internal"`
keyword argument. Every existing call site (`dispatch_board.py`,
`orders_management.py`, `customer_status_email_service.py`) keeps working
unchanged with the default. The Twilio SMS call site in
`orders_management.py` (`"driver_dispatch_sms"` message type) is updated to
pass `provider="twilio"`.

### Data merge decision: read-time union, not physical merge

Confirmed with the user. `dispatch_messages` and `load_communications` stay
as separate physical tables — merging them would require touching
`operations_inbox_service.py` and `operations_case_service.py`, which
CLAUDE.md explicitly flags as sensitive and which the Operations Inbox
depends on for case/intake linkage. Instead:

```python
# services/communications/communications_service.py
def get_load_timeline(load_id: int) -> pd.DataFrame:
    """Read-only union of dispatch_messages + load_communications for one
    load, normalized to (created_at, direction, channel, party, message_body),
    sorted newest first. Purely additive — no writes, no schema change to
    load_communications."""
```

Both source queries already exist in similar shape
(`_read_dispatch_messages` in `dispatch_data_service.py`); this function
adds a second query against `load_communications` filtered by `load_id`,
normalizes both DataFrames to the same column set, concatenates, and sorts.
Wrapped in the same try/except-return-empty-DataFrame pattern already used
by every other `_read_*` function in `dispatch_data_service.py`.

## UI — one new tab, nothing else changes

Load Workspace tabs today (`pages_app/dispatch_board.py:251-265`):
`Dispatch Details | [Port Sync/PIN] | Status Update | Timeline | Driver
Notes/Text | Customer Notes | Notes | Documents | Billing`.

Phase 1 adds one new tab, **"Communications"**, immediately after
"Timeline":

```python
tab_labels += ["Timeline", "Communications", "Driver Notes/Text", ...]
...
comms_tab = next(tab_iter)
...
with comms_tab:
    st.markdown("### Communications")
    timeline_df = get_load_timeline(load_id)
    if timeline_df.empty:
        st.info("No communications recorded yet.")
    else:
        st.dataframe(timeline_df, use_container_width=True, hide_index=True)
```

This mirrors the existing "Timeline" tab's own rendering pattern
(`dispatch_board.py:330-336`) exactly. Read-only: no send actions live here
in Phase 1. Sending continues to happen in the existing "Driver Notes/Text"
and "Customer Notes" tabs, unchanged — Phase 3 is what turns this tab into
the primary send/reply surface.

## Explicitly out of scope for Phase 1

- Real Motive API integration (send/receive, status, receipts) — Phase 2,
  blocked on API access.
- Any UI to send a message from the new Communications tab.
- AI-generated suggestions, translation, summarization — Phase 4.
- Attachments (Port PIN, Ticket, Delivery Order, appointment, photos, PDFs).
- WhatsApp provider.
- Admin settings page for enabling/disabling providers or automations —
  Phase 5.
- Physical merge of `dispatch_messages` and `load_communications` into one
  table.
- Changes to the Gmail Operations Inbox's own email sync, case, or intake
  logic.

## Testing plan

- `tests/test_communications_service.py`: unit tests for
  `get_load_timeline()` using fixture DataFrames (mocking `read_df`) —
  merges both sources correctly, sorts newest-first, handles one or both
  sources empty, handles a load with no communications at all.
- `tests/test_communications_providers.py`: for each of
  `email_provider`, `twilio_provider`, `motive_provider` — assert the
  module exposes `send_message`, `get_status`, `get_delivery_receipts` with
  signatures matching the `CommunicationProvider` Protocol. `motive_provider`
  additionally asserts `send_message()` raises/returns a clear
  "not configured" result rather than silently no-op-ing.
- Regression: full existing suite must continue to pass unchanged with the
  new `provider` column defaulting to `'internal'` — no existing call
  site's behavior changes.
- Manual UI smoke test: run Streamlit, open a Load Workspace for a load with
  both a driver text (from the Ready to Dispatch SMS work) and a customer
  status email on record, open the new "Communications" tab, confirm both
  appear in one combined, time-sorted list.
