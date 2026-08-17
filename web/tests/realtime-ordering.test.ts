import { describe, expect, it } from "vitest";

import { EventOrderingTracker } from "@/lib/realtime/ordering";

describe("EventOrderingTracker", () => {
  it("treats the first event for an aggregate as new", () => {
    const tracker = new EventOrderingTracker();
    expect(tracker.isNew("load", "381", 100)).toBe(true);
  });

  it("rejects an event whose id is not greater than the last one applied", () => {
    const tracker = new EventOrderingTracker();
    tracker.record("load", "381", 100);

    expect(tracker.isNew("load", "381", 100)).toBe(false);
    expect(tracker.isNew("load", "381", 99)).toBe(false);
  });

  it("accepts a strictly newer event", () => {
    const tracker = new EventOrderingTracker();
    tracker.record("load", "381", 100);

    expect(tracker.isNew("load", "381", 101)).toBe(true);
  });

  it("tracks ordering independently per aggregate", () => {
    const tracker = new EventOrderingTracker();
    tracker.record("load", "381", 100);

    expect(tracker.isNew("load", "999", 1)).toBe(true);
  });

  it("does not regress the recorded high-water mark on an out-of-order record() call", () => {
    const tracker = new EventOrderingTracker();
    tracker.record("load", "381", 100);
    tracker.record("load", "381", 50);

    expect(tracker.isNew("load", "381", 75)).toBe(false);
  });
});
