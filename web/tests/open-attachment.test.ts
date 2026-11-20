import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PopupBlockedError, openAttachment, setTokenGetter, setUnauthorizedHandler } from "@/lib/api/client";

// openAttachment() is the only path that ever renders customer-supplied
// (email attachment) bytes in this app's own origin - see client.ts's own
// comments for the full threat model. These tests cover the two PR #23 P2
// findings that are reachable without a real browser/backend: (1) a
// blocked popup must never be treated as success, and (2) every failure
// path (network error, non-2xx, a body read that fails mid-stream, or the
// final navigation itself throwing) must close the temporary tab and never
// leave an object URL un-revoked. Script-execution-in-the-popup and the
// MIME allowlist itself need a real browser and are covered separately in
// web/e2e/inbox-attachment-link.spec.ts.

function fakeResponse(init: {
  ok?: boolean;
  status?: number;
  contentType?: string;
  blob?: () => Promise<Blob>;
  json?: () => Promise<unknown>;
}): Response {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? true,
    status,
    headers: { get: (name: string) => (name.toLowerCase() === "content-type" ? init.contentType ?? null : null) },
    blob: init.blob ?? (async () => new Blob(["stub"], { type: init.contentType ?? "text/plain" })),
    json: init.json ?? (async () => ({})),
  } as unknown as Response;
}

function fakeTab() {
  return { close: vi.fn(), location: { href: "" } };
}

describe("openAttachment", () => {
  beforeEach(() => {
    setTokenGetter(() => "test-bearer-token");
    setUnauthorizedHandler(null);
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn(() => "blob:mock-url");
      static revokeObjectURL = vi.fn();
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("throws PopupBlockedError and never fetches when window.open() returns null", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    await expect(openAttachment("ref-1", 42)).rejects.toBeInstanceOf(PopupBlockedError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("sends the bearer token, the absolute cross-origin URL, and the work_item_id binding on success", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const fetchSpy = vi.fn().mockResolvedValue(fakeResponse({ contentType: "application/pdf" }));
    vi.stubGlobal("fetch", fetchSpy);

    await openAttachment("ref/with space", 42);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, requestInit] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/attachments/ref%2Fwith%20space/content?work_item_id=42");
    expect((requestInit.headers as Record<string, string>).Authorization).toBe("Bearer test-bearer-token");
    expect(tab.location.href).toBe("blob:mock-url");
    expect(tab.close).not.toHaveBeenCalled();
  });

  it("closes the tab and revokes nothing extra when fetch itself rejects (network error)", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const networkError = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(networkError));

    await expect(openAttachment("ref-1", 42)).rejects.toBe(networkError);
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled(); // no blob was ever created
  });

  it("closes the tab on a non-2xx HTTP response (existing behavior preserved)", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeResponse({ ok: false, status: 404, json: async () => ({ error: { message: "not found" } }) })),
    );

    await expect(openAttachment("ref-1", 42)).rejects.toMatchObject({ status: 404 });
    expect(tab.close).toHaveBeenCalledTimes(1);
  });

  it("closes the tab when the response body fails to read mid-stream", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const bodyError = new Error("body stream disturbed");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fakeResponse({ contentType: "application/pdf", blob: async () => { throw bodyError; } }),
      ),
    );

    await expect(openAttachment("ref-1", 42)).rejects.toBe(bodyError);
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled(); // blob never got created, nothing to revoke
  });

  it("closes the tab and revokes the object URL immediately if navigating the tab throws", async () => {
    const tab = {
      close: vi.fn(),
      get location(): never {
        throw new Error("tab was closed by the user");
      },
    };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ contentType: "application/pdf" })));

    await expect(openAttachment("ref-1", 42)).rejects.toThrow("tab was closed by the user");
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("schedules revocation of the object URL after a successful open (cleanup, not a leak)", async () => {
    vi.useFakeTimers();
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ contentType: "application/pdf" })));

    await openAttachment("ref-1", 42);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    vi.advanceTimersByTime(30_000);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    vi.useRealTimers();
  });

  describe("MIME allowlist (do not weaken)", () => {
    it.each(["text/html", "image/svg+xml", "application/javascript", "text/javascript"])(
      "forces %s to application/octet-stream before creating the blob",
      async (hostileType) => {
        const tab = fakeTab();
        vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
        const rawBlob = { type: hostileType, arrayBuffer: async () => new ArrayBuffer(4) } as unknown as Blob;
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ contentType: hostileType, blob: async () => rawBlob })));
        const createObjectURL = URL.createObjectURL as unknown as ReturnType<typeof vi.fn>;

        await openAttachment("ref-1", 42);

        const blobPassed = createObjectURL.mock.calls[0][0] as Blob;
        expect(blobPassed.type).toBe("application/octet-stream");
      },
    );

    it.each(["application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"])(
      "passes the allowlisted type %s through unchanged",
      async (safeType) => {
        const tab = fakeTab();
        vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
        const rawBlob = { type: safeType, arrayBuffer: async () => new ArrayBuffer(4) } as unknown as Blob;
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ contentType: safeType, blob: async () => rawBlob })));
        const createObjectURL = URL.createObjectURL as unknown as ReturnType<typeof vi.fn>;

        await openAttachment("ref-1", 42);

        const blobPassed = createObjectURL.mock.calls[0][0] as Blob;
        expect(blobPassed).toBe(rawBlob); // same-type fast path: passed through, not rebuilt
      },
    );

    it("forces a MIME-spoofed report (unknown/attacker-chosen type) to application/octet-stream", async () => {
      const tab = fakeTab();
      vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
      const rawBlob = { type: "application/x-spoofed", arrayBuffer: async () => new ArrayBuffer(4) } as unknown as Blob;
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(fakeResponse({ contentType: "application/x-spoofed", blob: async () => rawBlob })),
      );
      const createObjectURL = URL.createObjectURL as unknown as ReturnType<typeof vi.fn>;

      await openAttachment("ref-1", 42);

      const blobPassed = createObjectURL.mock.calls[0][0] as Blob;
      expect(blobPassed.type).toBe("application/octet-stream");
    });
  });
});
