"use client";

// Frontend auth/session abstraction (Phase 10 STEP 10). Wraps the
// browser login bridge (POST /api/v1/auth/login, GET /api/v1/me - see
// api/routers/auth.py) so components never parse a token or call those
// endpoints directly.
//
// Token storage tradeoff (STEP 41, documented rather than solved this
// phase): the session token is held in memory and mirrored to
// sessionStorage (tab-scoped, cleared on tab close) rather than
// localStorage (persists indefinitely) or an httpOnly cookie (would
// require the backend to set/rotate cookies and add CSRF protection -
// out of scope for the "smallest secure bridge" api/auth.py's
// session_tokens.py chose). sessionStorage is still readable by any
// script running on the page (XSS risk, same as localStorage) - a
// cookie-based session is the recommended follow-up before this client
// carries real production traffic. See docs/architecture/WEB_CLIENT.md.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, apiClient, setTokenGetter, setUnauthorizedHandler } from "@/lib/api/client";
import type { MeResponse } from "@/lib/api/types";

const SESSION_STORAGE_KEY = "calitrans_web_session_token";

type AuthState = {
  token: string | null;
  currentUser: MeResponse | null;
  status: "loading" | "authenticated" | "unauthenticated";
};

type AuthContextValue = AuthState & {
  role: string | null;
  permissions: string[];
  isAuthenticated: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
  hasPermission: (permission: string) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ token: null, currentUser: null, status: "loading" });

  // setTokenGetter/setUnauthorizedHandler wire the API client to this
  // context without a circular import (see lib/api/client.ts).
  useEffect(() => {
    setTokenGetter(() => state.token);
  }, [state.token]);

  const signOut = useCallback(() => {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
    setState({ token: null, currentUser: null, status: "unauthenticated" });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => signOut());
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  const loadCurrentUser = useCallback(async (token: string) => {
    setTokenGetter(() => token);
    try {
      const me = await apiClient.get<MeResponse>("/api/v1/me");
      setState({ token, currentUser: me, status: "authenticated" });
    } catch (error) {
      // Only a real 401 means the token itself is invalid/expired - clear
      // it and sign out. Any other failure (network hiccup, CORS
      // misconfiguration, transient 5xx) is not proof the session is
      // bad, so the stored token is kept and bootstrap is treated as
      // failed-but-retryable instead of forcing a logout on every
      // refresh that happens to race a flaky /me call.
      if (error instanceof ApiError && error.status === 401) {
        sessionStorage.removeItem(SESSION_STORAGE_KEY);
        setState({ token: null, currentUser: null, status: "unauthenticated" });
        return;
      }
      setState((prev) => ({ ...prev, status: "loading" }));
      throw error;
    }
  }, []);

  // Bootstrap from a previous tab session on first mount - a genuine
  // "synchronize with an external system" effect (STEP 10), not derived
  // state. loadCurrentUser's own setState calls are intentionally async
  // (after the /me fetch resolves), which is what react-hooks'
  // set-state-in-effect rule is built to catch for the synchronous case -
  // safe to disable here.
  useEffect(() => {
    const stored = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (stored) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      loadCurrentUser(stored).catch(() => {
        // Non-401 failures already update state inside loadCurrentUser
        // (see its catch block) - this only prevents an unhandled
        // promise rejection from this fire-and-forget bootstrap call.
        // signIn() awaits loadCurrentUser directly, so its rejection
        // still propagates to the login form there.
      });
    } else {
      setState((prev) => ({ ...prev, status: "unauthenticated" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const result = await apiClient.post<{ token: string; actor: string; role: string }>("/api/v1/auth/login", {
      email,
      password,
    });
    sessionStorage.setItem(SESSION_STORAGE_KEY, result.token);
    await loadCurrentUser(result.token);
  }, [loadCurrentUser]);

  const value = useMemo<AuthContextValue>(() => {
    const permissions = state.currentUser?.permissions ?? [];
    return {
      ...state,
      role: state.currentUser?.role ?? null,
      permissions,
      isAuthenticated: state.status === "authenticated",
      signIn,
      signOut,
      hasPermission: (permission: string) => permissions.includes(permission),
    };
  }, [state, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return context;
}
