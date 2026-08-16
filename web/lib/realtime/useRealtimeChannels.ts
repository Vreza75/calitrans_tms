"use client";

// STEP 20/24/26: subscribes to one or more channels, applies the
// event-ordering rule (lib/realtime/ordering.ts), invalidates the
// mapped queries (lib/realtime/invalidationMap.ts), and exposes a
// single connection state for the whole set of channels ("connected"
// only once every channel in the set is connected; "error"/
// "disconnected" if any is - a partial subscription is not silently
// reported as fully live).
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { invalidateForEvent } from "@/lib/realtime/invalidationMap";
import { subscribeToChannel } from "@/lib/realtime/client";
import { EventOrderingTracker } from "@/lib/realtime/ordering";
import type { RealtimeConnectionState, RealtimeDomainEvent } from "@/lib/realtime/types";

export function useRealtimeChannels(topics: string[]): RealtimeConnectionState {
  const queryClient = useQueryClient();
  const trackerRef = useRef(new EventOrderingTracker());
  const [statuses, setStatuses] = useState<Record<string, RealtimeConnectionState>>({});

  useEffect(() => {
    const unsubscribers = topics.map((topic) =>
      subscribeToChannel(
        topic,
        (eventType, rawPayload) => {
          const event = rawPayload as RealtimeDomainEvent;
          if (typeof event?.event_id !== "number") return;
          const tracker = trackerRef.current;
          if (!tracker.isNew(event.aggregate_type, event.aggregate_id, event.event_id)) return;
          tracker.record(event.aggregate_type, event.aggregate_id, event.event_id);
          invalidateForEvent(queryClient, eventType, event);
        },
        (status) => setStatuses((prev) => ({ ...prev, [topic]: status })),
      ),
    );

    return () => {
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topics.join(","), queryClient]);

  return summarizeConnectionState(topics, statuses);
}

function summarizeConnectionState(
  topics: string[],
  statuses: Record<string, RealtimeConnectionState>,
): RealtimeConnectionState {
  if (topics.length === 0) return "disconnected";
  const values = topics.map((topic) => statuses[topic] ?? "connecting");
  if (values.some((status) => status === "error")) return "error";
  if (values.every((status) => status === "connected")) return "connected";
  if (values.some((status) => status === "connecting")) return "connecting";
  return "disconnected";
}
