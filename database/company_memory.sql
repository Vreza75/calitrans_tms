create table if not exists company_memory (
    id bigserial primary key,
    memory_type text not null,
    memory_key text not null,
    memory_value jsonb not null default '{}'::jsonb,
    customer text,
    sender_domain text,
    source text,
    confidence numeric not null default 0.75,
    is_active boolean not null default true,
    usage_count integer not null default 0,
    last_used_at timestamptz,
    created_by text not null default 'system',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(memory_type, memory_key)
);

create index if not exists idx_company_memory_type on company_memory(memory_type);
create index if not exists idx_company_memory_key on company_memory(memory_key);
create index if not exists idx_company_memory_customer on company_memory(customer);
create index if not exists idx_company_memory_sender_domain on company_memory(sender_domain);
create index if not exists idx_company_memory_active on company_memory(is_active);
