"use client";

import { useState } from "react";

import {
  useCloseWorkItem,
  useCreateLoadFromWorkItem,
  useInboxAttachments,
  useInboxConversation,
  useInboxDetail,
  useLinkWorkItemToLoad,
  useUpdateLoadFromWorkItem,
} from "@/lib/api/useInbox";
import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";

const WORK_ITEM_MANAGE = "work_item:manage";

// Backend failure -> operator-facing message (Step 39). Never render raw
// exception text - api/errors.py's envelope already carries a safe
// message, but a 409/403/404 still deserve a workflow-specific one here.
function actionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "This item changed since you loaded it. Refreshing current state.";
    if (error.status === 403) return "You don't have permission to perform this action.";
    if (error.status === 404) return "This work item is no longer available.";
    return "Unable to complete this action.";
  }
  return "Unable to complete this action.";
}

export function WorkItemDetail({ workItemId }: { workItemId: number }) {
  const { hasPermission } = useAuth();
  const canManage = hasPermission(WORK_ITEM_MANAGE);

  const { data: detail, isLoading, isError } = useInboxDetail(workItemId);
  const { data: conversation } = useInboxConversation(workItemId);
  const { data: attachments } = useInboxAttachments(workItemId);

  const createLoad = useCreateLoadFromWorkItem(workItemId);
  const updateLoad = useUpdateLoadFromWorkItem(workItemId);
  const linkLoad = useLinkWorkItemToLoad(workItemId);
  const closeItem = useCloseWorkItem(workItemId);

  const [linkLoadId, setLinkLoadId] = useState("");
  const [updateLoadId, setUpdateLoadId] = useState("");
  const [fillBlankOnly, setFillBlankOnly] = useState(true);

  if (isLoading) return <p role="status">Loading work item...</p>;
  if (isError || !detail) return <p role="alert" className="field-error">This work item is no longer available.</p>;

  const pending = createLoad.isPending || updateLoad.isPending || linkLoad.isPending || closeItem.isPending;
  const anyActionError = createLoad.error ?? updateLoad.error ?? linkLoad.error ?? closeItem.error;

  return (
    <div className="inbox-work-item-detail">
      <header className="inbox-detail-header">
        <h2>{detail.source_subject || "(no subject)"}</h2>
        <p className="inbox-detail-header__sender">{detail.source_sender}</p>
        <div className="inbox-detail-header__chips">
          <span className="status-badge">{detail.review_status}</span>
          <span className="status-badge">{detail.request_type}</span>
          {detail.matched_load_id && <span className="status-badge">Matched: Load #{detail.matched_load_id}</span>}
          <span className="status-badge">{detail.confidence_score}% confidence</span>
        </div>
        <p className="inbox-detail-header__received">
          Received: {detail.source_received_at ? new Date(detail.source_received_at).toLocaleString() : "-"}
        </p>
      </header>

      <section aria-label="Extracted fields">
        <h3>Extracted Fields</h3>
        {Object.keys(detail.parsed_data).length === 0 ? (
          <p className="inbox-detail__empty">No fields extracted yet - this item may still be processing.</p>
        ) : (
          <dl className="detail-grid">
            {Object.entries(detail.parsed_data).map(([field, value]) => (
              <div className="detail-grid__row" key={field}>
                <dt>{field}</dt>
                <dd>{value ? String(value) : <em>missing</em>}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      <section aria-label="Match and recommendation">
        <h3>Match</h3>
        {detail.matched_load_id ? (
          <p>Matched to Load #{detail.matched_load_id}.</p>
        ) : (
          <p className="inbox-detail__empty">No load matched yet.</p>
        )}
      </section>

      <section aria-label="Conversation timeline">
        <h3>Conversation</h3>
        {!conversation || conversation.messages.length === 0 ? (
          <p className="inbox-detail__empty">No conversation history.</p>
        ) : (
          <ul className="inbox-conversation">
            {conversation.messages.map((message) => (
              <li key={message.id}>
                <span className="inbox-conversation__meta">
                  {message.email_direction} · {message.source_sender} ·{" "}
                  {message.source_received_at ? new Date(message.source_received_at).toLocaleString() : "-"}
                </span>
                <p>{message.message_preview}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Attachments">
        <h3>Attachments</h3>
        {!attachments || (attachments.current.length === 0 && attachments.prior.length === 0) ? (
          <p className="inbox-detail__empty">No attachments.</p>
        ) : (
          <ul className="inbox-attachments">
            {attachments.current.map((file) => (
              <li key={file.attachment_ref}>
                <a href={`/api/v1/attachments/${file.attachment_ref}/content`} target="_blank" rel="noreferrer">
                  {file.filename}
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Actions" className="inbox-actions">
        <h3>Actions</h3>
        {!canManage && <p className="inline-status">Your role does not have permission to act on work items.</p>}
        {anyActionError && <p role="alert" className="field-error">{actionErrorMessage(anyActionError)}</p>}

        <div className="inbox-actions__row">
          {detail.allowed_actions.includes("create_load") && (
            <button
              type="button"
              disabled={!canManage || pending}
              onClick={() => createLoad.mutate({ approved_fields: detail.parsed_data })}
            >
              Create Load
            </button>
          )}

          {detail.allowed_actions.includes("update_existing_load") && (
            <span className="inbox-actions__group">
              <input
                type="number"
                placeholder="Load ID"
                value={updateLoadId || String(detail.matched_load_id ?? "")}
                onChange={(event) => setUpdateLoadId(event.target.value)}
              />
              <label>
                <input
                  type="checkbox"
                  checked={fillBlankOnly}
                  onChange={(event) => setFillBlankOnly(event.target.checked)}
                />
                Fill blank fields only
              </label>
              <button
                type="button"
                disabled={!canManage || pending || !updateLoadId && !detail.matched_load_id}
                onClick={() =>
                  updateLoad.mutate({
                    load_id: Number(updateLoadId || detail.matched_load_id),
                    approved_fields: detail.parsed_data,
                    fill_blank_only: fillBlankOnly,
                  })
                }
              >
                Update Load
              </button>
            </span>
          )}

          {detail.allowed_actions.includes("link_to_load") && (
            <span className="inbox-actions__group">
              <input
                type="number"
                placeholder="Load ID"
                value={linkLoadId}
                onChange={(event) => setLinkLoadId(event.target.value)}
              />
              <button
                type="button"
                disabled={!canManage || pending || !linkLoadId}
                onClick={() => linkLoad.mutate({ load_id: Number(linkLoadId) })}
              >
                Link
              </button>
            </span>
          )}

          {detail.allowed_actions.includes("close_work_item") && (
            <button type="button" disabled={!canManage || pending} onClick={() => closeItem.mutate({ reason: "" })}>
              Close
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
