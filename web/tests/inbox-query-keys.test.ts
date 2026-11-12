import { describe, expect, it } from "vitest";

import { inboxKeys } from "@/lib/api/queryKeys";

describe("inboxKeys", () => {
  it("nests list keys under lists() so a broad invalidation matches every filter combination", () => {
    const listsKey = inboxKeys.lists();
    const specificListKey = inboxKeys.list({ queue: "New Orders", search: "RICGX" });

    expect(specificListKey.slice(0, listsKey.length)).toEqual(listsKey);
  });

  it("produces a stable key for the same id regardless of number vs string", () => {
    expect(inboxKeys.detail(42)).toEqual(inboxKeys.detail("42"));
    expect(inboxKeys.conversation(42)).toEqual(inboxKeys.conversation("42"));
    expect(inboxKeys.attachments(42)).toEqual(inboxKeys.attachments("42"));
  });

  it("keeps detail/conversation/attachments/counts keys distinct for the same id", () => {
    const id = 42;
    const keys = [inboxKeys.detail(id), inboxKeys.conversation(id), inboxKeys.attachments(id), inboxKeys.counts()];
    const unique = new Set(keys.map((k) => JSON.stringify(k)));
    expect(unique.size).toBe(keys.length);
  });

  it("counts key excludes queue/page/sort so one counts cache entry covers all queues for a given search", () => {
    const withoutQueue = inboxKeys.counts({ search: "acme" });
    expect(JSON.stringify(withoutQueue)).not.toContain("queue");
  });
});
