-- CaliTrans TMS Port Houston / Navis EVP integration support
-- Safe to run more than once.

create table if not exists port_houston_sync_log (
    id bigserial primary key,
    load_id bigint references loads(id) on delete set null,
    action_type text not null,
    lookup_type text,
    request_reference text,
    response_summary jsonb,
    status text not null default 'success',
    error_message text,
    created_by text not null default 'streamlit',
    created_at timestamptz not null default now()
);

create index if not exists idx_port_houston_sync_log_load_id on port_houston_sync_log(load_id);
create index if not exists idx_port_houston_sync_log_created_at on port_houston_sync_log(created_at desc);
create index if not exists idx_port_houston_sync_log_action_type on port_houston_sync_log(action_type);
