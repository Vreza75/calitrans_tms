import type { RealtimeConnectionState } from "@/lib/realtime/types";

const LABELS: Record<RealtimeConnectionState, string> = {
  connected: "Live updates connected",
  connecting: "Connecting to live updates...",
  disconnected: "Live updates temporarily unavailable",
  error: "Live updates temporarily unavailable",
};

// STEP 27: never implies data is invalid - only that push notifications
// are (temporarily) not arriving. The API remains authoritative and a
// manual refetch always works regardless of this indicator's state.
export function ConnectionIndicator({ state }: { state: RealtimeConnectionState }) {
  return (
    <span className={`connection-indicator connection-indicator--${state}`} role="status">
      <span className="connection-indicator__dot" aria-hidden="true" />
      {LABELS[state]}
    </span>
  );
}
