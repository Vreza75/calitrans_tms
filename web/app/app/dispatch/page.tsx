"use client";

import { useState } from "react";

import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { DispatchColumn } from "@/components/dispatch/DispatchColumn";
import { useLoadSearch } from "@/lib/api/useLoads";
import { useOperationalStages } from "@/lib/api/useDispatch";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";
import type { LoadListItem } from "@/lib/api/types";

const OTHER_STAGE_LABEL = "Other (Not Yet Dispatched / Closed)";

// Phase 10B: the board groups search results into columns named after
// GET /loads/stages's own stage list (fetched once with service_flow
// "Import" to get the full 7-stage superset - see useOperationalStages'
// own comment) - any load whose status isn't one of those stages (New,
// Booking Verified, Hold/Need Info, Cancelled, etc.) lands in a visible
// "Other" bucket instead of being silently dropped from the board.
function groupLoadsByStage(loads: LoadListItem[], stages: string[]): Map<string, LoadListItem[]> {
  const groups = new Map<string, LoadListItem[]>();
  for (const stage of stages) groups.set(stage, []);
  groups.set(OTHER_STAGE_LABEL, []);

  for (const load of loads) {
    const bucket = groups.has(load.status) ? load.status : OTHER_STAGE_LABEL;
    groups.get(bucket)!.push(load);
  }
  return groups;
}

export default function DispatchBoardPage() {
  const [search, setSearch] = useState("");
  const [driverName, setDriverName] = useState("");
  const [port, setPort] = useState("");
  const [warehouse, setWarehouse] = useState("");

  const connectionState = useRealtimeChannels(["loads"]);
  const { data: stageData, isLoading: isLoadingStages } = useOperationalStages("Import");
  const { data, isLoading, isError, error, isFetching } = useLoadSearch({
    search: search || undefined,
    driver_name: driverName || undefined,
    port: port || undefined,
    warehouse: warehouse || undefined,
    limit: 200,
  });

  const stages = stageData?.stages ?? [];
  const groups = data ? groupLoadsByStage(data.items, stages) : null;

  return (
    <section>
      <div className="page-header">
        <h1>Dispatch Board</h1>
        <ConnectionIndicator state={connectionState} />
      </div>

      <form
        className="search-bar"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Dispatch board filters"
      >
        <input
          type="search"
          placeholder="Search booking, reference, container, customer..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <input
          type="text"
          placeholder="Driver"
          value={driverName}
          onChange={(event) => setDriverName(event.target.value)}
        />
        <input type="text" placeholder="Port" value={port} onChange={(event) => setPort(event.target.value)} />
        <input
          type="text"
          placeholder="Warehouse"
          value={warehouse}
          onChange={(event) => setWarehouse(event.target.value)}
        />
        {isFetching && !isLoading && <span className="inline-status">Refreshing...</span>}
      </form>

      {(isLoading || isLoadingStages) && <p role="status">Loading dispatch board...</p>}

      {isError && (
        <p role="alert" className="field-error">
          Unable to load the dispatch board. {error instanceof Error ? error.message : ""}
        </p>
      )}

      {data && data.items.length === 0 && <p>No loads match this filter.</p>}

      {data && data.total_items > data.items.length && (
        <p role="alert" className="field-error">
          Showing {data.items.length} of {data.total_items} matching loads. Narrow the filters above to see the rest -
          the board never silently drops loads past this page.
        </p>
      )}

      {groups && data && data.items.length > 0 && (
        <div className="dispatch-board">
          {Array.from(groups.entries()).map(([stage, loads]) => (
            <DispatchColumn key={stage} stage={stage} loads={loads} />
          ))}
        </div>
      )}
    </section>
  );
}
