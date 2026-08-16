"use client";

import Link from "next/link";
import { useState } from "react";

import { useLoadSearch } from "@/lib/api/useLoads";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";

// STEP 30/31: Phase 8's paginated/searched load read model, via the API
// - never a direct DB query. Proves API reads + realtime list
// invalidation; not a replacement for the full Dispatch Board.
export default function LoadsPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const connectionState = useRealtimeChannels(["loads"]);

  const { data, isLoading, isError, error, isFetching } = useLoadSearch({ search: search || undefined, page });

  return (
    <section>
      <div className="page-header">
        <h1>Loads</h1>
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
          placeholder="Search booking, reference, container, customer..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="submit">Search</button>
        {isFetching && !isLoading && <span className="inline-status">Refreshing...</span>}
      </form>

      {isLoading && <p role="status">Loading loads...</p>}

      {isError && (
        <p role="alert" className="field-error">
          Unable to load this list. {error instanceof Error ? error.message : ""}
        </p>
      )}

      {data && data.items.length === 0 && <p>No loads match this search.</p>}

      {data && data.items.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Booking</th>
                <th>Customer</th>
                <th>Status</th>
                <th>Driver</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link href={`/app/loads/${item.id}`}>{item.booking_number || `#${item.id}`}</Link>
                  </td>
                  <td>{item.customer}</td>
                  <td>
                    <span className="status-badge">{item.status}</span>
                  </td>
                  <td>{item.driver_name || "-"}</td>
                  <td>{item.updated_at ? new Date(item.updated_at).toLocaleString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>

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
    </section>
  );
}
