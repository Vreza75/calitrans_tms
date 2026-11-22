"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useUpdateLoadFields } from "@/lib/api/useLoadWorkspace";
import type { LoadDetail } from "@/lib/api/types";

type Props = {
  load: LoadDetail;
};

// P1 fix: "chassis" removed - dispatch-governed (bundled into
// mark_load_ready_to_dispatch's guarded assignment), rejected
// server-side now by the general update endpoint regardless.
const EDITABLE_FIELDS: Array<[label: string, key: keyof LoadDetail]> = [
  ["Customer", "customer"],
  ["Port / Pickup", "port"],
  ["Warehouse / Delivery", "warehouse"],
  ["Address", "address"],
];

// Phase 10C: free-form multi-field save, wraps application/loads/
// commands.py::update_load_fields unchanged - the Next.js equivalent of
// the Streamlit Order Detail Editor's "Save Order Updates" button.
//
// P1 fix: `values` used to be seeded once from `load` at mount and then
// diffed against the live `load` prop at submit time - if a realtime
// update refreshed `load` between mount and submit (someone else's
// concurrent edit), an UNTOUCHED field would suddenly "differ" from the
// stale mount-time snapshot and get silently resubmitted with that
// stale value, overwriting the other person's change. Fixed by storing
// only the user's actual edits (`edits`, keyed by field) rather than a
// full merged snapshot: an untouched field is derived straight from the
// live `load` prop on every render (always current, never stale), a
// touched field shows and submits exactly what the user typed, and
// nothing is ever computed by diffing against a point-in-time copy of
// the server's data.
export function EditForm({ load }: Props) {
  const { hasPermission } = useAuth();
  const updateFields = useUpdateLoadFields(load.id);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState(false);

  if (!hasPermission("load:edit")) return null;

  function displayValue(key: keyof LoadDetail): string {
    return edits[key as string] ?? String(load[key] ?? "");
  }

  function handleChange(key: string, value: string) {
    setEdits((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (Object.keys(edits).length === 0) return;
    setConflict(false);
    updateFields.mutate(
      { updates: edits, expected_updated_at: load.updated_at },
      {
        onSuccess: () => setEdits({}),
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) setConflict(true);
        },
      },
    );
  }

  const hasChanges = Object.keys(edits).length > 0;

  return (
    <form className="load-workspace__edit-form" onSubmit={handleSubmit} aria-label="Edit load">
      {EDITABLE_FIELDS.map(([label, key]) => (
        <div className="field" key={String(key)}>
          <label htmlFor={`edit-${key}-${load.id}`}>{label}</label>
          <input
            id={`edit-${key}-${load.id}`}
            value={displayValue(key)}
            onChange={(event) => handleChange(key, event.target.value)}
          />
        </div>
      ))}

      {conflict && (
        <p role="alert" className="field-error">
          This load was changed by someone else since you opened it. Your unsaved edits above are kept - review the
          refreshed fields and save again when ready.
        </p>
      )}
      {updateFields.isError && !conflict && (
        <p role="alert" className="field-error">
          {updateFields.error instanceof ApiError ? updateFields.error.message : "Unable to save these changes."}
        </p>
      )}
      {updateFields.isSuccess && <p className="inline-status">Saved.</p>}

      <button type="submit" disabled={!hasChanges || updateFields.isPending}>
        {updateFields.isPending ? "Saving..." : "Save Changes"}
      </button>
    </form>
  );
}
