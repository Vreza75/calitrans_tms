"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { driverKeys, loadKeys, stageKeys } from "@/lib/api/queryKeys";
import type {
  AssignDriverRequest,
  Driver,
  OperationalStages,
  TransitionLoadRequest,
  TransitionResult,
} from "@/lib/api/types";

export function useActiveDrivers() {
  return useQuery({
    queryKey: driverKeys.active(),
    queryFn: ({ signal }) => apiClient.get<Driver[]>("/api/v1/drivers/active", signal),
  });
}

// Fetched with service_flow="Import" to get the full superset of stages
// (Import is the one move type that includes "Returning Empty" - see
// services/dispatch_stages.py::get_operational_stages) - the board's
// column headers, not per-load eligibility. A specific load's own valid
// next stage is still only ever decided server-side.
export function useOperationalStages(serviceFlow: string) {
  return useQuery({
    queryKey: stageKeys.forServiceFlow(serviceFlow),
    queryFn: ({ signal }) =>
      apiClient.get<OperationalStages>(
        `/api/v1/loads/stages?service_flow=${encodeURIComponent(serviceFlow)}`,
        signal,
      ),
    staleTime: Infinity, // this list is a fixed business rule, not live data
  });
}

// Both mutations below invalidate the same shape the realtime
// invalidation map (lib/realtime/invalidationMap.ts) already uses for
// load.status_changed/load.assignment_changed - so a dispatcher's own
// action updates their screen immediately, without waiting on the
// realtime round-trip.
function useInvalidateLoad(loadId: number) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: loadKeys.lists() });
    queryClient.invalidateQueries({ queryKey: loadKeys.detail(loadId) });
    queryClient.invalidateQueries({ queryKey: loadKeys.timeline(loadId) });
  };
}

export function useAssignDriver(loadId: number) {
  const invalidate = useInvalidateLoad(loadId);
  return useMutation({
    mutationFn: (payload: AssignDriverRequest) =>
      apiClient.post<TransitionResult>(`/api/v1/loads/${loadId}/assign-driver`, payload),
    onSuccess: invalidate,
    // P1 fix: a 409 here means someone else's assignment write already
    // landed on this load (see AssignDriverIn.expected_driver/
    // expected_truck) - this dispatcher's screen is now showing stale
    // data too, so refresh the same queries a successful write would,
    // instead of leaving the stale card/dialog on screen with no hint
    // anything changed underneath it.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) invalidate();
    },
  });
}

export function useTransitionLoad(loadId: number) {
  const invalidate = useInvalidateLoad(loadId);
  return useMutation({
    mutationFn: (payload: TransitionLoadRequest) =>
      apiClient.post<TransitionResult>(`/api/v1/loads/${loadId}/transition`, payload),
    onSuccess: invalidate,
  });
}
