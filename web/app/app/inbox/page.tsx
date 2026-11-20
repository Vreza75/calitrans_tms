"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { WorkItemDetail } from "@/components/inbox/WorkItemDetail";
import { useInboxCounts, useInboxList } from "@/lib/api/useInbox";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";

// Dispatcher-facing queue taxonomy - mirrors order_intake.work_queue
// values already computed server-side (repositories/work_item_repo.py),
// never re-derived here. "All" omits the queue filter entirely.
const QUEUES: { label: string; value: string | null }[] = [
  { label: "All", value: null },
  { label: "New Orders", value: "New Orders" },
  { label: "Existing Load Updates", value: "Existing Load Updates" },
  { label: "Quotes", value: "Quotes" },
  { label: "Appointments / PIN", value: "Appointments / PIN" },
  { label: "Documents", value: "Documents" },
  { label: "Billing", value: "Billing" },
  { label: "Needs Review", value: "Needs Review" },
  { label: "Store Only / Archive", value: "Store Only / Archive" },
];

function formatReceived(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Phase 10A: Operations Inbox proof - real work-item read model
// (GET /api/v1/work-items, already established by Phase 5B/9, never
// re-fetched-and-filtered client-side) plus realtime invalidation on
// inbox.received / inbox.review_status_changed. No manual Sync/Refresh/
// Recheck controls here - routine processing is fully automated (see
// docs/architecture/OPERATIONS_INBOX_WEB.md); those remain in
// Streamlit's Admin/Diagnostics only.
export default function InboxPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id") ? Number(searchParams.get("id")) : null;
  const queue = searchParams.get("queue");

  const [search, setSearch] = useState(searchParams.get("search") ?? "");
  const [page, setPage] = useState(1);
  const connectionState = useRealtimeChannels(["inbox"]);

  const filters = { queue: queue ?? undefined, search: search || undefined, page };
  const { data, isLoading, isError, error, isFetching } = useInboxList(filters);
  const { data: counts } = useInboxCounts({ search: search || undefined });

  function selectWorkItem(id: number) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("id", String(id));
    router.push(`/app/inbox?${params.toString()}`);
  }

  function selectQueue(value: string | null) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("queue", value);
    else params.delete("queue");
    params.delete("id");
    setPage(1);
    router.push(`/app/inbox?${params.toString()}`);
  }

  function countFor(queueValue: string | null): number | null {
    if (!counts) return null;
    if (queueValue === null) return counts.counts.reduce((sum, row) => sum + row.count, 0);
    return counts.counts.find((row) => row.queue === queueValue)?.count ?? 0;
  }

  return (
    <section className="inbox-workspace">
      <div className="page-header">
        <h1>Operations Inbox</h1>
        <ConnectionIndicator state={connectionState} />
      </div>

      <form
        className="search-bar"
        onSubmit={(event) => {
          event.preventDefault();
          setPage(1);
        }}
      >
        <input
          type="search"
          placeholder="Search subject, sender, booking, container, reference..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="submit">Search</button>
        {isFetching && !isLoading && <span className="inline-status">Refreshing...</span>}
      </form>

      <div className="inbox-layout">
        <aside className="inbox-queue-nav" aria-label="Operations queues">
          <ul>
            {QUEUES.map((item) => {
              const count = countFor(item.value);
              const active = (queue ?? null) === item.value;
              return (
                <li key={item.label}>
                  <button
                    type="button"
                    className={active ? "inbox-queue-nav__item inbox-queue-nav__item--active" : "inbox-queue-nav__item"}
                    onClick={() => selectQueue(item.value)}
                  >
                    <span>{item.label}</span>
                    {count !== null && <span className="inbox-queue-nav__count">{count}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <div className="inbox-list">
          {isLoading && <p role="status">Loading work items...</p>}
          {isError && (
            <p role="alert" className="field-error">
              Unable to load the inbox. {error instanceof Error ? error.message : ""}
            </p>
          )}
          {data && data.items.length === 0 && <p>No work items match this queue/search.</p>}

          {data && data.items.length > 0 && (
            <>
              <ul className="inbox-work-item-list">
                {data.items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={
                        selectedId === item.id ? "inbox-work-item-row inbox-work-item-row--active" : "inbox-work-item-row"
                      }
                      onClick={() => selectWorkItem(item.id)}
                    >
                      <span className="inbox-work-item-row__time">{formatReceived(item.source_received_at)}</span>
                      <span className="inbox-work-item-row__main">
                        <span className="inbox-work-item-row__sender">{item.customer || item.source_sender}</span>
                        <span className="inbox-work-item-row__subject">{item.source_subject}</span>
                      </span>
                      <span className="inbox-work-item-row__meta">
                        {item.booking_number && <span className="status-badge">{item.booking_number}</span>}
                        {item.attachment_count > 0 && <span title="Has attachments">📎{item.attachment_count}</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>

              <div className="pagination">
                <button type="button" disabled={data.page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </button>
                <span>
                  Page {data.page} of {data.total_pages || 1} ({data.total_items} total)
                </span>
                <button type="button" disabled={data.page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </button>
              </div>
            </>
          )}
        </div>

        <div className="inbox-detail">
          {selectedId === null ? (
            <p className="inbox-detail__empty">Select a work item to review it here.</p>
          ) : (
            <WorkItemDetail workItemId={selectedId} />
          )}
        </div>
      </div>
    </section>
  );
}
