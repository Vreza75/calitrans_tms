-- Calitrans TMS: document lifecycle states (Phase 6B)
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Phase 6B fixes attach_load_document's non-atomicity: the pre-existing
-- flow wrote the uploaded file to disk BEFORE inserting the documents
-- row, so a crash between the two left an orphaned file with no DB
-- record. The new flow stages the upload, inserts a documents row with
-- status='pending' and a checksum in the SAME transaction as an outbox
-- event (event_type='document.file.finalize'), and a worker
-- (services/outbox_processor.py, same infrastructure as Phase 6's SMS
-- conversion) atomically renames the staged file to its final path and
-- flips status to 'available' only once that succeeds - see
-- docs/architecture/DOCUMENT_LIFECYCLE.md.
--
-- status default 'available': every row inserted before this migration
-- (via the old, still-present-for-dead-code-only direct write path -
-- see pages_app/documents.py::render_pdf_intake, confirmed dead code)
-- already has its file on disk with no pending/failed concept - treating
-- pre-existing rows as 'available' is correct, not a guess.
alter table documents add column if not exists status text not null default 'available';
alter table documents add column if not exists checksum text;

create index if not exists idx_documents_status on documents(status);
