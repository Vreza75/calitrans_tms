"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useUpdateLoadFields } from "@/lib/api/useLoadWorkspace";
import type { LoadDetail } from "@/lib/api/types";

type Props = {
  load: LoadDetail;
};

const EDITABLE_FIELDS: Array<[label: string, key: keyof LoadDetail]> = [
  ["Customer", "customer"],
  ["Port / Pickup", "port"],
  ["Warehouse / Delivery", "warehouse"],
  ["Address", "address"],
  ["Chassis", "chassis"],
];

// Phase 10C: free-form multi-field save, wraps application/loads/
// commands.py::update_load_fields unchanged - the Next.js equivalent of
// the Streamlit Order Detail Editor's "Save Order Updates" button.
export function EditForm({ load }: Props) {
  const { hasPermission } = useAuth();
  const updateFields = useUpdateLoadFields(load.id);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(EDITABLE_FIELDS.map(([, key]) => [key, String(load[key] ?? "")])),
  );

  if (!hasPermission("load:edit")) return null;

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const updates: Record<string, string> = {};
    for (const [, key] of EDITABLE_FIELDS) {
      if (values[key] !== String(load[key] ?? "")) updates[key] = values[key];
    }
    if (Object.keys(updates).length === 0) return;
    updateFields.mutate({ updates });
  }

  const hasChanges = EDITABLE_FIELDS.some(([, key]) => values[key] !== String(load[key] ?? ""));

  return (
    <form className="load-workspace__edit-form" onSubmit={handleSubmit} aria-label="Edit load">
      {EDITABLE_FIELDS.map(([label, key]) => (
        <div className="field" key={String(key)}>
          <label htmlFor={`edit-${key}-${load.id}`}>{label}</label>
          <input
            id={`edit-${key}-${load.id}`}
            value={values[key] ?? ""}
            onChange={(event) => setValues((prev) => ({ ...prev, [key]: event.target.value }))}
          />
        </div>
      ))}

      {updateFields.isError && (
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
