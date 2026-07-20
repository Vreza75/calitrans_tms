# Send Dispatch Text on Ready to Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a dispatcher clicks "Mark Ready to Dispatch" in Orders Management, actually send the previewed dispatch message as a text to the driver's phone via Twilio, before updating the load's status.

**Architecture:** A new pure-function-plus-thin-I/O-wrapper service module (`services/driver_sms_service.py`) normalizes phone numbers and posts to Twilio's REST API directly over the existing `requests` dependency (no new SDK). `pages_app/orders_management.py`'s `_render_ready_to_dispatch_panel` captures the message preview's edited text (currently silently discarded — a pre-existing bug this plan also fixes), requires phone as a 4th gating field, and on click sends the text before writing the status change, only proceeding to update the load if the send succeeds.

**Tech Stack:** Python 3.14, Streamlit, `requests` (already a dependency), pytest, Twilio REST API (no SDK).

## Global Constraints

- No new PyPI dependency — use `requests` (already in `requirements.txt`) to call Twilio's REST API directly, not the `twilio` SDK.
- New secrets via the existing `config.get_secret` pattern: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`. Never hardcoded, never committed.
- US/Canada `+1` numbers only — no internationalization in this plan.
- Driver Phone becomes a required field (alongside Driver Name/Truck Assigned/Chassis) for the "Mark Ready to Dispatch" button — button stays disabled until all four are filled in.
- If the SMS send fails for any reason, the load's Driver Name/Truck Assigned/Chassis/Status must NOT change, and nothing is logged to `dispatch_messages` — only a successful send results in both the message log and the status update.
- `send_sms` must never raise — every failure path (missing secrets, network error, non-2xx response) returns `(False, reason)`.
- Automated tests must never call the real Twilio API (it would send a real text). Only `format_phone_e164` is unit tested; `send_sms` is verified by code inspection during task review, not by an automated network call.
- The text actually sent is whatever the dispatcher sees/edits in the "Dispatch Message" preview (`edited_message`, the `st.text_area`'s captured return value) — not a second, freshly-regenerated copy.

---

### Task 1: `format_phone_e164` pure phone normalizer

**Files:**
- Create: `services/driver_sms_service.py`
- Test: `tests/test_driver_sms_service.py`

**Interfaces:**
- Produces: `format_phone_e164(phone) -> str | None` — normalizes a free-text US phone string to `+1XXXXXXXXXX`, or `None` if it can't.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_driver_sms_service.py`:

```python
from services.driver_sms_service import format_phone_e164


def test_plain_ten_digit_number():
    assert format_phone_e164("8325552020") == "+18325552020"


def test_dashes_formatting():
    assert format_phone_e164("832-555-2020") == "+18325552020"


def test_parens_and_spaces_formatting():
    assert format_phone_e164("(832) 555-2020") == "+18325552020"


def test_already_e164():
    assert format_phone_e164("+18325552020") == "+18325552020"


def test_eleven_digit_with_leading_one():
    assert format_phone_e164("18325552020") == "+18325552020"


def test_too_short_returns_none():
    assert format_phone_e164("5552020") is None


def test_too_long_returns_none():
    assert format_phone_e164("183255520201234") is None


def test_blank_returns_none():
    assert format_phone_e164("") is None


def test_none_returns_none():
    assert format_phone_e164(None) is None


def test_non_numeric_junk_returns_none():
    assert format_phone_e164("no phone on file") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_driver_sms_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.driver_sms_service'`

- [ ] **Step 3: Write the minimal implementation**

Create `services/driver_sms_service.py`:

```python
from __future__ import annotations

from config import get_secret

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def format_phone_e164(phone) -> str | None:
    """Normalize a free-text US/Canada phone number to +1XXXXXXXXXX.

    Returns None if the input can't produce a plausible 10-digit US number
    — callers must treat None as "cannot send," never guess or truncate.
    """
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())

    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None
```

Note: `send_sms` is added in Task 2, not this task — this task is the pure function only.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_driver_sms_service.py -v`
Expected: `10 passed`

- [ ] **Step 5: Compile check**

Run: `python -m compileall services/driver_sms_service.py tests/test_driver_sms_service.py`
Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add services/driver_sms_service.py tests/test_driver_sms_service.py
git commit -m "$(cat <<'EOF'
feat: add phone number normalizer for driver SMS

Pure format_phone_e164 function, unit tested. send_sms (the Twilio
I/O wrapper) is added in the next commit.
EOF
)"
```

