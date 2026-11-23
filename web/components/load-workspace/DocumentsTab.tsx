"use client";

import { useRef } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useAttachDocument, useLoadDocuments } from "@/lib/api/useLoadWorkspace";

type Props = {
  loadId: number;
};

export function DocumentsTab({ loadId }: Props) {
  const { hasPermission } = useAuth();
  const canAttach = hasPermission("document:attach");
  const { data, isLoading, isError } = useLoadDocuments(loadId);
  const attach = useAttachDocument(loadId);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    attach.mutate(file, {
      onSettled: () => {
        if (fileInputRef.current) fileInputRef.current.value = "";
      },
    });
  }

  return (
    <div className="load-workspace__tab">
      {canAttach && (
        <div className="field">
          <label htmlFor={`document-upload-${loadId}`}>Attach a document</label>
          <input
            id={`document-upload-${loadId}`}
            ref={fileInputRef}
            type="file"
            onChange={handleFileChange}
            disabled={attach.isPending}
          />
          {attach.isPending && <p className="inline-status">Uploading...</p>}
          {attach.isError && (
            <p role="alert" className="field-error">
              {attach.error instanceof ApiError ? attach.error.message : "Unable to upload this document."}
            </p>
          )}
        </div>
      )}

      {isLoading && <p role="status">Loading documents...</p>}
      {isError && (
        <p role="alert" className="field-error">
          Unable to load documents for this load.
        </p>
      )}
      {data && data.length === 0 && <p>No documents attached yet.</p>}

      {data && data.length > 0 && (
        <ul className="load-workspace__document-list">
          {data.map((doc) => (
            <li key={doc.id}>
              <span>{doc.filename}</span>
              <span className="status-badge">{doc.status}</span>
              <span className="load-workspace__document-meta">
                {doc.created_at ? new Date(doc.created_at).toLocaleString() : "-"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
