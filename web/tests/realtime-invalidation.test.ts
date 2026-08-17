import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { inboxKeys, loadKeys } from "@/lib/api/queryKeys";
import { invalidateForEvent } from "@/lib/realtime/invalidationMap";
import type { RealtimeDomainEvent } from "@/lib/realtime/types";

function event(overrides: Partial<RealtimeDomainEvent> = {}): RealtimeDomainEvent {
  return {
    event_id: 1,
    aggregate_type: "load",
    aggregate_id: "381",
    version: null,
    occurred_at: "2026-08-15T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}

describe("invalidateForEvent", () => {
  it("invalidates only load list/detail/timeline queries for a load event - not the whole cache", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    invalidateForEvent(queryClient, "load.status_changed", event());

    const invalidatedKeys = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(loadKeys.lists());
    expect(invalidatedKeys).toContainEqual(loadKeys.detail("381"));
    expect(invalidatedKeys).toContainEqual(loadKeys.timeline("381"));
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("invalidates load communications using metadata.load_id, not the dispatch_message aggregate_id", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    invalidateForEvent(
      queryClient,
      "communication.delivery_status_changed",
      event({ aggregate_type: "dispatch_message", aggregate_id: "555", metadata: { load_id: 381 } }),
    );

    expect(spy).toHaveBeenCalledWith({ queryKey: loadKeys.communications("381") });
  });

  it("invalidates inbox lists/detail/counts (not the whole cache) for inbox.received", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    invalidateForEvent(queryClient, "inbox.received", event({ aggregate_type: "order_intake_item", aggregate_id: "42" }));

    const invalidatedKeys = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidatedKeys).toContainEqual(inboxKeys.lists());
    expect(invalidatedKeys).toContainEqual(inboxKeys.detail("42"));
    expect(invalidatedKeys).toContainEqual([...inboxKeys.all, "counts"]);
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("invalidates inbox queries for inbox.review_status_changed too", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    invalidateForEvent(
      queryClient,
      "inbox.review_status_changed",
      event({ aggregate_type: "order_intake_item", aggregate_id: "42" }),
    );

    expect(spy).toHaveBeenCalledWith({ queryKey: inboxKeys.detail("42") });
  });

  it("does nothing for an unrecognized event_type rather than throwing", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    expect(() => invalidateForEvent(queryClient, "some.future.event", event())).not.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not invalidate communications when metadata.load_id is missing", () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    invalidateForEvent(
      queryClient,
      "communication.queued",
      event({ aggregate_type: "dispatch_message", aggregate_id: "555", metadata: {} }),
    );

    expect(spy).not.toHaveBeenCalled();
  });
});
