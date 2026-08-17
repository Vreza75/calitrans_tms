"use client";

// STEP 30/32/35: the load detail proof screen. Subscribes to both the
// "loads" collection channel and this load's "load:{id}" resource
// channel (STEP 21 - matches realtime/channels.py::channels_for
// exactly). This is the screen the two-browser acceptance test drives:
// change the same load elsewhere -> a domain event broadcasts -> this
// screen's queries invalidate -> refetches the API -> re-renders, with
// no manual refresh and no Streamlit rerun involved.
import Link from "next/link";
import { useParams } from "next/navigation";

import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { useLoadDetail } from "@/lib/api/useLoads";
import type { LoadDetail } from "@/lib/api/types";
import { resourceChannel } from "@/lib/realtime/channels";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";

const DETAIL_FIELDS: Array<[label: string, key: keyof LoadDetail]> = [
  ["Booking", "booking_number"],
  ["Reference", "reference_number"],
  ["Container", "container_number"],
  ["Customer", "customer"],
  ["Status", "status"],
  ["Driver", "driver_name"],
  ["Truck", "truck_assigned"],
  ["Port", "port"],
  ["Warehouse", "warehouse"],
  ["Address", "address"],
  ["Delivery Need Date", "delivery_need_date"],
  ["Document Cutoff", "document_cutoff"],
];

export default function LoadDetailPage() {
  const params = useParams<{ id: string }>();
  const loadId = params.id;

  const topics = ["loads"];
  const resource = resourceChannel("load", loadId);
  if (resource) topics.push(resource);
  const connectionState = useRealtimeChannels(topics);

  const { data, isLoading, isError, error, isFetching } = useLoadDetail(loadId);

  return (
    <section>
      <div className="page-header">
        <div>
          <Link href="/app/loads">&larr; Back to Loads</Link>
          <h1>Load {data?.booking_number ?? loadId}</h1>
        </div>
        <ConnectionIndicator state={connectionState} />
      </div>

      {isFetching && !isLoading && <p className="inline-status">Refreshing from a live update...</p>}

      {isLoading && <p role="status">Loading load...</p>}

      {isError && (
        <p role="alert" className="field-error">
          Unable to load this record. {error instanceof Error ? error.message : ""}
        </p>
      )}

      {data && (
        <dl className="detail-grid">
          {DETAIL_FIELDS.map(([label, key]) => (
            <div key={String(key)} className="detail-grid__row">
              <dt>{label}</dt>
              <dd>{String(data[key] ?? "-") || "-"}</dd>
            </div>
          ))}
          <div className="detail-grid__row">
            <dt>Updated</dt>
            <dd>{data.updated_at ? new Date(data.updated_at).toLocaleString() : "-"}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}
