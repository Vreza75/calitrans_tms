import { describe, expect, it } from "vitest";

import { loadKeys } from "@/lib/api/queryKeys";

describe("loadKeys", () => {
  it("nests list keys under the lists() key so a broad invalidation matches every filter combination", () => {
    const listsKey = loadKeys.lists();
    const specificListKey = loadKeys.list({ status: "Dispatched" });

    expect(specificListKey.slice(0, listsKey.length)).toEqual(listsKey);
  });

  it("produces a stable key for the same id regardless of number vs string", () => {
    expect(loadKeys.detail(381)).toEqual(loadKeys.detail("381"));
  });

  it("keeps detail/timeline/communications/documents keys distinct for the same id", () => {
    const id = 381;
    const keys = [loadKeys.detail(id), loadKeys.timeline(id), loadKeys.communications(id), loadKeys.documents(id)];
    const unique = new Set(keys.map((k) => JSON.stringify(k)));
    expect(unique.size).toBe(keys.length);
  });
});
