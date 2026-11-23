"use client";

import { useLoadTimeline } from "@/lib/api/useLoadWorkspace";

type Props = {
  loadId: number;
};

export function TimelineTab({ loadId }: Props) {
  const { data, isLoading, isError } = useLoadTimeline(loadId);

  return (
    <div className="load-workspace__tab">
      {isLoading && <p role="status">Loading timeline...</p>}
      {isError && (
        <p role="alert" className="field-error">
          Unable to load the timeline for this load.
        </p>
      )}
      {data && data.items.length === 0 && <p>No timeline events yet.</p>}

      {data && data.items.length > 0 && (
        <ul className="load-workspace__timeline-list">
          {data.items.map((event, index) => (
            <li key={index}>
              <span className="status-badge">{event.event_type}</span>
              <span>{event.title}</span>
              {event.details && <span className="load-workspace__document-meta">{event.details}</span>}
              <span className="load-workspace__document-meta">
                {event.actor} - {event.created_at ? new Date(event.created_at).toLocaleString() : "-"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
