// STEP 43: "realtime load.updated event -> query invalidation -> detail
// refresh", with the realtime transport mocked (no live Supabase
// connection in CI - see lib/realtime/client.ts's subscribeToChannel,
// mocked below rather than opening a real websocket).
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadKeys } from "@/lib/api/queryKeys";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";

type BroadcastHandler = (eventType: string, payload: unknown) => void;
type StatusHandler = (status: "connecting" | "connected" | "disconnected" | "error") => void;

const subscriptions = new Map<string, { onEvent: BroadcastHandler; onStatus: StatusHandler }>();

vi.mock("@/lib/realtime/client", () => ({
  subscribeToChannel: (topic: string, onEvent: BroadcastHandler, onStatus: StatusHandler) => {
    subscriptions.set(topic, { onEvent, onStatus });
    onStatus("connected");
    return () => subscriptions.delete(topic);
  },
}));

function emitLoadUpdated(topic: string, eventId: number) {
  subscriptions.get(topic)?.onEvent("load.updated", {
    event_id: eventId,
    aggregate_type: "load",
    aggregate_id: "381",
    version: null,
    occurred_at: "2026-08-15T00:00:00Z",
    metadata: { updated_fields: ["driver_name"] },
  });
}

let fetchCallCount = 0;

function LoadDetailProbe() {
  const connectionState = useRealtimeChannels(["loads", "load:381"]);
  const { data } = useQuery({
    queryKey: loadKeys.detail("381"),
    queryFn: async () => {
      fetchCallCount += 1;
      return { driver_name: `fetch-${fetchCallCount}` };
    },
  });

  return (
    <div>
      <span data-testid="connection">{connectionState}</span>
      <span data-testid="driver">{data?.driver_name}</span>
    </div>
  );
}

describe("useRealtimeChannels integration", () => {
  afterEach(() => {
    subscriptions.clear();
    fetchCallCount = 0;
    vi.clearAllMocks();
  });

  it("refetches the load detail query after a load.updated broadcast for this aggregate", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <LoadDetailProbe />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("driver")).toHaveTextContent("fetch-1"));
    expect(screen.getByTestId("connection")).toHaveTextContent("connected");

    emitLoadUpdated("load:381", 1001);

    await waitFor(() => expect(screen.getByTestId("driver")).toHaveTextContent("fetch-2"));
  });

  it("ignores a stale/out-of-order broadcast for an aggregate already seen at a higher event_id", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <LoadDetailProbe />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("driver")).toHaveTextContent("fetch-1"));

    emitLoadUpdated("load:381", 50);
    await waitFor(() => expect(screen.getByTestId("driver")).toHaveTextContent("fetch-2"));

    // A broadcast with a lower event_id than one already applied for this
    // aggregate must not trigger another refetch.
    emitLoadUpdated("load:381", 10);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByTestId("driver")).toHaveTextContent("fetch-2");
  });
});
