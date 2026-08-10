# Communications Engine — Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the provider-agnostic communications foundation (schema extension, `typing.Protocol` provider interface, Email/Twilio/Motive-stub adapters, a read-only combined timeline) so future phases (real Motive integration, AI assistant, admin settings) can add providers without touching existing code.

**Architecture:** Extend the existing `dispatch_messages` table in place with provider/delivery/read/attachment columns (idempotent migration, gated by `column_exists`). Define a `typing.Protocol` (`send_message`/`get_status`/`get_delivery_receipts`) that three flat-function provider modules (`email_provider.py`, `twilio_provider.py`, `motive_provider.py`) conform to structurally — no classes, matching this codebase's existing service style. A thin `communications_service.py` routes `send_message()` by channel and exposes `get_load_timeline()`, which reads `dispatch_messages` (driver/internal) and `load_communications` (Gmail Operations Inbox customer email) separately and merges them in Python. A new read-only "Communications" tab in the Load Workspace (`pages_app/dispatch_board.py`) renders that timeline.

**Tech Stack:** Python, Streamlit, PostgreSQL (via existing `db_client.execute`/`read_df`/`column_exists`), pandas, pytest, `requests` (already a dependency — used for the Twilio status lookup, same pattern as the existing `send_sms`).

## Global Constraints

- Extend `dispatch_messages` in place. Do not create a new table, do not rename it. (User decision, confirmed during brainstorming.)
- `dispatch_messages` and `load_communications` are combined only at read-time, in Python. Do not physically merge them or touch `operations_inbox_service.py` / `operations_case_service.py`. (User decision — CLAUDE.md flags these files as sensitive and requires not breaking the Gmail Operations Inbox.)
- Provider interface is a `typing.Protocol` plus flat function modules. No provider classes, no inheritance. (User decision, matches existing `driver_sms_service.py` / `driver_roster_service.py` / `dispatch_data_service.py` style.)
- No Streamlit imports in any `services/` module (CLAUDE.md architecture rule) — the schema-migration guard uses `column_exists()` only, no `st.session_state` caching.
- Every existing call site of `_insert_dispatch_message()` keeps working unchanged — the new `provider` parameter defaults to `"internal"`.
- No UI to send a message from the new "Communications" tab in this phase — it is read-only. Sending still happens via the existing "Driver Notes/Text" and "Customer Notes" tabs.
- No real Motive API calls — `motive_provider.py` is a stub that returns a clear "not configured" failure result and never raises.
- Standard verification commands for every task: `.venv/Scripts/python.exe -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core` and `.venv/Scripts/python.exe -m pytest -q`.

---

### Task 1: Idempotent schema migration on `dispatch_messages`

**Files:**
- Modify: `services/dispatch_data_service.py`
- Test: `tests/test_communications_schema.py`

**Interfaces:**
- Consumes: `column_exists`, `execute` from `db_client` (already used elsewhere in this codebase, e.g. `services/operations_case_service.py`).
- Produces: `ensure_communications_schema() -> None`, callable by any later task with no arguments, safe to call repeatedly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_communications_schema.py`:

```python
"""ensure_communications_schema() extends dispatch_messages with the
columns the Communications Engine's provider-agnostic layer needs. Hits
the real dev database, same as tests/test_db_client_column_exists.py —
column_exists() is a real round trip and the added columns are additive
and idempotent, so re-running this test (or the app) is always safe.
"""
from db_client import column_exists
from services.dispatch_data_service import ensure_communications_schema

EXPECTED_COLUMNS = [
    "provider",
    "delivery_status",
    "read_status",
    "attachments",
    "metadata",
    "provider_message_id",
]


def test_ensure_communications_schema_adds_expected_columns():
    ensure_communications_schema()
    for column in EXPECTED_COLUMNS:
        assert column_exists("dispatch_messages", column) is True


def test_ensure_communications_schema_is_idempotent():
    ensure_communications_schema()
    ensure_communications_schema()
    assert column_exists("dispatch_messages", "provider") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_communications_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_communications_schema'`

- [ ] **Step 3: Write minimal implementation**

In `services/dispatch_data_service.py`, change the import line at the top from:

```python
from db_client import DispatchDatabaseClient, execute, read_df
```

to:

