# realtime/publisher.py

from __future__ import annotations

"""Phase 9 STEP 2/23: the transport-facing half of realtime delivery.

Only services/realtime_publisher.py (the processor) talks to this
module - application commands never do (they only call realtime/
events.py::publish_event, which knows nothing about Supabase or any
other transport). That separation is what STEP 3 means by "Do NOT
expose Supabase-specific payload structures to application commands."

BroadcastPublisher is a Protocol (structural typing, no base class to
inherit) so a test can hand the processor a trivial fake without
importing this module's real implementations.

SupabaseBroadcastPublisher calls Supabase's REST Broadcast endpoint
(https://supabase.com/docs/guides/realtime/broadcast#broadcast-from-the-server-1)
over plain HTTP via `requests` (already a dependency - see
requirements.txt) rather than adding the supabase-py SDK, which pulls in
websockets/gotrue/postgrest/storage3 for one REST call this module
doesn't need - same "smallest production-appropriate mechanism"
reasoning Phase 7 used to choose GitHub Actions over a dedicated worker
process (see docs/architecture/WORKER_RUNTIME.md). NOT verified against
a live Supabase project in this pass (no live credentials available in
this environment) - see docs/architecture/REALTIME_EVENTS.md's
"Delivery semantics" section for what that means for this pass's
guarantees.

get_publisher() never silently falls back to a no-op if realtime is
supposed to be enabled (STEP 23): REALTIME_ENABLED=true with missing
SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY raises immediately, loudly,
instead of quietly no-op-ing broadcasts in what an operator believes is
a live configuration."""

from typing import Any, Protocol


class BroadcastPublisher(Protocol):
    def broadcast(self, *, channel: str, event_type: str, payload: dict[str, Any]) -> tuple[bool, str]:
        """Returns (success, error) - error is "" on success, never None,
        so callers can always str() it safely (same contract as
        services/outbox_processor.py's handler functions)."""
        ...


class NoOpBroadcastPublisher:
    """Dev/test default (STEP 23: "Dev/test mode should support a
    fake/no-op publisher without external network calls"). Always
    succeeds and makes zero network calls - a domain_events row still
    gets recorded and marked 'published', it just never reaches a real
    client. Safe default for local development and the test suite; NOT
    safe as a silent production fallback (see get_publisher below)."""

    def broadcast(self, *, channel: str, event_type: str, payload: dict[str, Any]) -> tuple[bool, str]:
        return True, ""


class SupabaseBroadcastPublisher:
    """Calls Supabase Realtime's REST Broadcast endpoint. One event per
    HTTP call - Phase 9's event volume (a handful of load/inbox/
    communication/document changes per business day at this company's
    scale) does not justify batching multiple domain_events rows into one
    request."""

    _BROADCAST_PATH = "/realtime/v1/api/broadcast"
    _TIMEOUT_SECONDS = 10

    def __init__(self, *, base_url: str, service_role_key: str):
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key

    def broadcast(self, *, channel: str, event_type: str, payload: dict[str, Any]) -> tuple[bool, str]:
        import requests

        url = f"{self._base_url}{self._BROADCAST_PATH}"
        body = {
            "messages": [
                {"topic": channel, "event": event_type, "payload": payload}
            ]
        }
        headers = {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(url, json=body, headers=headers, timeout=self._TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            return False, f"Supabase broadcast request failed: {exc}"

        if response.status_code >= 400:
            return False, f"Supabase broadcast failed: HTTP {response.status_code}"
        return True, ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def get_publisher() -> BroadcastPublisher:
    """Factory used by services/realtime_publisher.py. Reads
    REALTIME_ENABLED / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY via
    config.get_secret (env/.env/Streamlit-secrets precedence, same
    resolution every other module in this codebase uses - never
    st.secrets directly, per CLAUDE.md's secrets rule)."""
    from config import get_secret

    if not _truthy(get_secret("REALTIME_ENABLED", "false")):
        return NoOpBroadcastPublisher()

    base_url = get_secret("SUPABASE_URL")
    service_role_key = get_secret("SUPABASE_SERVICE_ROLE_KEY")
    if not base_url or not service_role_key:
        raise RuntimeError(
            "REALTIME_ENABLED is true but SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured. "
            "Set both, or set REALTIME_ENABLED=false to use the no-op publisher explicitly."
        )
    return SupabaseBroadcastPublisher(base_url=base_url, service_role_key=service_role_key)
