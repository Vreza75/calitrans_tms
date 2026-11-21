"use client";

import { useState } from "react";

import { useAuth } from "@/lib/auth/AuthContext";
import { ApiError } from "@/lib/api/client";
import { useSaveLoadNote } from "@/lib/api/useLoadWorkspace";
import type { LoadDetail } from "@/lib/api/types";

type Props = {
  load: LoadDetail;
};

export function NotesEditor({ load }: Props) {
  const { hasPermission } = useAuth();
  const saveNote = useSaveLoadNote(load.id);
  const [note, setNote] = useState(load.dispatcher_notes || "");

  if (!hasPermission("load:edit")) return null;

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    saveNote.mutate({ note });
  }

  return (
    <form className="load-workspace__notes" onSubmit={handleSubmit} aria-label="Dispatcher notes">
      <div className="field">
        <label htmlFor={`notes-${load.id}`}>Dispatcher Notes</label>
        <textarea id={`notes-${load.id}`} value={note} onChange={(event) => setNote(event.target.value)} rows={4} />
      </div>

      {saveNote.isError && (
        <p role="alert" className="field-error">
          {saveNote.error instanceof ApiError ? saveNote.error.message : "Unable to save this note."}
        </p>
      )}
      {saveNote.isSuccess && <p className="inline-status">Notes saved.</p>}

      <button type="submit" disabled={note === (load.dispatcher_notes || "") || saveNote.isPending}>
        {saveNote.isPending ? "Saving..." : "Save Notes"}
      </button>
    </form>
  );
}
