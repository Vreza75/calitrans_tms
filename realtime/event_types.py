# realtime/event_types.py

from __future__ import annotations

"""Phase 9 initial event catalog (STEP 7). A small, deliberately
incomplete set - only event types an instrumented command/service
actually emits this pass. See docs/architecture/REALTIME_EVENTS.md for
the full catalog including future/not-yet-implemented event types.

Each constant's value is the wire event_type string clients match on
(realtime/envelope.py::DomainEvent.event_type) - stable once published,
treat renaming one as a breaking change for any listening client."""

LOAD_STATUS_CHANGED = "load.status_changed"
LOAD_ASSIGNMENT_CHANGED = "load.assignment_changed"
LOAD_UPDATED = "load.updated"

INBOX_RECEIVED = "inbox.received"
INBOX_REVIEW_STATUS_CHANGED = "inbox.review_status_changed"

COMMUNICATION_QUEUED = "communication.queued"
COMMUNICATION_DELIVERY_STATUS_CHANGED = "communication.delivery_status_changed"

DOCUMENT_AVAILABLE = "document.available"
DOCUMENT_FAILED = "document.failed"

# Event types documented in the original Phase 9 spec's catalog but NOT
# implemented this pass (no instrumented emitter yet) - see docs/
# architecture/REALTIME_EVENTS.md's "Future events" section for why:
#   load.created                  - underlying create path
#                                    (services.operations_inbox_service::
#                                    create_load_from_inbox_item) is
#                                    already multi-transaction, non-atomic
#                                    (documented Phase 1/2 known
#                                    limitation) - coupling an event to it
#                                    "in the same transaction" per STEP 6
#                                    is not honest until that path itself
#                                    becomes atomic.
#   inbox.processing_completed     - would require a single transaction
#                                    boundary across a whole sync/process
#                                    run, which the Phase 7 async job
#                                    model deliberately does not have
#                                    (each message is processed by its own
#                                    independent job).
