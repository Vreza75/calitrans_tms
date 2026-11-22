"use client";

import { useRef, useState } from "react";

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
//
// P1 fix: `baseVersionRef` snapshots `load.updated_at` on the
// null -> string transition (editing begins) and is preserved for the
// whole editing session - a background realtime refresh of `load`
// while `edit` is non-null must never swap it out, or a stale-version
// 409 would silently become a pass-through overwrite of someone else's
// save (see EditForm's matching comment). Cleared only on a successful
// save or an explicit discard-and-reload.
export function NotesEditor({ load }: Props) {
  const { hasPermission } = useAuth();
  const saveNote = useSaveLoadNote(load.id);
  const [edit, setEdit] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const baseVersionRef = useRef<string | null>(null);

  if (!hasPermission("load:edit")) return null;

  const note = edit ?? (load.dispatcher_notes || "");

  function handleChange(value: string) {
    if (edit === null) baseVersionRef.current = load.updated_at;
    setEdit(value);
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (edit === null || saveNote.isPending) return;
    setConflict(false);
    saveNote.mutate(
      { note, expected_updated_at: baseVersionRef.current ?? load.updated_at },
      {
        onSuccess: () => {
          setEdit(null);
          baseVersionRef.current = null;
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) setConflict(true);
        },
      },
    );
  }

  function handleDiscardAndReload() {
    setEdit(null);
    setConflict(false);
    baseVersionRef.current = null;
  }

  return (
    <form className="load-workspace__notes" onSubmit={handleSubmit} aria-label="Dispatcher notes">
      <div className="field">
        <label htmlFor={`notes-${load.id}`}>Dispatcher Notes</label>
        <textarea
          id={`notes-${load.id}`}
          value={note}
          onChange={(event) => handleChange(event.target.value)}
          rows={4}
          disabled={saveNote.isPending}
        />
      </div>

      {conflict && (
        <p role="alert" className="field-error">
          This load was changed by someone else since you opened it. Your unsaved note above is kept - save again
          when ready, or{" "}
          <button type="button" onClick={handleDiscardAndReload}>
            discard my note and reload the latest value
          </button>
          .
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
