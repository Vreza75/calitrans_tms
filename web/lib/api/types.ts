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

export type WorkItemSummary = Schemas["WorkItemSummaryOut"];
export type WorkItemPage = Schemas["WorkItemPageOut"];
export type WorkItemDetail = Schemas["WorkItemDetailOut"];
export type WorkItemQueueCounts = Schemas["WorkItemQueueCountsOut"];
export type QueueCount = Schemas["QueueCountOut"];
export type AttachmentMeta = Schemas["AttachmentMetaOut"];
export type AttachmentSummary = Schemas["AttachmentSummaryOut"];
export type ConversationMessage = Schemas["ConversationMessageOut"];
export type ConversationPage = Schemas["ConversationPageOut"];
export type CommandResult = Schemas["CommandResultOut"];
export type CreateLoadRequest = Schemas["CreateLoadIn"];
export type CreateLoadResult = Schemas["CreateLoadOut"];
export type UpdateLoadRequest = Schemas["UpdateLoadIn"];
export type UpdateLoadResult = Schemas["UpdateLoadOut"];
export type LinkLoadRequest = Schemas["LinkLoadIn"];
export type CloseWorkItemRequest = Schemas["CloseWorkItemIn"];

// The FastAPI error envelope every response from api/errors.py uses -
// {"error": {"code", "message", "details"}} - never {"detail": ...}.
export type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
};
