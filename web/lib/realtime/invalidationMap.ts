// STEP 24: a centralized event_type -> query invalidation map, so this
// logic never gets duplicated/drifted across components. STEP 25: never
// invalidate the whole cache for one event - only the specific queries
// the client invalidation contract (docs/architecture/
// REALTIME_EVENTS.md's "Client invalidation contract" table) names for
// that event_type.
import type { QueryClient } from "@tanstack/react-query";

import { inboxKeys, loadKeys } from "@/lib/api/queryKeys";
import type { RealtimeDomainEvent } from "@/lib/realtime/types";

const LOAD_EVENT_TYPES = new Set(["load.status_changed", "load.assignment_changed", "load.updated"]);
const INBOX_EVENT_TYPES = new Set(["inbox.received", "inbox.review_status_changed"]);
const COMMUNICATION_EVENT_TYPES = new Set(["communication.queued", "communication.delivery_status_changed"]);
const DOCUMENT_EVENT_TYPES = new Set(["document.available", "document.failed"]);

export function invalidateForEvent(queryClient: QueryClient, eventType: string, event: RealtimeDomainEvent): void {
  if (LOAD_EVENT_TYPES.has(eventType)) {
    queryClient.invalidateQueries({ queryKey: loadKeys.lists() });
    queryClient.invalidateQueries({ queryKey: loadKeys.detail(event.aggregate_id) });
    queryClient.invalidateQueries({ queryKey: loadKeys.timeline(event.aggregate_id) });
    return;
  }

  if (INBOX_EVENT_TYPES.has(eventType)) {
    queryClient.invalidateQueries({ queryKey: inboxKeys.lists() });
    queryClient.invalidateQueries({ queryKey: inboxKeys.detail(event.aggregate_id) });
    queryClient.invalidateQueries({ queryKey: [...inboxKeys.all, "counts"] });
    return;
  }

  const loadId = event.metadata?.load_id;
  if (COMMUNICATION_EVENT_TYPES.has(eventType) && loadId != null) {
    queryClient.invalidateQueries({ queryKey: loadKeys.communications(String(loadId)) });
    return;
  }

  if (DOCUMENT_EVENT_TYPES.has(eventType) && loadId != null) {
    queryClient.invalidateQueries({ queryKey: loadKeys.documents(String(loadId)) });
    return;
  }

  // An event_type this map doesn't recognize yet is not an error - the
  // backend's event catalog (realtime/event_types.py) may grow ahead of
  // this map; a client should ignore what it doesn't understand rather
  // than throw.
}
