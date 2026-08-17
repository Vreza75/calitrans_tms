// Browser Supabase Realtime client (STEP 20). Publishable/anon key
// only - never the service-role key (that lives server-side only, in
// realtime/publisher.py). Business reads/writes still go through
// FastAPI (lib/api/client.ts) - this client is transport for broadcast
// invalidation signals only, never a direct database query.
import { type RealtimeChannel, createClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

export function isRealtimeConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_PUBLISHABLE_KEY);
}

let client: ReturnType<typeof createClient> | null = null;

// Lazily constructed and only when configured - STEP 36/27: a missing
// realtime configuration must not crash the app or block API reads,
// it just means no live channel is ever opened.
export function getRealtimeClient(): ReturnType<typeof createClient> | null {
  if (!isRealtimeConfigured()) {
    return null;
  }
  if (!client) {
    client = createClient(SUPABASE_URL as string, SUPABASE_PUBLISHABLE_KEY as string);
  }
  return client;
}

export type BroadcastHandler = (eventType: string, payload: unknown) => void;

// Subscribes to every broadcast event on `topic` (channel naming from
// lib/realtime/channels.ts, matching realtime/channels.py exactly).
// Returns an unsubscribe function - callers must call it on cleanup
// (component unmount) to avoid leaking a websocket subscription.
export function subscribeToChannel(
  topic: string,
  onEvent: BroadcastHandler,
  onStatusChange: (status: "connecting" | "connected" | "disconnected" | "error") => void,
): () => void {
  const supabase = getRealtimeClient();
  if (!supabase) {
    onStatusChange("disconnected");
    return () => {};
  }

  onStatusChange("connecting");
  const channel: RealtimeChannel = supabase
    .channel(topic, { config: { broadcast: { self: true } } })
    .on("broadcast", { event: "*" }, ({ event, payload }) => onEvent(event, payload))
    .subscribe((status) => {
      if (status === "SUBSCRIBED") onStatusChange("connected");
      else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") onStatusChange("error");
      else if (status === "CLOSED") onStatusChange("disconnected");
    });

  return () => {
    supabase.removeChannel(channel);
  };
}