---

### Task 2: `send_sms` Twilio REST wrapper

**Files:**
- Modify: `services/driver_sms_service.py`

**Interfaces:**
- Consumes: `config.get_secret` (existing).
- Produces: `send_sms(to_phone: str, message: str) -> tuple[bool, str]` — `(True, message_sid)` on success, `(False, reason)` on any failure. Never raises.

- [ ] **Step 1: Add the `requests` import and `send_sms` function**

In `services/driver_sms_service.py`, replace:

```python
from __future__ import annotations

from config import get_secret

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
```

with:

```python
from __future__ import annotations

import requests

from config import get_secret

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
```

Then append this function at the end of the file (after `format_phone_e164`):

```python
def send_sms(to_phone: str, message: str) -> tuple[bool, str]:
    """Send `message` to `to_phone` via Twilio. Returns (success, sid_or_error).

    Never raises — every failure (missing secrets, network error, non-2xx
    response) is reported as (False, reason) so the caller can show it to
    the dispatcher without a stack trace.
    """
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    from_number = get_secret("TWILIO_FROM_NUMBER")

    if not account_sid or not auth_token or not from_number:
        return False, "Twilio is not configured (missing TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)."

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"

    try:
        response = requests.post(
            url,
            auth=(account_sid, auth_token),
            data={"To": to_phone, "From": from_number, "Body": message},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach Twilio: {exc}"

    if response.status_code in (200, 201):
        try:
            sid = response.json().get("sid", "")
        except ValueError:
            sid = ""
        return True, sid

    try:
        error_detail = response.json().get("message", response.text)
    except ValueError:
        error_detail = response.text

    return False, f"Twilio error ({response.status_code}): {error_detail}"
```

- [ ] **Step 2: Compile check**

Run: `python -m compileall services/driver_sms_service.py`
Expected: exit code 0.

- [ ] **Step 3: Verify the existing phone-normalizer tests still pass**

