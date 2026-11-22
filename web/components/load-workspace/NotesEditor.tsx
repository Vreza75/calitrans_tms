"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useSaveLoadNote } from "@/lib/api/useLoadWorkspace";
import type { LoadDetail } from "@/lib/api/types";

type Props = {
  load: LoadDetail;
};

// P1 fix: `edit` is null when untouched (displays the live server
// value every render) and a string once the user types - same
// derive-during-render approach as EditForm, avoiding any stale
// point-in-time snapshot of `load`.
export function NotesEditor({ load }: Props) {
  const { hasPermission } = useAuth();
  const saveNote = useSaveLoadNote(load.id);
  const [edit, setEdit] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  if (!hasPermission("load:edit")) return null;

  const note = edit ?? (load.dispatcher_notes || "");

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (edit === null) return;
    setConflict(false);
    saveNote.mutate(
      { note, expected_updated_at: load.updated_at },
      {
        onSuccess: () => setEdit(null),
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) setConflict(true);
        },
      },
    );
  }

  return (
    <form className="load-workspace__notes" onSubmit={handleSubmit} aria-label="Dispatcher notes">
      <div className="field">
        <label htmlFor={`notes-${load.id}`}>Dispatcher Notes</label>
        <textarea id={`notes-${load.id}`} value={note} onChange={(event) => setEdit(event.target.value)} rows={4} />
      </div>

      {conflict && (
        <p role="alert" className="field-error">
          This load was changed by someone else since you opened it. Your unsaved note above is kept - save again
          when ready.
        </p>
      )}
      {saveNote.isError && !conflict && (
        <p role="alert" className="field-error">
          {saveNote.error instanceof ApiError ? saveNote.error.message : "Unable to save this note."}
        </p>
      )}
      {saveNote.isSuccess && <p className="inline-status">Notes saved.</p>}

      <button type="submit" disabled={edit === null || saveNote.isPending}>
        {saveNote.isPending ? "Saving..." : "Save Notes"}
      </button>
    </form>
  );
}
