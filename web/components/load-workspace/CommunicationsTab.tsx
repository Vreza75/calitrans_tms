"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useLoadCommunications, useLogCommunication } from "@/lib/api/useLoadWorkspace";

type Props = {
  loadId: number;
};

const MESSAGE_TYPES = ["note", "driver_communication", "customer_note", "operational_note"] as const;

export function CommunicationsTab({ loadId }: Props) {
  const { hasPermission } = useAuth();
  const canLog = hasPermission("dispatch:communication_log");
  const { data, isLoading, isError } = useLoadCommunications(loadId);
  const logCommunication = useLogCommunication(loadId);
  const [messageType, setMessageType] = useState<string>(MESSAGE_TYPES[0]);
  const [messageBody, setMessageBody] = useState("");

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!messageBody.trim()) return;
    logCommunication.mutate(
      { message_type: messageType, direction: "internal", recipient: "", message_body: messageBody.trim() },
      { onSuccess: () => setMessageBody("") },
    );
  }

  return (
    <div className="load-workspace__tab">
      {canLog && (
        <form onSubmit={handleSubmit} aria-label="Log a communication">
          <div className="field">
            <label htmlFor={`comm-type-${loadId}`}>Type</label>
            <select id={`comm-type-${loadId}`} value={messageType} onChange={(event) => setMessageType(event.target.value)}>
              {MESSAGE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`comm-body-${loadId}`}>Note</label>
            <textarea
              id={`comm-body-${loadId}`}
              value={messageBody}
              onChange={(event) => setMessageBody(event.target.value)}
              rows={3}
            />
          </div>
          {logCommunication.isError && (
            <p role="alert" className="field-error">
              {logCommunication.error instanceof ApiError ? logCommunication.error.message : "Unable to log this note."}
            </p>
          )}
          <button type="submit" disabled={!messageBody.trim() || logCommunication.isPending}>
            {logCommunication.isPending ? "Saving..." : "Save Note"}
          </button>
        </form>
      )}

      {isLoading && <p role="status">Loading communications...</p>}
      {isError && (
        <p role="alert" className="field-error">
          Unable to load communications for this load.
        </p>
      )}
      {data && data.items.length === 0 && <p>No communications logged yet.</p>}

      {data && data.items.length > 0 && (
        <ul className="load-workspace__communication-list">
          {data.items.map((comm) => (
            <li key={comm.id}>
              <span className="status-badge">{comm.message_type}</span>
              <span>{comm.message_body}</span>
              <span className="load-workspace__document-meta">
                {comm.sent_by} - {comm.created_at ? new Date(comm.created_at).toLocaleString() : "-"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
