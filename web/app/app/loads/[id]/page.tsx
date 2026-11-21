"use client";

// STEP 30/32/35 + Phase 10C: the load detail/workspace screen.
// Subscribes to the "loads" collection channel, this load's "load:{id}"
// resource channel, and (Phase 10C) the "documents"/"communications"
// collection channels too - collectionChannel() already maps those
// aggregate types (see lib/realtime/channels.ts), this page just never
// subscribed to them before because there was nothing on screen that
// needed them.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { CommunicationsTab } from "@/components/load-workspace/CommunicationsTab";
import { DocumentsTab } from "@/components/load-workspace/DocumentsTab";
import { EditForm } from "@/components/load-workspace/EditForm";
import { NotesEditor } from "@/components/load-workspace/NotesEditor";
import { QuickActions } from "@/components/load-workspace/QuickActions";
import { TimelineTab } from "@/components/load-workspace/TimelineTab";
import { useLoadDetail } from "@/lib/api/useLoads";
import type { LoadDetail } from "@/lib/api/types";
import { collectionChannel, resourceChannel } from "@/lib/realtime/channels";
import { useRealtimeChannels } from "@/lib/realtime/useRealtimeChannels";

const DETAIL_FIELDS: Array<[label: string, key: keyof LoadDetail]> = [
  ["Booking", "booking_number"],
  ["Reference", "reference_number"],
  ["Container", "container_number"],
  ["Customer", "customer"],
  ["Status", "status"],
  ["Driver", "driver_name"],
  ["Truck", "truck_assigned"],
  ["Chassis", "chassis"],
  ["Size", "size"],
  ["Port", "port"],
  ["Warehouse", "warehouse"],
  ["Address", "address"],
  ["Delivery Need Date", "delivery_need_date"],
  ["Document Cutoff", "document_cutoff"],
  ["Load Date", "load_date"],
  ["LFD", "lfd"],
  ["Invoice Status", "invoice_status"],
  ["Driver Pay Status", "driver_pay_status"],
  ["Closeout Stage", "closeout_stage"],
  ["Steamship Line", "steamship_line"],
  ["Vessel Name", "vessel_name"],
  ["Terminal", "terminal"],
  ["Pickup Appointment", "pickup_appointment"],
  ["Delivery Appointment", "delivery_appointment"],
  ["Empty Return Location", "empty_return_location"],
  ["Empty Return Date", "empty_return_date"],
];

const TABS = ["Detail", "Documents", "Communications", "Timeline"] as const;
type Tab = (typeof TABS)[number];

export default function LoadDetailPage() {
  const params = useParams<{ id: string }>();
  const loadId = params.id;
  const [activeTab, setActiveTab] = useState<Tab>("Detail");

  const topics = ["loads", collectionChannel("document"), collectionChannel("dispatch_message")];
  const resource = resourceChannel("load", loadId);
  if (resource) topics.push(resource);
  const connectionState = useRealtimeChannels(topics);

  const { data, isLoading, isError, error, isFetching } = useLoadDetail(loadId);
  const numericLoadId = data ? data.id : null;

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

      {data && numericLoadId !== null && (
        <>
          <nav className="load-workspace__tabs" aria-label="Load workspace sections">
            {TABS.map((tab) => (
              <button
                key={tab}
                type="button"
                className={tab === activeTab ? "load-workspace__tab-button--active" : undefined}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </nav>

          {activeTab === "Detail" && (
            <>
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

              <EditForm load={data} />
              <NotesEditor load={data} />
              <QuickActions load={data} />
            </>
          )}

          {activeTab === "Documents" && <DocumentsTab loadId={numericLoadId} />}
          {activeTab === "Communications" && <CommunicationsTab loadId={numericLoadId} />}
          {activeTab === "Timeline" && <TimelineTab loadId={numericLoadId} />}
        </>
      )}
    </section>
  );
}
