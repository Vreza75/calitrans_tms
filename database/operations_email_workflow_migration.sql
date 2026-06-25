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
alter table order_intake add column if not exists email_direction text not null default 'inbound';
alter table order_intake add column if not exists email_mailbox text;
alter table order_intake add column if not exists email_in_reply_to text;
alter table order_intake add column if not exists email_references jsonb not null default '[]'::jsonb;
alter table order_intake add column if not exists email_thread_id text;
alter table order_intake add column if not exists email_normalized_subject text;
alter table order_intake add column if not exists conversation_status text not null default 'New Conversation';
alter table order_intake add column if not exists email_synced_at timestamptz;
alter table order_intake add column if not exists case_id bigint;

create table if not exists operations_cases (
    id bigserial primary key,
    case_number text unique not null,
    conversation_key text,
    status text not null default 'New',
    owner text not null default 'Unassigned',
    priority text not null default 'Normal',
    customer text,
    source_subject text,
    request_type text,
    linked_load_id bigint references loads(id) on delete set null,
    next_action text,
    last_message_direction text,
    last_message_at timestamptz,
    message_count integer not null default 0,
    first_response_due_at timestamptz,
    first_response_at timestamptz,
    resolution_due_at timestamptz,
    resolved_at timestamptz,
    customer_wait_started_at timestamptz,
    department_wait_started_at timestamptz,
    sla_status text not null default 'On Track',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    closed_at timestamptz,
    reopened_at timestamptz
);

create table if not exists operations_case_notes (
    id bigserial primary key,
    case_id bigint references operations_cases(id) on delete cascade,
    note_body text not null,
    note_type text not null default 'internal',
    created_by text not null default 'dispatcher',
    created_at timestamptz not null default now()
);

create table if not exists operations_case_owner_history (
    id bigserial primary key,
    case_id bigint references operations_cases(id) on delete cascade,
    old_owner text,
    new_owner text not null,
    changed_by text not null default 'dispatcher',
    changed_at timestamptz not null default now()
);

create table if not exists operations_case_events (
    id bigserial primary key,
    case_id bigint references operations_cases(id) on delete cascade,
    event_type text not null,
    title text,
    details text,
    actor text not null default 'system',
    department text,
    created_at timestamptz not null default now()
);

alter table operations_cases add column if not exists first_response_due_at timestamptz;
alter table operations_cases add column if not exists first_response_at timestamptz;
alter table operations_cases add column if not exists resolution_due_at timestamptz;
alter table operations_cases add column if not exists resolved_at timestamptz;
alter table operations_cases add column if not exists customer_wait_started_at timestamptz;
alter table operations_cases add column if not exists department_wait_started_at timestamptz;
alter table operations_cases add column if not exists sla_status text not null default 'On Track';

update operations_cases
set first_response_due_at = coalesce(first_response_due_at, created_at + interval '2 hours'),
    resolution_due_at = coalesce(resolution_due_at, created_at + interval '48 hours');

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'order_intake_case_id_fkey'
    ) then
        alter table order_intake
            add constraint order_intake_case_id_fkey
            foreign key (case_id) references operations_cases(id) on delete set null;
    end if;
end $$;

create table if not exists load_communications (
    id bigserial primary key,
    load_id bigint references loads(id) on delete cascade,
    intake_id bigint references order_intake(id) on delete set null,
    case_id bigint references operations_cases(id) on delete set null,
    conversation_key text,
    communication_type text,
    direction text not null default 'inbound',
    subject text,
    sender text,
    message_body text,
    created_at timestamptz not null default now()
);

alter table load_communications add column if not exists case_id bigint references operations_cases(id) on delete set null;

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
    case_id bigint references operations_cases(id) on delete set null,
    recipient text not null,
    subject text not null,
    body text not null,
    status text not null default 'sent',
    error_message text,
    sent_at timestamptz,
    sent_by text,
    created_at timestamptz not null default now()
);

alter table operations_email_replies add column if not exists case_id bigint references operations_cases(id) on delete set null;

