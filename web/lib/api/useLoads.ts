"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { type LoadSearchFilters, loadKeys } from "@/lib/api/queryKeys";
import type { LoadDetail, LoadPage } from "@/lib/api/types";

export function useLoadSearch(filters: LoadSearchFilters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.service_flow) params.set("service_flow", filters.service_flow);
  if (filters.customer) params.set("customer", filters.customer);
  if (filters.driver_name) params.set("driver_name", filters.driver_name);
  if (filters.port) params.set("port", filters.port);
  if (filters.warehouse) params.set("warehouse", filters.warehouse);
  if (filters.invoice_status) params.set("invoice_status", filters.invoice_status);
  if (filters.search) params.set("search", filters.search);
  if (filters.delivery_after) params.set("delivery_after", filters.delivery_after);
  if (filters.delivery_before) params.set("delivery_before", filters.delivery_before);
  params.set("page", String(filters.page ?? 1));
  params.set("limit", String(filters.limit ?? 25));

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
