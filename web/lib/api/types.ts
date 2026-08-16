// Re-exports of the OpenAPI-generated schema types (lib/api/generated.ts).
// Do not hand-edit generated.ts - regenerate it with `npm run api:generate`
// (see README under web/ for the exact steps).
import type { components } from "./generated";

export type Schemas = components["schemas"];

export type LoadSummary = Schemas["LoadSummaryOut"];
export type LoadListItem = Schemas["LoadListItemOut"];
export type LoadDetail = Schemas["LoadDetailOut"];
export type LoadPage = Schemas["LoadPageOut"];
export type LoginRequest = Schemas["LoginIn"];
export type LoginResponse = Schemas["LoginOut"];
export type MeResponse = Schemas["MeOut"];

// The FastAPI error envelope every response from api/errors.py uses -
// {"error": {"code", "message", "details"}} - never {"detail": ...}.
export type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
};