```python
from db_client import DispatchDatabaseClient, column_exists, execute, read_df
```

Then add this function anywhere above `_insert_dispatch_message`:

```python
def ensure_communications_schema() -> None:
    """Idempotently extends dispatch_messages with the columns the
    Communications Engine's provider-agnostic layer needs (provider,
    delivery/read status, attachments, metadata, provider message id).
    Safe to call on every insert/read: column_exists() is a single cheap
    round trip and this app's traffic (~10-20 drivers, one dispatcher)
    never makes that a bottleneck. No st.session_state caching here —
    services/ modules must not import streamlit (CLAUDE.md)."""
    if column_exists("dispatch_messages", "provider"):
        return
    execute("alter table dispatch_messages add column if not exists provider text not null default 'internal'")
    execute("alter table dispatch_messages add column if not exists delivery_status text")
    execute("alter table dispatch_messages add column if not exists read_status text")
    execute("alter table dispatch_messages add column if not exists attachments jsonb")
    execute("alter table dispatch_messages add column if not exists metadata jsonb")
    execute("alter table dispatch_messages add column if not exists provider_message_id text")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_communications_schema.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_data_service.py tests/test_communications_schema.py
git commit -m "feat: add idempotent communications schema migration on dispatch_messages"
```

---

### Task 2: `_insert_dispatch_message` gains a `provider` parameter

**Files:**
- Modify: `services/dispatch_data_service.py`
- Modify: `pages_app/orders_management.py` (~line 759, the Twilio SMS call site)

**Interfaces:**
- Consumes: `ensure_communications_schema()` from Task 1.
- Produces: `_insert_dispatch_message(load_id: int, message_type: str, direction: str, recipient: str, message_body: str, provider: str = "internal") -> None` — every existing positional call site keeps working unchanged.

- [ ] **Step 1: Update `_insert_dispatch_message`**

In `services/dispatch_data_service.py`, replace:

```python
def _insert_dispatch_message(load_id: int, message_type: str, direction: str, recipient: str, message_body: str) -> None:
    execute(
        """
        insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, sent_by)
        values (:load_id, :message_type, :direction, :recipient, :message_body, 'dispatcher')
        """,
        {
            "load_id": load_id,
            "message_type": message_type,
            "direction": direction,
            "recipient": recipient or None,
            "message_body": message_body,
        },
    )
```

with:

```python
def _insert_dispatch_message(load_id: int, message_type: str, direction: str, recipient: str, message_body: str, provider: str = "internal") -> None:
    ensure_communications_schema()
    execute(
        """
        insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, sent_by, provider)
        values (:load_id, :message_type, :direction, :recipient, :message_body, 'dispatcher', :provider)
        """,
        {
            "load_id": load_id,
            "message_type": message_type,
            "direction": direction,
            "recipient": recipient or None,
            "message_body": message_body,
            "provider": provider,
        },
    )
```

- [ ] **Step 2: Update the Twilio SMS call site**

In `pages_app/orders_management.py`, find (around line 759):

```python
                _insert_dispatch_message(
                    selected_row_id,
                    "driver_dispatch_sms",
                    "outbound",
                    normalized_phone,
                    edited_message,
                )
```

Replace with:

```python
                _insert_dispatch_message(
                    selected_row_id,
                    "driver_dispatch_sms",
                    "outbound",
                    normalized_phone,
                    edited_message,
                    provider="twilio",
                )
```

- [ ] **Step 3: Compile check**

Run: `.venv/Scripts/python.exe -m compileall -q pages_app services`
Expected: no output (success)

