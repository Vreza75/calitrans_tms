import { describe, expect, it } from "vitest";

import { driverKeys, loadKeys, stageKeys } from "@/lib/api/queryKeys";

describe("driverKeys", () => {
  it("nests active() under all() so a broad invalidation would still match it", () => {
    const activeKey = driverKeys.active();
    expect(activeKey.slice(0, driverKeys.all.length)).toEqual(driverKeys.all);
  });
});

describe("stageKeys", () => {
  it("produces distinct keys for distinct service flows", () => {
    expect(stageKeys.forServiceFlow("Import")).not.toEqual(stageKeys.forServiceFlow("Export"));
  });

  it("nests forServiceFlow() under all()", () => {
    const key = stageKeys.forServiceFlow("Import");
    expect(key.slice(0, stageKeys.all.length)).toEqual(stageKeys.all);
  });
});

describe("loadKeys.list with the extended Phase 10B filter set", () => {
  it("produces distinct keys for distinct driver_name/port/warehouse filters", () => {
    const base = loadKeys.list({ search: "RICGX" });
    const withDriver = loadKeys.list({ search: "RICGX", driver_name: "Joe Driver" });
    const withPort = loadKeys.list({ search: "RICGX", port: "Houston" });

    expect(base).not.toEqual(withDriver);
    expect(withDriver).not.toEqual(withPort);
  });

  it("stays nested under lists() so a broad invalidation still matches every filter combination", () => {
    const listsKey = loadKeys.lists();
    const specific = loadKeys.list({ driver_name: "Joe Driver", port: "Houston", warehouse: "West" });
    expect(specific.slice(0, listsKey.length)).toEqual(listsKey);
  });
});