create table if not exists operations_ai_feedback (
    id bigserial primary key,
    intake_id bigint references order_intake(id) on delete cascade,
    load_id bigint references loads(id) on delete set null,
    source_subject text,
    source_sender text,
    ai_request_type text,
    final_request_type text,
    ai_confidence_score integer,
    ai_priority text,
    ai_action_required text,
    final_action_required text,
    ai_reply_body text,
    final_reply_body text,
    correction_type text not null,
    feedback_notes text,
    created_by text not null default 'dispatcher',
    created_at timestamptz not null default now()
);

create table if not exists operations_inbox_preferences (
    preference_name text primary key,
    preference_value jsonb not null,
    updated_at timestamptz not null default now()
);

create index if not exists idx_order_intake_review_status on order_intake(review_status);
create index if not exists idx_order_intake_request_type on order_intake(request_type);
create index if not exists idx_order_intake_conversation_key on order_intake(conversation_key);
create index if not exists idx_order_intake_matched_load_id on order_intake(matched_load_id);
create index if not exists idx_order_intake_review_created_at on order_intake(review_status, created_at desc);
create index if not exists idx_order_intake_source_created_at on order_intake(source, created_at desc);
create index if not exists idx_order_intake_source_received_at on order_intake(source_received_at desc);
create index if not exists idx_order_intake_email_direction on order_intake(email_direction);
create index if not exists idx_order_intake_email_mailbox on order_intake(email_mailbox);
create index if not exists idx_order_intake_email_thread_id on order_intake(email_thread_id);
create index if not exists idx_order_intake_email_normalized_subject on order_intake(email_normalized_subject);
create index if not exists idx_order_intake_conversation_status on order_intake(conversation_status);
create index if not exists idx_order_intake_email_synced_at on order_intake(email_synced_at desc);
create index if not exists idx_order_intake_case_id on order_intake(case_id);
create unique index if not exists idx_order_intake_source_message_id_unique
    on order_intake(source_message_id)
    where source_message_id is not null;

create index if not exists idx_operations_cases_conversation_key on operations_cases(conversation_key);
create index if not exists idx_operations_cases_status on operations_cases(status);
create index if not exists idx_operations_cases_owner on operations_cases(owner);
create index if not exists idx_operations_cases_linked_load_id on operations_cases(linked_load_id);
create index if not exists idx_operations_cases_updated_at on operations_cases(updated_at desc);
create index if not exists idx_operations_cases_sla_status on operations_cases(sla_status);
create index if not exists idx_operations_cases_first_response_due_at on operations_cases(first_response_due_at);
create index if not exists idx_operations_cases_resolution_due_at on operations_cases(resolution_due_at);
create index if not exists idx_operations_case_notes_case_id on operations_case_notes(case_id);
create index if not exists idx_operations_case_owner_history_case_id on operations_case_owner_history(case_id);
create index if not exists idx_operations_case_events_case_id on operations_case_events(case_id);
create index if not exists idx_operations_case_events_created_at on operations_case_events(created_at);
create index if not exists idx_load_communications_load_id on load_communications(load_id);
create index if not exists idx_load_communications_intake_id on load_communications(intake_id);
create index if not exists idx_load_communications_case_id on load_communications(case_id);
create index if not exists idx_load_communications_conversation_key on load_communications(conversation_key);
create index if not exists idx_quote_requests_status on quote_requests(quote_status);
create index if not exists idx_dispatch_messages_load_id on dispatch_messages(load_id);
create index if not exists idx_email_notifications_load_id on email_notifications(load_id);
create index if not exists idx_operations_email_replies_intake_id on operations_email_replies(intake_id);
create index if not exists idx_operations_email_replies_case_id on operations_email_replies(case_id);
create index if not exists idx_operations_ai_feedback_intake_id on operations_ai_feedback(intake_id);
create index if not exists idx_operations_ai_feedback_created_at on operations_ai_feedback(created_at);
