// Matches the wire envelope services/realtime_publisher.py::
// _envelope_payload builds (see docs/architecture/REALTIME_EVENTS.md's
// "Wire payload" subsection) - not guessed, read directly off the
// backend's actual broadcast shape.
export type RealtimeDomainEvent = {
  event_id: number;
  aggregate_type: string;
  aggregate_id: string;
  version: string | number | null;
  occurred_at: string | null;
  metadata: Record<string, unknown>;
};

export type RealtimeConnectionState = "connecting" | "connected" | "disconnected" | "error";
