import { describe, expect, it } from "vitest";

import { collectionChannel, resourceChannel } from "@/lib/realtime/channels";

// Mirrors realtime/channels.py's own tests (tests/test_realtime_channels.py)
// - must stay in exact agreement with the backend's naming.
describe("realtime channels", () => {
  it("maps every known aggregate type to its collection channel", () => {
    expect(collectionChannel("load")).toBe("loads");
    expect(collectionChannel("order_intake_item")).toBe("inbox");
    expect(collectionChannel("dispatch_message")).toBe("communications");
    expect(collectionChannel("document")).toBe("documents");
  });

  it("throws for an unknown aggregate type instead of guessing a channel name", () => {
    expect(() => collectionChannel("something_new")).toThrow();
  });

  it("gives load a resource channel", () => {
    expect(resourceChannel("load", 381)).toBe("load:381");
  });

  it("gives document no resource channel", () => {
    expect(resourceChannel("document", 42)).toBeNull();
  });
});
