-- Calitrans TMS Multi-Container Booking migration
-- Run this in Supabase SQL Editor after database/schema.sql and
-- database/operations_email_workflow_migration.sql.
-- Safe to run more than once.
--
-- Adds the pending-order-draft table and child-container tracking columns
-- that pages_app/operations_inbox.py already reads/writes but that were
-- never migrated (order_intake_drafts did not exist in any prior migration).
--
-- Container quantity is intentionally nullable everywhere: an unknown
-- quantity must surface as "Needs Review", never silently default to 1.

create table if not exists order_intake_drafts (
    id bigserial primary key,
    conversation_key text not null,
    draft_status text not null default 'Awaiting Document Parse',
    request_stage text,
    customer text,
    booking_number text,
    container_number text,
    reference_number text,
    container_qty integer,
    containers_created integer not null default 0,
    load_group_key text,
    service_flow text,
    origin_port text,
    destination_warehouse text,
    delivery_address text,
    delivery_need_date text,
    lfd text,
    container_size text,
    pickup_appointment text,
    delivery_appointment text,
    warehouse_contact text,
    warehouse_phone text,
    terminal text,
    steamship_line text,
    vessel_name text,
    exporting_carrier text,
    opens_for_receiving text,
    document_cutoff text,
    vgm_cutoff text,
    cargo_cutoff text,
    sailing_date text,
    eta_date text,
    rate_per_container text,
    rate_total text,
    dispatcher_notes text,
    commodity text,
    empty_pickup_location text,
    full_return_terminal text,
    port_of_loading text,
    port_of_discharge text,
    place_of_delivery text,
    missing_fields jsonb not null default '[]'::jsonb,
    parsed_data jsonb,
    created_from_intake_id bigint references order_intake(id) on delete set null,
    last_intake_id bigint references order_intake(id) on delete set null,
    created_load_id bigint references loads(id) on delete set null,
    latest_direction text,
    latest_subject text,
    latest_sender text,
    last_customer_message_at timestamptz,
    last_dispatch_message_at timestamptz,
    dispatcher_confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint order_intake_drafts_container_qty_range
        check (container_qty is null or (container_qty between 1 and 10)),
    constraint order_intake_drafts_containers_created_range
        check (containers_created between 0 and 10)
);

create unique index if not exists idx_order_intake_drafts_conversation_key
    on order_intake_drafts(conversation_key);
create index if not exists idx_order_intake_drafts_booking_number on order_intake_drafts(booking_number);
create index if not exists idx_order_intake_drafts_container_number on order_intake_drafts(container_number);
create index if not exists idx_order_intake_drafts_draft_status on order_intake_drafts(draft_status);
create index if not exists idx_order_intake_drafts_updated_at on order_intake_drafts(updated_at desc);
create index if not exists idx_order_intake_drafts_created_load_id on order_intake_drafts(created_load_id);

drop trigger if exists trg_order_intake_drafts_updated_at on order_intake_drafts;
create trigger trg_order_intake_drafts_updated_at
before update on order_intake_drafts
for each row
execute function set_load_updated_at();

alter table loads add column if not exists parent_booking_key text;
alter table loads add column if not exists container_sequence integer;
alter table loads add column if not exists container_total integer;
alter table loads add column if not exists is_placeholder_container boolean not null default false;
alter table loads add column if not exists commodity text;
alter table loads add column if not exists empty_pickup_location text;
alter table loads add column if not exists full_return_terminal text;
alter table loads add column if not exists port_of_loading text;
alter table loads add column if not exists port_of_discharge text;
alter table loads add column if not exists vessel_name text;
alter table loads add column if not exists steamship_line text;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'loads_container_sequence_range'
    ) then
        alter table loads
            add constraint loads_container_sequence_range
            check (container_sequence is null or (container_sequence between 1 and 10));
    end if;

    if not exists (
        select 1 from pg_constraint where conname = 'loads_container_total_range'
    ) then
        alter table loads
            add constraint loads_container_total_range
            check (container_total is null or (container_total between 1 and 10));
    end if;
end $$;

-- Idempotency guarantee for child-load creation: a second click (or a
-- retried request) cannot create a duplicate container_sequence for the
-- same parent booking. Only applies to rows that are actually part of a
-- multi-container split (parent_booking_key is not null).
create unique index if not exists idx_loads_parent_booking_container_sequence
    on loads(parent_booking_key, container_sequence)
    where parent_booking_key is not null;

create index if not exists idx_loads_parent_booking_key on loads(parent_booking_key)
    where parent_booking_key is not null;


-- ============================================================
-- ROLLBACK (run manually if you need to revert this migration)
-- ============================================================
-- drop index if exists idx_loads_parent_booking_container_sequence;
-- drop index if exists idx_loads_parent_booking_key;
-- alter table loads drop constraint if exists loads_container_sequence_range;
-- alter table loads drop constraint if exists loads_container_total_range;
-- alter table loads drop column if exists parent_booking_key;
-- alter table loads drop column if exists container_sequence;
-- alter table loads drop column if exists container_total;
-- alter table loads drop column if exists is_placeholder_container;
-- alter table loads drop column if exists commodity;
-- alter table loads drop column if exists empty_pickup_location;
-- alter table loads drop column if exists full_return_terminal;
-- alter table loads drop column if exists port_of_loading;
-- alter table loads drop column if exists port_of_discharge;
-- alter table loads drop column if exists vessel_name;
-- alter table loads drop column if exists steamship_line;
-- drop trigger if exists trg_order_intake_drafts_updated_at on order_intake_drafts;
-- drop table if exists order_intake_drafts;
