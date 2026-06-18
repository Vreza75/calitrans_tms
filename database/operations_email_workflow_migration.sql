-- Calitrans TMS Operations Inbox email workflow migration
-- Run this after database/schema.sql, database/portpro_style_migration.sql,
-- and database/order_intake_migration.sql.
-- Safe to run more than once.

alter table order_intake add column if not exists review_status text not null default 'Open';
alter table order_intake add column if not exists request_type text not null default 'Needs Classification';
alter table order_intake add column if not exists conversation_key text;
alter table order_intake add column if not exists matched_load_id bigint references loads(id) on delete set null;
alter table order_intake add column if not exists confidence_score integer not null default 0;
alter table order_intake add column if not exists source_message_id text;
alter table order_intake add column if not exists source_received_at timestamptz;

create table if not exists load_communications (
    id bigserial primary key,
    load_id bigint references loads(id) on delete cascade,
    intake_id bigint references order_intake(id) on delete set null,
    conversation_key text,
    communication_type text,
    direction text not null default 'inbound',
    subject text,
    sender text,
    message_body text,
    created_at timestamptz not null default now()
);

create table if not exists quote_requests (
    id bigserial primary key,
    intake_id bigint references order_intake(id) on delete set null,
    customer text,
    origin text,
    destination text,
    container_type text,
    requested_date date,
    notes text,
    quote_status text not null default 'Requested',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists dispatch_messages (
    id bigserial primary key,
    load_id bigint references loads(id) on delete cascade,
    message_type text not null,
    direction text not null default 'internal',
    recipient text,
    message_body text not null,
    sent_by text,
    created_at timestamptz not null default now()
);

create table if not exists email_notifications (
    id bigserial primary key,
    load_id bigint references loads(id) on delete cascade,
    old_status text,
    new_status text,
    sent_to text,
    subject text,
    body text,
    status text not null default 'pending',
    error_message text,
    sent_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists operations_email_replies (
    id bigserial primary key,
    intake_id bigint references order_intake(id) on delete cascade,
    load_id bigint references loads(id) on delete set null,
    recipient text not null,
    subject text not null,
    body text not null,
    status text not null default 'sent',
    error_message text,
    sent_at timestamptz,
    sent_by text,
    created_at timestamptz not null default now()
);

create index if not exists idx_order_intake_review_status on order_intake(review_status);
create index if not exists idx_order_intake_request_type on order_intake(request_type);
create index if not exists idx_order_intake_conversation_key on order_intake(conversation_key);
create index if not exists idx_order_intake_matched_load_id on order_intake(matched_load_id);
create unique index if not exists idx_order_intake_source_message_id_unique
    on order_intake(source_message_id)
    where source_message_id is not null;

create index if not exists idx_load_communications_load_id on load_communications(load_id);
create index if not exists idx_load_communications_intake_id on load_communications(intake_id);
create index if not exists idx_load_communications_conversation_key on load_communications(conversation_key);
create index if not exists idx_quote_requests_status on quote_requests(quote_status);
create index if not exists idx_dispatch_messages_load_id on dispatch_messages(load_id);
create index if not exists idx_email_notifications_load_id on email_notifications(load_id);
create index if not exists idx_operations_email_replies_intake_id on operations_email_replies(intake_id);
