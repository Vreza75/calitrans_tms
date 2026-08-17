import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth/AuthContext";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="role">{auth.role ?? "none"}</span>
      <span data-testid="permission-count">{auth.permissions.length}</span>
      <button
        type="button"
        onClick={() => {
          void auth.signIn("dispatcher@calitranscorp.com", "correct-password");
        }}
      >
        Sign in
      </button>
      <button type="button" onClick={() => auth.signOut()}>
        Sign out
      </button>
    </div>
  );
}

describe("AuthProvider", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts unauthenticated when there is no stored session", async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
  });

  it("becomes authenticated after signIn resolves, exposing role and permissions from /me", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return jsonResponse({ token: "session-token-abc", actor: "dispatcher@calitranscorp.com", role: "dispatcher" });
      }
      if (url.includes("/me")) {
        return jsonResponse({
          email: "dispatcher@calitranscorp.com",
          display_name: "Dee Dispatcher",
          role: "dispatcher",
          permissions: ["load:view", "load:edit"],
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof fetch);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));

    await act(async () => {
      screen.getByText("Sign in").click();
    });

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    expect(screen.getByTestId("role")).toHaveTextContent("dispatcher");
    expect(screen.getByTestId("permission-count")).toHaveTextContent("2");
    expect(sessionStorage.getItem("calitrans_web_session_token")).toBe("session-token-abc");
    fetchSpy.mockRestore();
  });

  it("clears the session on signOut", async () => {
    sessionStorage.setItem("calitrans_web_session_token", "stale-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        email: "dispatcher@calitranscorp.com",
        display_name: "Dee Dispatcher",
        role: "dispatcher",
        permissions: ["load:view"],
      }),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));

    await act(async () => {
      screen.getByText("Sign out").click();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated");
    expect(sessionStorage.getItem("calitrans_web_session_token")).toBeNull();
  });

  it("signs the user out when the bootstrap /me call comes back 401 (stale/expired stored token)", async () => {
    sessionStorage.setItem("calitrans_web_session_token", "expired-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "UNAUTHENTICATED", message: "Invalid or unrecognized API token." } }, 401),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
    expect(sessionStorage.getItem("calitrans_web_session_token")).toBeNull();
  });

  it("keeps the stored session token when the bootstrap /me call fails with a non-401 error (network hiccup, CORS, transient 5xx)", async () => {
    sessionStorage.setItem("calitrans_web_session_token", "still-valid-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "INTERNAL", message: "Upstream unavailable." } }, 500),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    // Status must never flip to "unauthenticated" (which would redirect to
    // /login) or "authenticated" (no confirmed user) on a transient
    // bootstrap failure - it stays "loading" so the protected-route guard
    // keeps rendering its loading state instead of redirecting.
    await waitFor(() => expect(screen.getByTestId("status")).not.toHaveTextContent("authenticated"));
    expect(screen.getByTestId("status")).toHaveTextContent("loading");
    expect(sessionStorage.getItem("calitrans_web_session_token")).toBe("still-valid-token");
  });
});
