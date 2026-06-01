-- Calitrans TMS PostgreSQL / Supabase schema
-- Run this in Supabase SQL Editor or any PostgreSQL database.

create table if not exists customers (
    id bigserial primary key,
    company_name text not null unique,
    contact_name text,
    email text,
    phone text,
    created_at timestamptz not null default now()
);

create table if not exists warehouses (
    id bigserial primary key,
    warehouse_name text not null unique,
    address text,
    city text,
    state text,
    zip_code text,
    contact_name text,
    phone text,
    created_at timestamptz not null default now()
);

create table if not exists carriers (
    id bigserial primary key,
    company_name text not null unique,
    contact_name text,
    email text,
    phone text,
    mc_number text,
    created_at timestamptz not null default now()
);

create table if not exists drivers (
    id bigserial primary key,
    carrier_id bigint references carriers(id) on delete set null,
    driver_name text not null,
    phone text,
    email text,
    truck_number text,
    created_at timestamptz not null default now()
);

create table if not exists loads (
    id bigserial primary key,
    type text,
    load_id text,
    booking_number text not null,
    reference_number text,
    container_number text,
    customer text,
    port text,
    warehouse text,
    address text,
    document_cutoff date,
    delivery_need_date date,
    load_date date,
    lfd date,
    status text not null default 'New',
    driver_name text,
    truck_assigned text,
    chassis text,
    size text,
    billing_notes text,
    dispatcher_notes text,
    ready_for_profittools boolean not null default false,
    rate numeric(12,2),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists status_events (
    id bigserial primary key,
    load_id bigint not null references loads(id) on delete cascade,
    old_status text,
    new_status text not null,
    notes text,
    created_by text,
    created_at timestamptz not null default now()
);

create table if not exists documents (
    id bigserial primary key,
    load_id bigint not null references loads(id) on delete cascade,
    document_type text not null default 'load_pdf',
    filename text not null,
    file_path text not null,
    source text,
    created_at timestamptz not null default now()
);

create index if not exists idx_loads_booking_number on loads(booking_number);
create index if not exists idx_loads_status on loads(status);
create index if not exists idx_loads_type on loads(type);
create index if not exists idx_loads_delivery_need_date on loads(delivery_need_date);
create index if not exists idx_status_events_load_id on status_events(load_id);

create or replace function set_load_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_loads_updated_at on loads;
create trigger trg_loads_updated_at
before update on loads
for each row
execute function set_load_updated_at();