- [ ] **Step 4: Run full suite to confirm no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass, same count as before plus the 2 from Task 1 (no test exists yet for `_insert_dispatch_message` itself — it wasn't unit tested before this change either, consistent with `send_sms` in `services/driver_sms_service.py` not being unit tested for its real I/O; reviewed by inspection instead).

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_data_service.py pages_app/orders_management.py
git commit -m "feat: tag dispatch_messages rows with their sending provider"
```

---

### Task 3: Provider Protocol and shared types

**Files:**
- Create: `services/communications/__init__.py`
- Create: `services/communications/base.py`

**Interfaces:**
- Produces: `SendResult` (TypedDict with `success: bool`, `provider_message_id: str | None`, `error: str | None`) and `CommunicationProvider` (Protocol with `send_message(recipient, body, **kwargs) -> SendResult`, `get_status(provider_message_id) -> str`, `get_delivery_receipts(provider_message_id) -> dict`), used by every provider module in Tasks 4-6 and the router in Task 7.

- [ ] **Step 1: Create the package and base module**

Create `services/communications/__init__.py` (empty file — marks the directory as a package).

Create `services/communications/base.py`:

```python
from __future__ import annotations

from typing import Protocol, TypedDict


class SendResult(TypedDict):
    success: bool
    provider_message_id: str | None
    error: str | None


class CommunicationProvider(Protocol):
    """Structural interface every communications provider module conforms
    to. No inheritance required — a provider module just needs functions
    matching these signatures (see services/communications/email_provider.py,
    twilio_provider.py, motive_provider.py). This lets a future provider
    (WhatsApp, Slack, etc.) be added without changing communications_service.py
    or anything that calls it."""

    def send_message(self, recipient: str, body: str, **kwargs) -> SendResult: ...

    def get_status(self, provider_message_id: str) -> str: ...

    def get_delivery_receipts(self, provider_message_id: str) -> dict: ...
```

- [ ] **Step 2: Compile check (no test — this is a type-only module)**

Run: `.venv/Scripts/python.exe -m compileall -q services`
Expected: no output (success)

- [ ] **Step 3: Commit**

```bash
git add services/communications/__init__.py services/communications/base.py
git commit -m "feat: add CommunicationProvider Protocol for the communications engine"
```

---

### Task 4: Email provider adapter

**Files:**
- Create: `services/communications/email_provider.py`
- Test: `tests/test_email_provider.py`

**Interfaces:**
- Consumes: `_send_smtp_email(to_email, subject, body, from_email="", cc_email="")` from `services/customer_status_email_service.py` (existing, raises on failure).
- Produces: `send_message`, `get_status`, `get_delivery_receipts` conforming to `CommunicationProvider` (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_provider.py`:

```python
from services.communications import email_provider


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(email_provider, "_send_smtp_email", lambda *a, **k: None)
    result = email_provider.send_message("customer@example.com", "Hello", subject="Update")
    assert result == {"success": True, "provider_message_id": None, "error": None}


def test_send_message_failure(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("Missing email settings.")

    monkeypatch.setattr(email_provider, "_send_smtp_email", _boom)
    result = email_provider.send_message("customer@example.com", "Hello", subject="Update")
    assert result["success"] is False
    assert "Missing email settings" in result["error"]


def test_get_status_returns_unknown():
    assert email_provider.get_status("anything") == "unknown"


def test_get_delivery_receipts_returns_empty_dict():
    assert email_provider.get_delivery_receipts("anything") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_email_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.communications.email_provider'`

- [ ] **Step 3: Write minimal implementation**

Create `services/communications/email_provider.py`:

```python
from __future__ import annotations

from services.communications.base import SendResult
from services.customer_status_email_service import _send_smtp_email


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    """Email provider adapter. Requires `subject` in kwargs — plain SMTP
    has no subject-less send path. `from_email`/`cc_email` are optional
    passthroughs to `_send_smtp_email`."""
    subject = kwargs.get("subject", "")
    from_email = kwargs.get("from_email", "")
    cc_email = kwargs.get("cc_email", "")
    try:
        _send_smtp_email(recipient, subject, body, from_email=from_email, cc_email=cc_email)
        return {"success": True, "provider_message_id": None, "error": None}
    except Exception as exc:
        return {"success": False, "provider_message_id": None, "error": str(exc)}


def get_status(provider_message_id: str) -> str:
    """Plain SMTP has no post-send delivery tracking in this codebase."""
    return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    """Plain SMTP has no delivery-receipt API in this codebase."""
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_email_provider.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/communications/email_provider.py tests/test_email_provider.py
git commit -m "feat: add email provider adapter for the communications engine"
```

---

### Task 5: Twilio provider adapter

**Files:**
- Create: `services/communications/twilio_provider.py`
- Test: `tests/test_twilio_provider.py`

**Interfaces:**
- Consumes: `format_phone_e164`, `send_sms`, `TWILIO_API_BASE` from `services/driver_sms_service.py` (existing); `get_secret` from `config` (existing).
- Produces: `send_message`, `get_status`, `get_delivery_receipts` conforming to `CommunicationProvider` (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_twilio_provider.py`:

```python
from services.communications import twilio_provider


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_send_message_invalid_phone_returns_failure():
    result = twilio_provider.send_message("not a phone", "hello")
    assert result["success"] is False
    assert "not a valid phone number" in result["error"]


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "format_phone_e164", lambda p: "+18325551234")
    monkeypatch.setattr(twilio_provider, "send_sms", lambda phone, body: (True, "SM123"))
    result = twilio_provider.send_message("8325551234", "hello")
    assert result == {"success": True, "provider_message_id": "SM123", "error": None}


def test_send_message_failure(monkeypatch):
    monkeypatch.setattr(twilio_provider, "format_phone_e164", lambda p: "+18325551234")
    monkeypatch.setattr(twilio_provider, "send_sms", lambda phone, body: (False, "Twilio error (500): boom"))
    result = twilio_provider.send_message("8325551234", "hello")
    assert result["success"] is False
    assert result["error"] == "Twilio error (500): boom"


def test_get_status_missing_secrets_returns_unknown(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: None)
    assert twilio_provider.get_status("SM123") == "unknown"


def test_get_status_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: "fake_value")
    monkeypatch.setattr(twilio_provider.requests, "get", lambda *a, **k: _FakeResponse(200, {"status": "delivered"}))
    assert twilio_provider.get_status("SM123") == "delivered"


