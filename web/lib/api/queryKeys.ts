// Stable query-key factories - the single source of truth for how a
// resource's cache entries are keyed, so realtime invalidation
// (lib/realtime/invalidationMap.ts) and components never drift onto
// slightly different key shapes for the same resource.

export type LoadSearchFilters = {
  status?: string;
  customer?: string;
  search?: string;
  page?: number;
};

export const loadKeys = {
  all: ["loads"] as const,
  lists: () => [...loadKeys.all, "list"] as const,
  list: (filters: LoadSearchFilters) => [...loadKeys.lists(), filters] as const,
  details: () => [...loadKeys.all, "detail"] as const,
  detail: (id: number | string) => [...loadKeys.details(), String(id)] as const,
  timeline: (id: number | string) => [...loadKeys.all, "timeline", String(id)] as const,
  communications: (id: number | string) => [...loadKeys.all, "communications", String(id)] as const,
  documents: (id: number | string) => [...loadKeys.all, "documents", String(id)] as const,
};

export const inboxKeys = {
  all: ["inbox"] as const,
  lists: () => [...inboxKeys.all, "list"] as const,
  list: (filters: Record<string, unknown> = {}) => [...inboxKeys.lists(), filters] as const,
  detail: (id: number | string) => [...inboxKeys.all, "detail", String(id)] as const,
};

export const meKeys = {
  current: ["me"] as const,
};
