import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiClient, setTokenGetter, setUnauthorizedHandler } from "@/lib/api/client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiClient", () => {
  beforeEach(() => {
    setTokenGetter(() => null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches the bearer token when one is configured", async () => {
    setTokenGetter(() => "abc123");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

    await apiClient.get("/api/v1/me");

    const [, init] = fetchSpy.mock.calls[0];
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer abc123");
  });

  it("omits the Authorization header when there is no token", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

    await apiClient.get("/api/v1/health");

    const [, init] = fetchSpy.mock.calls[0];
    expect((init?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("returns parsed JSON on success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ status: "ok" }));

    const result = await apiClient.get<{ status: string }>("/api/v1/health");

    expect(result).toEqual({ status: "ok" });
  });

  it("throws an ApiError built from the backend's error envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "FORBIDDEN", message: "Not permitted.", details: {} } }, 403),
    );

    await expect(apiClient.get("/api/v1/loads")).rejects.toMatchObject({
      status: 403,
      code: "FORBIDDEN",
      message: "Not permitted.",
    });
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not json", { status: 500 }));

    await expect(apiClient.get("/api/v1/loads")).rejects.toBeInstanceOf(ApiError);
  });

  it("calls the unauthorized handler on a 401 without swallowing the error", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "UNAUTHENTICATED", message: "Missing token." } }, 401),
    );

    await expect(apiClient.get("/api/v1/me")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledOnce();
  });

  it("sends a JSON body and Content-Type header for POST requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

    await apiClient.post("/api/v1/auth/login", { email: "a@b.com", password: "x" });

    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init?.body).toBe(JSON.stringify({ email: "a@b.com", password: "x" }));
  });

  // Phase 10C: document upload sends FormData - JSON-encoding it (the
  // default for any other body) would both mangle the payload and omit
  // the multipart boundary the server needs to parse it.
  it("sends a FormData body as-is, without a Content-Type header, for a multipart upload", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true, document_id: 1 }));
    const formData = new FormData();
    formData.append("file", new Blob(["pdf bytes"], { type: "application/pdf" }), "rate.pdf");

    await apiClient.post("/api/v1/loads/1/documents", formData);

    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.body).toBe(formData);
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });
});
