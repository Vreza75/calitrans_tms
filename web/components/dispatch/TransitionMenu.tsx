"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { useOperationalStages, useTransitionLoad } from "@/lib/api/useDispatch";
import type { LoadListItem } from "@/lib/api/types";

type Props = {
  load: LoadListItem;
  onClose: () => void;
};

const CANCELLED_STATUS = "Cancelled";

// Phase 10B: this menu only ever sequences off the stage list the server
// returns (GET /loads/stages) and lets the server's own apply_transition
// validation (services/dispatch_stages.py::validate_transition) decide
// what's actually allowed - it never re-derives the driver/truck/origin/
// sequencing rules itself (CLAUDE.md: don't duplicate business logic in
// the frontend). A rejected transition surfaces the server's message
// verbatim rather than attempting to predict it client-side.
export function TransitionMenu({ load, onClose }: Props) {
  const { data: stageData, isLoading: isLoadingStages } = useOperationalStages(load.type);
  const transition = useTransitionLoad(load.id);
  const [overrideTarget, setOverrideTarget] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [showOverride, setShowOverride] = useState(false);

  const stages = stageData?.stages ?? [];
  const currentIndex = stages.indexOf(load.status);
  const nextStage = currentIndex >= 0 && currentIndex + 1 < stages.length ? stages[currentIndex + 1] : null;

  function advanceTo(newStatus: string, options: { override?: boolean; overrideReason?: string } = {}) {
    transition.mutate(
      {
        new_status: newStatus,
        note: "",
        override: options.override ?? false,
        override_reason: options.overrideReason ?? "",
      },
      { onSuccess: onClose },
    );
  }

  function handleOverrideSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!overrideTarget || !overrideReason.trim()) return;
    advanceTo(overrideTarget, { override: true, overrideReason: overrideReason.trim() });
  }

  return (
    <div className="dispatch-dialog" aria-label={`Transition load ${load.booking_number}`}>
      {isLoadingStages && <p role="status">Loading valid stages...</p>}

      {transition.isError && (
        <p role="alert" className="field-error">
          {transition.error instanceof ApiError ? transition.error.message : "Unable to transition this load."}
        </p>
      )}

      {!showOverride && (
        <div className="dispatch-dialog__actions">
          {nextStage && (
            <button type="button" disabled={transition.isPending} onClick={() => advanceTo(nextStage)}>
              {transition.isPending ? "Updating..." : `Advance to "${nextStage}"`}
            </button>
          )}
          {load.status !== CANCELLED_STATUS && (
            <button type="button" disabled={transition.isPending} onClick={() => advanceTo(CANCELLED_STATUS)}>
              Cancel Load
            </button>
          )}
          <button type="button" onClick={() => setShowOverride(true)} disabled={transition.isPending}>
            Override...
          </button>
          <button type="button" onClick={onClose} disabled={transition.isPending}>
            Close
          </button>
        </div>
      )}

      {showOverride && (
        <form onSubmit={handleOverrideSubmit}>
          <div className="field">
            <label htmlFor={`override-target-${load.id}`}>Move to</label>
            <select
              id={`override-target-${load.id}`}
              value={overrideTarget}
              onChange={(event) => setOverrideTarget(event.target.value)}
            >
              <option value="">Select a stage...</option>
              {stages.map((stage) => (
                <option key={stage} value={stage}>
                  {stage}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`override-reason-${load.id}`}>Reason (required)</label>
            <input
              id={`override-reason-${load.id}`}
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              required
            />
          </div>
          <div className="dispatch-dialog__actions">
            <button type="button" onClick={() => setShowOverride(false)} disabled={transition.isPending}>
              Back
            </button>
            <button type="submit" disabled={!overrideTarget || !overrideReason.trim() || transition.isPending}>
              {transition.isPending ? "Updating..." : "Confirm Override"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
