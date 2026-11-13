// Centralized FastAPI client. Every request goes through here - no ad
// hoc fetch() calls in components. Owns: base URL, auth header, JSON
// parsing, error normalization (api/errors.py's {"error": {...}}
// envelope -> ApiError), and 401 handling (clears the session and lets
// the caller/auth context redirect to /login - this module has no
// router access itself).
import type { ApiErrorEnvelope } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: Record<string, unknown>;

  constructor(status: number, message: string, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type Unauthorized401Handler = () => void;

let onUnauthorized: Unauthorized401Handler | null = null;

// Set once by the auth context at app startup - avoids a circular
// import (auth context needs the client; the client should not import
// the auth context back).
export function setUnauthorizedHandler(handler: Unauthorized401Handler | null): void {
  onUnauthorized = handler;
}

let getToken: () => string | null = () => null;

export function setTokenGetter(getter: () => string | null): void {
  getToken = getter;
}

async function parseErrorBody(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}.`;
  let code: string | undefined;
  let details: Record<string, unknown> | undefined;

  try {
    const body = (await response.json()) as ApiErrorEnvelope;
    if (body?.error?.message) {
      message = body.error.message;
      code = body.error.code;
      details = body.error.details;
    }
  } catch {
    // Response body was not JSON (or empty) - fall back to the generic
    // message above rather than throwing while building an error.
  }

  return new ApiError(response.status, message, code, details);
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (response.status === 401) {
    onUnauthorized?.();
  }

  if (!response.ok) {
    throw await parseErrorBody(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { method: "GET", signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<T>(path, { method: "POST", body, signal }),
  put: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<T>(path, { method: "PUT", body, signal }),
};

// Opens an attachment in a new tab. A plain `<a href>` cannot work here for
// two independent reasons: (1) a relative href resolves against Next.js's
// own origin, not FastAPI's - broken whenever the two run on different
// origins (the default dev/prod topology, per CORS_ALLOWED_ORIGINS existing
// specifically to support that split); (2) even with the right origin, this
// endpoint requires a Bearer token (api/auth.py's require_auth reads only
// the Authorization header, never a cookie) - a browser-native navigation
// can't attach one. So: fetch with the same auth header every other call
// uses, then open the resulting bytes as a blob URL.
export async function openAttachment(attachmentRef: string, workItemId: number): Promise<void> {
  // Must open the tab synchronously, before the `await` below - calling
  // window.open() after an async gap loses the user-gesture context and
  // every major browser silently blocks it as a popup (no error, no
  // event - just nothing happens). Open a blank tab first, point it at
  // the blob once the fetch resolves. Deliberately no "noopener": this
  // function needs the window handle back to navigate it later, and the
  // target is always our own blob: URL, never an external/untrusted one,
  // so the opener-leak risk noopener guards against doesn't apply here.
  const tab = window.open("", "_blank");

  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(
    `${API_BASE_URL}/api/v1/attachments/${attachmentRef}/content?work_item_id=${workItemId}`,
    { headers },
  );

  if (response.status === 401) {
    onUnauthorized?.();
  }
  if (!response.ok) {
    tab?.close();
    throw await parseErrorBody(response);
  }

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  if (tab) {
    tab.location.href = blobUrl;
  }
  // Revoke once the tab has had time to load the blob - revoking
  // immediately can race the tab's own fetch of the object URL.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000);
}
