"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { loadKeys } from "@/lib/api/queryKeys";
import type {
  DocumentUploadResult,
  LoadCommandResult,
  LoadCommunicationPage,
  LoadDocumentMeta,
  LoadNoteOnlyRequest,
  LoadTimelinePage,
  LogCommunicationRequest,
  LogCommunicationResult,
  SaveLoadNoteRequest,
  UpdateLoadFieldsRequest,
} from "@/lib/api/types";

export function useLoadDocuments(loadId: number | null) {
  return useQuery({
    queryKey: loadKeys.documents(loadId ?? ""),
    queryFn: ({ signal }) => apiClient.get<LoadDocumentMeta[]>(`/api/v1/loads/${loadId}/documents`, signal),
    enabled: loadId !== null,
  });
}

export function useLoadCommunications(loadId: number | null) {
  return useQuery({
    queryKey: loadKeys.communications(loadId ?? ""),
    queryFn: ({ signal }) =>
      apiClient.get<LoadCommunicationPage>(`/api/v1/loads/${loadId}/communications?page=1&limit=50`, signal),
    enabled: loadId !== null,
  });
}

export function useLoadTimeline(loadId: number | null) {
  return useQuery({
    queryKey: loadKeys.timeline(loadId ?? ""),
    queryFn: ({ signal }) => apiClient.get<LoadTimelinePage>(`/api/v1/loads/${loadId}/timeline?page=1&limit=50`, signal),
    enabled: loadId !== null,
  });
}

// Every mutation below invalidates the same shape the realtime
// invalidation map (lib/realtime/invalidationMap.ts) already uses for
// its matching event type - so a user's own action updates their screen
// immediately, without waiting on the realtime round-trip (same pattern
// as lib/api/useDispatch.ts's useInvalidateLoad).
function useInvalidateLoadWorkspace(loadId: number) {
  const queryClient = useQueryClient();
  return (scope: "fields" | "documents" | "communications" = "fields") => {
    queryClient.invalidateQueries({ queryKey: loadKeys.lists() });
    queryClient.invalidateQueries({ queryKey: loadKeys.detail(loadId) });
    queryClient.invalidateQueries({ queryKey: loadKeys.timeline(loadId) });
    if (scope === "documents") queryClient.invalidateQueries({ queryKey: loadKeys.documents(loadId) });
    if (scope === "communications") queryClient.invalidateQueries({ queryKey: loadKeys.communications(loadId) });
  };
}

export function useUpdateLoadFields(loadId: number) {
  const invalidate = useInvalidateLoadWorkspace(loadId);
  return useMutation({
    mutationFn: (payload: UpdateLoadFieldsRequest) =>
      apiClient.put<LoadCommandResult>(`/api/v1/loads/${loadId}`, payload),
    onSuccess: () => invalidate("fields"),
    // P1 fix: a 409 means another writer's edit landed on this load
    // since this form's data was loaded - refresh the same queries a
    // success would, so the untouched/refreshed fields pick up the
    // other writer's values (the component keeps the user's own
    // unsaved dirty fields regardless - see EditForm's own comment).
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) invalidate("fields");
    },
  });
}

export function useSaveLoadNote(loadId: number) {
  const invalidate = useInvalidateLoadWorkspace(loadId);
  return useMutation({
    mutationFn: (payload: SaveLoadNoteRequest) =>
      apiClient.post<LoadCommandResult>(`/api/v1/loads/${loadId}/notes`, payload),
    onSuccess: () => invalidate("fields"),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) invalidate("fields");
    },
  });
}

function useStatusQuickAction(loadId: number, action: "mark-missing-info" | "verify-booking" | "cancel") {
  const invalidate = useInvalidateLoadWorkspace(loadId);
  return useMutation({
    mutationFn: (payload: LoadNoteOnlyRequest = { note: "" }) =>
      apiClient.post<LoadCommandResult>(`/api/v1/loads/${loadId}/${action}`, payload),
    onSuccess: () => invalidate("fields"),
  });
}

export function useMarkMissingInfo(loadId: number) {
  return useStatusQuickAction(loadId, "mark-missing-info");
}

export function useVerifyBooking(loadId: number) {
  return useStatusQuickAction(loadId, "verify-booking");
}

export function useCancelLoad(loadId: number) {
  return useStatusQuickAction(loadId, "cancel");
}

export function useLogCommunication(loadId: number) {
  const invalidate = useInvalidateLoadWorkspace(loadId);
  return useMutation({
    mutationFn: (payload: LogCommunicationRequest) =>
      apiClient.post<LogCommunicationResult>(`/api/v1/loads/${loadId}/communications`, payload),
    onSuccess: () => invalidate("communications"),
  });
}

// Multipart upload - apiClient.post()'s underlying apiRequest() detects
// a FormData body and skips JSON-encoding/the Content-Type header (see
// lib/api/client.ts), so the browser sets its own multipart boundary.
export function useAttachDocument(loadId: number) {
  const invalidate = useInvalidateLoadWorkspace(loadId);
  return useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return apiClient.post<DocumentUploadResult>(`/api/v1/loads/${loadId}/documents`, formData);
    },
    onSuccess: () => invalidate("documents"),
  });
}