Run: `python -m pytest tests/test_driver_sms_service.py -v`
Expected: `10 passed` (unchanged — this task adds no new tests, per the Global Constraint that `send_sms` isn't unit tested against the real API).

- [ ] **Step 4: Manual, no-network sanity check of the missing-secrets path**

This does NOT call the real Twilio API (no secrets are configured in this environment), so it safely exercises exactly the "missing credentials" failure path:

```bash
python -c "
from services.driver_sms_service import send_sms
ok, reason = send_sms('+18325550100', 'test message')
print('ok:', ok)
print('reason:', reason)
assert ok is False
assert 'not configured' in reason
print('PASS: missing-secrets path returns a clean failure, no exception raised')
"
```
Expected: `PASS: missing-secrets path returns a clean failure, no exception raised`

- [ ] **Step 5: Commit**

```bash
git add services/driver_sms_service.py
git commit -m "$(cat <<'EOF'
feat: add Twilio REST wrapper for sending driver dispatch texts

send_sms posts to Twilio's Messages API directly over requests (no
SDK dependency). Never raises — missing secrets, network errors, and
non-2xx responses all return a clean (False, reason) the caller can
show to the dispatcher.
EOF
)"
```

---

### Task 3: Wire sending into the Ready to Dispatch panel

**Files:**
- Modify: `pages_app/orders_management.py:8-20` (imports)
- Modify: `pages_app/orders_management.py:729-769` (message preview + button handler)

**Interfaces:**
- Consumes: `format_phone_e164`, `send_sms` (Tasks 1-2); `_insert_dispatch_message` (services/dispatch_data_service.py, already exists — same function the Dispatch Board already uses for its own driver-message audit trail).

- [ ] **Step 1: Add the new imports**

Replace:

```python
from db_client import DispatchDatabaseClient
from services.dispatch_workflow_service import (
    LOAD_TYPE_TABS,
    _generate_driver_dispatch_message,
    _load_has_pin_or_appointment,
    _load_port_verified,
    _load_requires_port,
    _normalize_load_type_value,
    _status_row_style,
)
from services.driver_roster_service import find_driver_in_roster, list_active_drivers
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter
```

with:

```python
from db_client import DispatchDatabaseClient
from services.dispatch_data_service import _insert_dispatch_message
from services.dispatch_workflow_service import (
    LOAD_TYPE_TABS,
    _generate_driver_dispatch_message,
    _load_has_pin_or_appointment,
    _load_port_verified,
    _load_requires_port,
    _normalize_load_type_value,
    _status_row_style,
)
from services.driver_roster_service import find_driver_in_roster, list_active_drivers
from services.driver_sms_service import format_phone_e164, send_sms
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter
```

- [ ] **Step 2: Capture the message preview's edited value, require phone, and send before updating**

In `_render_ready_to_dispatch_panel`, replace:

```python
    st.markdown("#### Generated Dispatch Message")
    preview_load = selected_load.copy()
    preview_load["Driver Name"] = driver_name
    preview_load["Truck Assigned"] = truck
    preview_load["Chassis"] = chassis
    generated_message = _generate_driver_dispatch_message(preview_load)
    st.text_area(
        "Dispatch Message",
        value=generated_message,
        height=260,
        key=f"{panel_key}_message",
    )
    if phone.strip():
        st.caption(f"Driver phone on file: {phone.strip()}")

    ready_disabled = not (driver_name.strip() and truck.strip() and chassis.strip())
    if st.button(
        "Mark Ready to Dispatch",
        key=f"{panel_key}_mark_ready",
        use_container_width=True,
        disabled=ready_disabled,
    ):
        DispatchDatabaseClient().update_row_fields(
            selected_row_id,
            {
                "Driver Name": driver_name.strip(),
                "Truck Assigned": truck.strip(),
                "Chassis": chassis.strip(),
                "Status": "Ready to Dispatch",
                "Dispatcher Notes": _safe_str(selected_load.get("Dispatcher Notes", ""))
                or "Driver, truck, and chassis assigned. Ready to dispatch.",
            },
        )
        st.session_state.pop("orders_management_selected_row_id", None)
        st.session_state.pop("orders_management_selected_context", None)
        refresh_data()
        st.success("Order marked Ready to Dispatch.")
        st.rerun()

    if ready_disabled:
        st.info("Mark Ready to Dispatch is disabled until Driver, Truck, and Chassis are all filled in.")
```

with:

```python
    st.markdown("#### Generated Dispatch Message")
    preview_load = selected_load.copy()
    preview_load["Driver Name"] = driver_name
    preview_load["Truck Assigned"] = truck
    preview_load["Chassis"] = chassis
    generated_message = _generate_driver_dispatch_message(preview_load)
    edited_message = st.text_area(
        "Dispatch Message",
        value=generated_message,
        height=260,
        key=f"{panel_key}_message",
    )
    if phone.strip():
        st.caption(f"Driver phone on file: {phone.strip()}")

    ready_disabled = not (driver_name.strip() and truck.strip() and chassis.strip() and phone.strip())
    if st.button(
        "Mark Ready to Dispatch",
        key=f"{panel_key}_mark_ready",
        use_container_width=True,
        disabled=ready_disabled,
    ):
        normalized_phone = format_phone_e164(phone)
        if not normalized_phone:
            st.error(f"'{phone.strip()}' isn't a valid phone number. Fix it and try again — no text was sent.")
        else:
            sent, sid_or_error = send_sms(normalized_phone, edited_message)
            if sent:
                _insert_dispatch_message(
                    selected_row_id,
                    "driver_dispatch_sms",
                    "outbound",
                    normalized_phone,
                    edited_message,
                )
                DispatchDatabaseClient().update_row_fields(
                    selected_row_id,
                    {
                        "Driver Name": driver_name.strip(),
                        "Truck Assigned": truck.strip(),
                        "Chassis": chassis.strip(),
                        "Status": "Ready to Dispatch",
                        "Dispatcher Notes": _safe_str(selected_load.get("Dispatcher Notes", ""))
                        or "Driver, truck, and chassis assigned. Ready to dispatch.",
                    },
                )
                st.session_state.pop("orders_management_selected_row_id", None)
                st.session_state.pop("orders_management_selected_context", None)
                refresh_data()
                st.success(f"Text sent to {driver_name} and load marked Ready to Dispatch.")
                st.rerun()
            else:
                st.error(f"Could not send the text — no changes were made. {sid_or_error}")

    if ready_disabled:
        st.info("Mark Ready to Dispatch is disabled until Driver, Truck, Chassis, and Phone are all filled in.")
```

Note: on a failed send, nothing runs after the `else` branch's `st.error` — no `DispatchDatabaseClient` call, no `_insert_dispatch_message`, no `st.rerun()`. The dispatcher stays on the same panel with the same (still-editable) fields and can retry.

- [ ] **Step 3: Compile check**

Run: `python -m compileall pages_app/orders_management.py`
Expected: exit code 0.

- [ ] **Step 4: Verify the wiring**

Run:
```bash
grep -n "edited_message\|format_phone_e164\|send_sms\|driver_dispatch_sms" pages_app/orders_management.py
```
Expected: `edited_message` appears at its `st.text_area` assignment and again in the `send_sms(...)` call; `format_phone_e164`/`send_sms` appear in the import line and in the button handler; `driver_dispatch_sms` appears once, as the `message_type` argument to `_insert_dispatch_message`.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q --ignore=tests/test_operations_attachment_parsing.py --ignore=tests/test_operations_classification.py --ignore=tests/test_operations_container_qty_confirmation.py --ignore=tests/test_operations_control_center_snapshot.py --ignore=tests/test_operations_email_insert_resilience.py --ignore=tests/test_operations_email_sync_budget.py --ignore=tests/test_operations_email_triage_service.py --ignore=tests/test_operations_merge_parsed_fields.py --ignore=tests/test_operations_merge_saved_attachment_fields.py --ignore=tests/test_operations_multi_container_service.py --ignore=tests/test_operations_pending_draft_fields.py --ignore=tests/test_operations_route_cargo_section.py --ignore=tests/test_pdf_preview.py --ignore=tests/test_db_client_column_exists.py`
Expected: all pass (same 112 count as before this task — Task 1 added 10 tests to the prior 102 baseline; this task adds no new tests, it's UI wiring).

- [ ] **Step 6: Commit**

```bash
git add pages_app/orders_management.py
git commit -m "$(cat <<'EOF'
feat: send driver dispatch text on Mark Ready to Dispatch

Phone is now required alongside driver/truck/chassis. The text is
sent via Twilio before the load's status changes — a failed send
leaves the load untouched so the dispatcher can fix and retry. Also
fixes a pre-existing bug where edits to the message preview were
never captured (the text_area's return value was discarded).
EOF
)"
```

---

### Task 4: Final verification pass

**Files:**
- None modified — verification only.

- [ ] **Step 1: Compile every touched file**

Run: `python -m compileall pages_app/orders_management.py services/driver_sms_service.py tests/test_driver_sms_service.py`
Expected: exit code 0.

- [ ] **Step 2: Run the full filtered test suite one more time**

Run the same filtered pytest command as Task 3, Step 5.
Expected: all pass, including the 10 new `test_driver_sms_service.py` tests.

- [ ] **Step 3: Confirm no real Twilio credentials exist in this environment**

Run:
```bash
python -c "
from config import get_secret
for name in ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM_NUMBER']:
    print(name, '=', 'SET' if get_secret(name) else 'not set')
"
```
This is expected to print `not set` for all three in this development environment — confirming that none of the automated steps in this plan could have sent a real text (the missing-secrets path in Task 2 Step 4 is the only thing that ever called `send_sms`, and it returns `(False, ...)` without ever reaching the network).

- [ ] **Step 4: Report results and manual UAT instructions**

No commit for this task (verification only). Report: compile result, test result, and the confirmation from Step 3. Then give the user these manual UAT instructions (requires them to add real Twilio credentials and a phone number they control — cannot be done by an automated agent):

1. Sign up for Twilio (or use an existing account), buy/verify a phone number capable of sending SMS.
2. Add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` to `.streamlit/secrets.toml` (or `.env`) in the actual running environment — not this worktree/plan, since secrets are never committed or copied by this process.
3. In the Drivers roster (Admin → Drivers), set a phone number you control for a test driver, OR type your own number into the "Other / not in roster" phone field in the Ready to Dispatch panel.
4. Open an order at Booking Verified, go to the Ready to Dispatch tab, confirm the button is disabled until phone is filled in.
5. Temporarily enter an invalid phone (e.g. "123") and click the button — confirm an error shows, no text is sent, and the order does NOT change status.
6. Enter your real phone number, click Mark Ready to Dispatch, confirm the text arrives on your phone with the expected message content, confirm the order's status flips to Ready to Dispatch, and confirm the message appears in that load's Driver Communication Thread on the Dispatch Board (`message_type = driver_dispatch_sms`).
