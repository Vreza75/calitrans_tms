"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useCancelLoad, useMarkMissingInfo, useVerifyBooking } from "@/lib/api/useLoadWorkspace";
import type { LoadDetail } from "@/lib/api/types";

type Props = {
  load: LoadDetail;
};

const CANCELLED_STATUS = "Cancelled";

// Phase 10C: the three Streamlit "Quick Actions" (Mark Missing Info /
// Mark Booking Verified / Cancel Order), each a thin wrapper over its
// own already-permission-gated command. Cosmetic-only client-side
// gating per button, same pattern as DispatchCard's hasPermission check
// - the server-side require_permission call inside each command is the
// real boundary.
export function QuickActions({ load }: Props) {
  const { hasPermission } = useAuth();
  const [note, setNote] = useState("");

  const markMissingInfo = useMarkMissingInfo(load.id);
  const verifyBooking = useVerifyBooking(load.id);
  const cancelLoad = useCancelLoad(load.id);

  const canEdit = hasPermission("load:edit");
  const canVerify = hasPermission("load:verify");
  const canCancel = hasPermission("load:cancel");
  const pending = markMissingInfo.isPending || verifyBooking.isPending || cancelLoad.isPending;
  const activeError = markMissingInfo.error ?? verifyBooking.error ?? cancelLoad.error;

  if (!canEdit && !canVerify && !canCancel) return null;

  return (
    <div className="load-workspace__quick-actions">
      <div className="field">
        <label htmlFor={`quick-action-note-${load.id}`}>Note (optional)</label>
        <input id={`quick-action-note-${load.id}`} value={note} onChange={(event) => setNote(event.target.value)} />
      </div>

      <div className="dispatch-dialog__actions">
        {canEdit && (
          <button type="button" disabled={pending} onClick={() => markMissingInfo.mutate({ note })}>
            Mark Missing Info
          </button>
        )}
        {canVerify && (
          <button type="button" disabled={pending} onClick={() => verifyBooking.mutate({ note })}>
            Mark Booking Verified
          </button>
        )}
        {canCancel && load.status !== CANCELLED_STATUS && (
          <button type="button" disabled={pending} onClick={() => cancelLoad.mutate({ note })}>
            Cancel Load
          </button>
        )}
      </div>

      {activeError && (
        <p role="alert" className="field-error">
          {activeError instanceof ApiError ? activeError.message : "Unable to complete this action."}
        </p>
      )}
    </div>
  );
}
