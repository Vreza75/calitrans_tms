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
