-- Calitrans TMS Order Intake / Action Queue migration
-- Run this in Supabase SQL Editor after your current schema and PortPro-style migration.
-- Safe to run more than once.

create table if not exists order_intake (
    id bigserial primary key,
    source text not null default 'manual_upload',
    source_subject text,
    source_sender text,
    source_received_at timestamptz,
    filename text,
    file_path text,
    parsed_data jsonb,
    raw_text text,
    intake_status text not null default 'Needs Review',
    action_required text,
    linked_load_id bigint references loads(id) on delete set null,
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_order_intake_status on order_intake(intake_status);
create index if not exists idx_order_intake_created_at on order_intake(created_at desc);
create index if not exists idx_order_intake_linked_load_id on order_intake(linked_load_id);
