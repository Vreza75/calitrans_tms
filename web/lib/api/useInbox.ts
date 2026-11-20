"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { type InboxFilters, inboxKeys, loadKeys } from "@/lib/api/queryKeys";
import type {
  AttachmentSummary,
  CloseWorkItemRequest,
  CommandResult,
  ConversationPage,
  CreateLoadRequest,
  CreateLoadResult,
  LinkLoadRequest,
  UpdateLoadRequest,
  UpdateLoadResult,
  WorkItemDetail,
  WorkItemPage,
  WorkItemQueueCounts,
} from "@/lib/api/types";

function buildParams(filters: InboxFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.queue) params.set("queue", filters.queue);
  if (filters.service_flow) params.set("service_flow", filters.service_flow);
  if (filters.customer) params.set("customer", filters.customer);
  if (filters.search) params.set("search", filters.search);
  if (filters.subject) params.set("subject", filters.subject);
  if (filters.status) params.set("status", filters.status);
  if (filters.attachment_status) params.set("attachment_status", filters.attachment_status);
  if (filters.sort_by) params.set("sort_by", filters.sort_by);
  if (filters.sort_direction) params.set("sort_direction", filters.sort_direction);
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", "25");
  return params;
}

export function useInboxList(filters: InboxFilters) {
  const params = buildParams(filters);
  return useQuery({
    queryKey: inboxKeys.list(filters),
    queryFn: ({ signal }) => apiClient.get<WorkItemPage>(`/api/v1/work-items?${params.toString()}`, signal),
    placeholderData: (previous) => previous,
  });
}

export function useInboxCounts(filters: Omit<InboxFilters, "queue" | "page" | "sort_by" | "sort_direction">) {
  const params = buildParams(filters);
  params.delete("page");
  params.delete("page_size");
  return useQuery({
    queryKey: inboxKeys.counts(filters),
    queryFn: ({ signal }) =>
      apiClient.get<WorkItemQueueCounts>(`/api/v1/work-items/counts?${params.toString()}`, signal),
  });
}

export function useInboxDetail(id: number | null) {
  return useQuery({
    queryKey: inboxKeys.detail(id ?? ""),
    queryFn: ({ signal }) => apiClient.get<WorkItemDetail>(`/api/v1/work-items/${id}`, signal),
    enabled: id !== null,
  });
}

export function useInboxConversation(id: number | null) {
  return useQuery({
    queryKey: inboxKeys.conversation(id ?? ""),
    queryFn: ({ signal }) =>
      apiClient.get<ConversationPage>(`/api/v1/work-items/${id}/conversation?page=1&page_size=50`, signal),
    enabled: id !== null,
  });
}

export function useInboxAttachments(id: number | null) {
  return useQuery({
    queryKey: inboxKeys.attachments(id ?? ""),
    queryFn: ({ signal }) => apiClient.get<AttachmentSummary>(`/api/v1/work-items/${id}/attachments`, signal),
    enabled: id !== null,
  });
}

// All four mutations invalidate the same shape: this work item's own
// detail, the inbox lists/counts (queue membership may have changed),
// and - for the two load-touching actions - the loads list/detail so the
// Loads screen reflects the new/updated record without a manual reload.
function useInvalidateAfterAction(workItemId: number) {
  const queryClient = useQueryClient();
  return (loadId?: number | null) => {
    queryClient.invalidateQueries({ queryKey: inboxKeys.detail(workItemId) });
    queryClient.invalidateQueries({ queryKey: inboxKeys.lists() });
    queryClient.invalidateQueries({ queryKey: [...inboxKeys.all, "counts"] });
    if (loadId != null) {
      queryClient.invalidateQueries({ queryKey: loadKeys.lists() });
      queryClient.invalidateQueries({ queryKey: loadKeys.detail(loadId) });
    }
  };
}

export function useCreateLoadFromWorkItem(workItemId: number) {
  const invalidate = useInvalidateAfterAction(workItemId);
  return useMutation({
    mutationFn: (payload: CreateLoadRequest) =>
      apiClient.post<CreateLoadResult>(`/api/v1/work-items/${workItemId}/create-load`, payload),
    onSuccess: (result) => invalidate(result.load_id),
  });
}

export function useUpdateLoadFromWorkItem(workItemId: number) {
  const invalidate = useInvalidateAfterAction(workItemId);
  return useMutation({
    mutationFn: (payload: UpdateLoadRequest) =>
      apiClient.post<UpdateLoadResult>(`/api/v1/work-items/${workItemId}/update-load`, payload),
    onSuccess: (result) => invalidate(result.load_id),
  });
}

export function useLinkWorkItemToLoad(workItemId: number) {
  const invalidate = useInvalidateAfterAction(workItemId);
  return useMutation({
    mutationFn: (payload: LinkLoadRequest) =>
      apiClient.post<CommandResult>(`/api/v1/work-items/${workItemId}/link-load`, payload),
    onSuccess: (_result, payload) => invalidate(payload.load_id),
  });
}

export function useCloseWorkItem(workItemId: number) {
  const invalidate = useInvalidateAfterAction(workItemId);
  return useMutation({
    mutationFn: (payload: CloseWorkItemRequest = { reason: "" }) =>
      apiClient.post<CommandResult>(`/api/v1/work-items/${workItemId}/close`, payload),
    onSuccess: () => invalidate(),
  });
}
