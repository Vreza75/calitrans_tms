-- Calitrans TMS PortPro-style workflow migration
-- Run this AFTER your original database/schema.sql.
-- Safe to run more than once.

alter table loads add column if not exists steamship_line text;
alter table loads add column if not exists vessel_name text;
alter table loads add column if not exists terminal text;
alter table loads add column if not exists pickup_appointment timestamptz;
alter table loads add column if not exists delivery_appointment timestamptz;
alter table loads add column if not exists empty_return_location text;
alter table loads add column if not exists empty_return_date date;
alter table loads add column if not exists chassis_provider text;
alter table loads add column if not exists pickup_reference text;
alter table loads add column if not exists delivery_reference text;
alter table loads add column if not exists invoice_status text not null default 'Not Ready';
alter table loads add column if not exists driver_pay_status text not null default 'Pending';
alter table loads add column if not exists customer_rate numeric(12,2);
alter table loads add column if not exists carrier_pay numeric(12,2);
alter table loads add column if not exists accessorials numeric(12,2) not null default 0;
alter table loads add column if not exists margin numeric(12,2);

create table if not exists appointments (
    id bigserial primary key,
    load_id bigint not null references loads(id) on delete cascade,
    appointment_type text not null,
    appointment_time timestamptz,
    location_name text,
    address text,
    confirmation_number text,
    notes text,
    created_at timestamptz not null default now()
);

create table if not exists tasks (
    id bigserial primary key,
    load_id bigint references loads(id) on delete cascade,
    task_type text not null,
    description text not null,
    assigned_to text,
    due_date date,
    status text not null default 'Open',
    created_at timestamptz not null default now()
);

create index if not exists idx_loads_invoice_status on loads(invoice_status);
create index if not exists idx_loads_driver_pay_status on loads(driver_pay_status);
create index if not exists idx_appointments_load_id on appointments(load_id);
create index if not exists idx_tasks_status on tasks(status);
