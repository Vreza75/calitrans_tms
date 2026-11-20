// Stable query-key factories - the single source of truth for how a
// resource's cache entries are keyed, so realtime invalidation
// (lib/realtime/invalidationMap.ts) and components never drift onto
// slightly different key shapes for the same resource.

export type LoadSearchFilters = {
  status?: string;
  service_flow?: string;
  customer?: string;
  driver_name?: string;
  port?: string;
  warehouse?: string;
  invoice_status?: string;
  search?: string;
  delivery_after?: string;
  delivery_before?: string;
  page?: number;
  limit?: number;
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

export type InboxFilters = {
  queue?: string;
  service_flow?: string;
  customer?: string;
  search?: string;
  subject?: string;
  status?: string;
  attachment_status?: string;
  page?: number;
  sort_by?: string;
  sort_direction?: string;
};

export const inboxKeys = {
  all: ["inbox"] as const,
  lists: () => [...inboxKeys.all, "list"] as const,
  list: (filters: InboxFilters = {}) => [...inboxKeys.lists(), filters] as const,
  detail: (id: number | string) => [...inboxKeys.all, "detail", String(id)] as const,
  counts: (filters: Omit<InboxFilters, "queue" | "page" | "sort_by" | "sort_direction"> = {}) =>
    [...inboxKeys.all, "counts", filters] as const,
  conversation: (id: number | string) => [...inboxKeys.all, "conversation", String(id)] as const,
  attachments: (id: number | string) => [...inboxKeys.all, "attachments", String(id)] as const,
};

export const meKeys = {
  current: ["me"] as const,
};

export const driverKeys = {
  all: ["drivers"] as const,
  active: () => [...driverKeys.all, "active"] as const,
};

export const stageKeys = {
  all: ["load-stages"] as const,
  forServiceFlow: (serviceFlow: string) => [...stageKeys.all, serviceFlow] as const,
};
