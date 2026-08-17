"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { type LoadSearchFilters, loadKeys } from "@/lib/api/queryKeys";
import type { LoadDetail, LoadPage } from "@/lib/api/types";

export function useLoadSearch(filters: LoadSearchFilters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.customer) params.set("customer", filters.customer);
  if (filters.search) params.set("search", filters.search);
  params.set("page", String(filters.page ?? 1));
  params.set("limit", "25");

  return useQuery({
    queryKey: loadKeys.list(filters),
    queryFn: ({ signal }) => apiClient.get<LoadPage>(`/api/v1/loads/search?${params.toString()}`, signal),
  });
}

export function useLoadDetail(loadId: string | number | null) {
  return useQuery({
    queryKey: loadKeys.detail(loadId ?? ""),
    queryFn: ({ signal }) => apiClient.get<LoadDetail>(`/api/v1/loads/${loadId}/detail`, signal),
    enabled: loadId !== null,
  });
}