def test_get_delivery_receipts_missing_secrets_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: None)
    assert twilio_provider.get_delivery_receipts("SM123") == {}


def test_get_delivery_receipts_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: "fake_value")
    payload = {"status": "delivered", "error_code": None, "error_message": None, "date_sent": "2026-07-16"}
    monkeypatch.setattr(twilio_provider.requests, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = twilio_provider.get_delivery_receipts("SM123")
    assert result["status"] == "delivered"
    assert result["date_sent"] == "2026-07-16"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_twilio_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.communications.twilio_provider'`

- [ ] **Step 3: Write minimal implementation**

Create `services/communications/twilio_provider.py`:

```python
from __future__ import annotations

import requests

from config import get_secret
from services.communications.base import SendResult
from services.driver_sms_service import TWILIO_API_BASE, format_phone_e164, send_sms


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    normalized = format_phone_e164(recipient)
    if not normalized:
        return {
            "success": False,
            "provider_message_id": None,
            "error": f"'{recipient}' is not a valid phone number.",
        }
    sent, sid_or_error = send_sms(normalized, body)
    if sent:
        return {"success": True, "provider_message_id": sid_or_error, "error": None}
    return {"success": False, "provider_message_id": None, "error": sid_or_error}


def get_status(provider_message_id: str) -> str:
    """Looks up a previously sent message's delivery status via Twilio's
    Messages API. Returns 'unknown' on any failure or missing config —
    never raises, mirroring send_sms's error-handling convention."""
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not provider_message_id:
        return "unknown"
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages/{provider_message_id}.json"
    try:
        response = requests.get(url, auth=(account_sid, auth_token), timeout=15)
        if response.status_code != 200:
            return "unknown"
        return response.json().get("status", "unknown")
    except requests.RequestException:
        return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    """Returns Twilio's status/error fields for a sent message, or {} on
    any failure or missing config — never raises."""
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not provider_message_id:
        return {}
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages/{provider_message_id}.json"
    try:
        response = requests.get(url, auth=(account_sid, auth_token), timeout=15)
        if response.status_code != 200:
            return {}
        data = response.json()
        return {
            "status": data.get("status"),
            "error_code": data.get("error_code"),
            "error_message": data.get("error_message"),
            "date_sent": data.get("date_sent"),
        }
    except requests.RequestException:
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_twilio_provider.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add services/communications/twilio_provider.py tests/test_twilio_provider.py
git commit -m "feat: add Twilio provider adapter for the communications engine"
```

---

### Task 6: Motive provider stub

**Files:**
- Create: `services/communications/motive_provider.py`
- Test: `tests/test_motive_provider.py`

**Interfaces:**
- Consumes: `SendResult` from Task 3.
- Produces: `send_message`, `get_status`, `get_delivery_receipts` conforming to `CommunicationProvider` — all return clean "not configured" results, never raise, so the router (Task 7) needs no special-case handling for Motive vs. any other provider.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_motive_provider.py`:

```python
from services.communications import motive_provider


def test_send_message_returns_not_configured_failure():
    result = motive_provider.send_message("driver-1", "hello")
    assert result["success"] is False
    assert result["provider_message_id"] is None
    assert "not yet configured" in result["error"]


def test_get_status_returns_unknown():
    assert motive_provider.get_status("anything") == "unknown"


def test_get_delivery_receipts_returns_empty_dict():
    assert motive_provider.get_delivery_receipts("anything") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_motive_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.communications.motive_provider'`

- [ ] **Step 3: Write minimal implementation**

Create `services/communications/motive_provider.py`:

```python
from __future__ import annotations

from services.communications.base import SendResult

_NOT_CONFIGURED_ERROR = (
    "Motive is not yet configured — no API credentials available yet. "
    "See Phase 2 of the Communications Engine plan "
    "(docs/superpowers/specs/2026-07-16-communications-engine-foundation-design.md)."
)


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    return {"success": False, "provider_message_id": None, "error": _NOT_CONFIGURED_ERROR}


def get_status(provider_message_id: str) -> str:
    return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_motive_provider.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add services/communications/motive_provider.py tests/test_motive_provider.py
git commit -m "feat: add Motive provider stub for the communications engine"
```

---

### Task 7: Communications service — router and combined timeline

**Files:**
- Create: `services/communications/communications_service.py`
- Test: `tests/test_communications_service.py`

**Interfaces:**
- Consumes: `email_provider`, `twilio_provider`, `motive_provider` modules (Tasks 4-6); `ensure_communications_schema` from `services/dispatch_data_service.py` (Task 1); `read_df` from `db_client`.
- Produces: `send_message(channel: str, recipient: str, body: str, **kwargs) -> SendResult` and `get_load_timeline(load_id: int) -> pd.DataFrame` with columns `["created_at", "direction", "channel", "party", "message_body"]`, sorted by `created_at` descending. Task 8 (UI) consumes `get_load_timeline`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_communications_service.py`:

```python
import pandas as pd

from services.communications import communications_service as cs


def _fake_read_df(dispatch_rows, customer_rows):
    def _read_df(sql, params=None):
        if "dispatch_messages" in sql:
            return pd.DataFrame(dispatch_rows)
        if "load_communications" in sql:
            return pd.DataFrame(customer_rows)
        raise AssertionError(f"unexpected query: {sql}")

    return _read_df


def test_merges_both_sources_sorted_newest_first(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)
    dispatch_rows = [
        {
            "created_at": "2026-07-16 10:00:00",
            "direction": "outbound",
            "channel": "twilio",
            "party": "+18325551234",
            "message_body": "Dispatch text",
        },
    ]
    customer_rows = [
        {
            "created_at": "2026-07-16 11:00:00",
            "direction": "inbound",
            "channel": "email",
            "party": "customer@example.com",
            "message_body": "Reply",
        },
    ]
    monkeypatch.setattr(cs, "read_df", _fake_read_df(dispatch_rows, customer_rows))
    result = cs.get_load_timeline(123)
    assert list(result["message_body"]) == ["Reply", "Dispatch text"]


def test_both_sources_empty_returns_empty_df_with_expected_columns(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)
    monkeypatch.setattr(cs, "read_df", _fake_read_df([], []))
    result = cs.get_load_timeline(123)
    assert result.empty
    assert list(result.columns) == ["created_at", "direction", "channel", "party", "message_body"]


def test_dispatch_source_error_falls_back_to_customer_only(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)

    def _read_df(sql, params=None):
        if "dispatch_messages" in sql:
            raise RuntimeError("db down")
        return pd.DataFrame(
            [
                {
                    "created_at": "2026-07-16 11:00:00",
                    "direction": "inbound",
                    "channel": "email",
                    "party": "x",
                    "message_body": "y",
                }
            ]
        )

    monkeypatch.setattr(cs, "read_df", _read_df)
    result = cs.get_load_timeline(123)
    assert len(result) == 1
    assert result.iloc[0]["message_body"] == "y"


def test_routes_to_email_provider(monkeypatch):
    monkeypatch.setattr(
        cs.email_provider,
        "send_message",
        lambda r, b, **k: {"success": True, "provider_message_id": None, "error": None},
    )
    result = cs.send_message("email", "customer@example.com", "hi", subject="Update")
    assert result["success"] is True


def test_routes_to_twilio_provider(monkeypatch):
    monkeypatch.setattr(
        cs.twilio_provider,
        "send_message",
        lambda r, b, **k: {"success": True, "provider_message_id": "SM1", "error": None},
    )
    result = cs.send_message("twilio", "8325551234", "hi")
    assert result["provider_message_id"] == "SM1"


def test_unknown_channel_returns_failure_without_raising():
    result = cs.send_message("carrier_pigeon", "recipient", "hi")
    assert result["success"] is False
    assert "Unknown communications channel" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_communications_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.communications.communications_service'`

- [ ] **Step 3: Write minimal implementation**

Create `services/communications/communications_service.py`:

```python
from __future__ import annotations

import pandas as pd

from db_client import read_df
from services.communications import email_provider, motive_provider, twilio_provider
from services.communications.base import SendResult
from services.dispatch_data_service import ensure_communications_schema

_TIMELINE_COLUMNS = ["created_at", "direction", "channel", "party", "message_body"]

_PROVIDERS = {
    "email": email_provider,
    "twilio": twilio_provider,
    "motive": motive_provider,
}


def send_message(channel: str, recipient: str, body: str, **kwargs) -> SendResult:
    """Routes to the provider module matching `channel`. Adding a new
    provider (WhatsApp, Slack, ...) means adding one entry to _PROVIDERS —
    nothing else in this function, or any caller, changes."""
    provider = _PROVIDERS.get(channel)
    if provider is None:
        return {
            "success": False,
            "provider_message_id": None,
            "error": f"Unknown communications channel: {channel!r}",
        }
    return provider.send_message(recipient, body, **kwargs)


def get_load_timeline(load_id: int) -> pd.DataFrame:
    """Read-only combined view of dispatch_messages (driver/internal) and
    load_communications (Gmail Operations Inbox customer email) for one
    load, normalized to a common shape and sorted newest first. Purely
    additive — no writes, no changes to load_communications or the
    Operations Inbox."""
    ensure_communications_schema()

    try:
        dispatch_df = read_df(
            """
            select created_at, direction, coalesce(provider, 'internal') as channel,
                   recipient as party, message_body
            from dispatch_messages
            where load_id = :load_id
            """,
            {"load_id": load_id},
        )
    except Exception:
        dispatch_df = pd.DataFrame(columns=_TIMELINE_COLUMNS)

    try:
        customer_df = read_df(
            """
            select created_at, direction, 'email' as channel, sender as party, message_body
            from load_communications
            where load_id = :load_id
            """,
            {"load_id": load_id},
        )
    except Exception:
        customer_df = pd.DataFrame(columns=_TIMELINE_COLUMNS)

    combined = pd.concat([dispatch_df, customer_df], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=_TIMELINE_COLUMNS)
    return combined.sort_values("created_at", ascending=False).reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_communications_service.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add services/communications/communications_service.py tests/test_communications_service.py
git commit -m "feat: add communications router and combined load timeline"
```

---

### Task 8: "Communications" tab in the Load Workspace

**Files:**
- Modify: `pages_app/dispatch_board.py`

**Interfaces:**
- Consumes: `get_load_timeline(load_id: int) -> pd.DataFrame` from Task 7.

- [ ] **Step 1: Add the import**

In `pages_app/dispatch_board.py`, near the other `services.*` imports, add:

```python
from services.communications.communications_service import get_load_timeline
```

- [ ] **Step 2: Add the new tab label and iterator assignment**

Find (around line 254):

```python
    tab_labels += ["Dispatch Details"]
    if show_port_tab:
        tab_labels.append("Port Sync / PIN")
    tab_labels += ["Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Notes", "Documents", "Billing"]
```

Replace the last line with:

```python
    tab_labels += ["Status Update", "Timeline", "Communications", "Driver Notes/Text", "Customer Notes", "Notes", "Documents", "Billing"]
```

Find (around lines 259-265):

```python
    status_tab = next(tab_iter)
    timeline_tab = next(tab_iter)
    driver_tab = next(tab_iter)
    customer_tab = next(tab_iter)
    notes_tab = next(tab_iter)
    docs_tab = next(tab_iter)
    billing_tab = next(tab_iter)
```

Replace with:

```python
    status_tab = next(tab_iter)
    timeline_tab = next(tab_iter)
    comms_tab = next(tab_iter)
    driver_tab = next(tab_iter)
    customer_tab = next(tab_iter)
    notes_tab = next(tab_iter)
    docs_tab = next(tab_iter)
    billing_tab = next(tab_iter)
```

- [ ] **Step 3: Render the tab**

Find the end of the existing `with timeline_tab:` block (around lines 330-337):

```python
    with timeline_tab:
        st.markdown("### Load Timeline")
        timeline = _read_status_timeline(load_id)
        if timeline.empty:
            st.info("No timeline records yet.")
        else:
            st.dataframe(timeline, use_container_width=True, hide_index=True)

    with driver_tab:
```

Insert a new block between them:

```python
    with timeline_tab:
        st.markdown("### Load Timeline")
        timeline = _read_status_timeline(load_id)
        if timeline.empty:
            st.info("No timeline records yet.")
        else:
            st.dataframe(timeline, use_container_width=True, hide_index=True)

    with comms_tab:
        st.markdown("### Communications")
        st.caption("Combined driver, customer, and internal communication history for this load.")
        comms_timeline = get_load_timeline(load_id)
        if comms_timeline.empty:
            st.info("No communications recorded yet.")
        else:
            st.dataframe(comms_timeline, use_container_width=True, hide_index=True)

    with driver_tab:
```

- [ ] **Step 4: Compile check**

Run: `.venv/Scripts/python.exe -m compileall -q pages_app`
Expected: no output (success)

- [ ] **Step 5: Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (no test file targets Streamlit UI rendering directly, consistent with every other tab in this file — verified manually in Step 6).

- [ ] **Step 6: Manual smoke test**

Run: `.venv/Scripts/python.exe -m streamlit run app.py --server.headless true`

In the browser: open the Dispatch Board, open a Load Workspace for a load that has at least one driver text/dispatch message on record (e.g. one processed through the "Ready to Dispatch" SMS flow), confirm a "Communications" tab appears between "Timeline" and "Driver Notes/Text", and confirm it renders the combined table (or the "No communications recorded yet." message for a load with none). Stop the server after confirming.

- [ ] **Step 7: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "feat: add read-only Communications tab to the Load Workspace"
```

---

### Task 9: Final regression pass

**Files:** none (verification only)

- [ ] **Step 1: Full compile check**

Run: `.venv/Scripts/python.exe -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core`
Expected: no errors

- [ ] **Step 2: Full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (prior full run was 216; this task should show 216 + the ~23 new tests added across Tasks 1, 4, 5, 6, 7)

- [ ] **Step 3: Confirm no leftover debug artifacts**

Run: `git status --short`
Expected: clean tree except for anything intentionally left uncommitted from before this plan started (e.g. unrelated in-progress work stashed/restored earlier this session) — no new untracked files from this plan's work.

## Self-Review

**Spec coverage:** Architecture overview (Task 3), database migration (Task 1), `_insert_dispatch_message` provider tagging (Task 2), email/Twilio/Motive adapters (Tasks 4-6), router + `get_load_timeline` (Task 7), Communications tab (Task 8), explicitly-out-of-scope items (no send UI, no Motive API calls, no physical table merge, no Operations Inbox changes) are honored by omission in every task above. Testing plan items from the spec are each covered by a task's test step.

**Placeholder scan:** No TBD/TODO; every step has complete, runnable code.

**Type consistency:** `SendResult` (Task 3) is used identically in Tasks 4, 5, 6, 7. `get_load_timeline(load_id: int) -> pd.DataFrame` with columns `["created_at", "direction", "channel", "party", "message_body"]` is defined once in Task 7 and consumed as-is in Task 8. `_insert_dispatch_message`'s new `provider` parameter (Task 2) matches the column added in Task 1.
