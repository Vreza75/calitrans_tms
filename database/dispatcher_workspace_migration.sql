-- database/dispatcher_workspace_migration.sql
-- CaliTrans AI Dispatcher Workspace task foundation

create table if not exists operations_tasks (
    id bigserial primary key,
    task_title text not null,
    task_description text,
    task_status text not null default 'Open',
    task_priority text not null default 'Medium',
    owner text not null default 'Dispatch',
    due_at timestamptz,
    completed_at timestamptz,
    completed_by text,
    source_type text,
    intake_id bigint references order_intake(id) on delete set null,
    case_id bigint references operations_cases(id) on delete set null,
    load_id bigint references loads(id) on delete set null,
    customer text,
    booking_number text,
    container_number text,
    created_by text not null default 'system',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists dispatcher_actions (
    id bigserial primary key,
    action_type text not null,
    action_status text not null default 'Recorded',
    action_summary text,
    intake_id bigint references order_intake(id) on delete set null,
    case_id bigint references operations_cases(id) on delete set null,
    load_id bigint references loads(id) on delete set null,
    task_id bigint references operations_tasks(id) on delete set null,
    actor text not null default 'dispatcher',
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists ai_recommendation_decisions (
    id bigserial primary key,
    intake_id bigint references order_intake(id) on delete set null,
    case_id bigint references operations_cases(id) on delete set null,
    load_id bigint references loads(id) on delete set null,
    task_id bigint references operations_tasks(id) on delete set null,
    recommendation_type text not null,
    recommendation_summary text,
    ai_confidence integer,
    decision text not null,
    decision_notes text,
    decided_by text not null default 'dispatcher',
    ai_payload jsonb not null default '{}'::jsonb,
    final_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_operations_tasks_status on operations_tasks(task_status);
create index if not exists idx_operations_tasks_owner on operations_tasks(owner);
create index if not exists idx_operations_tasks_due_at on operations_tasks(due_at);
create index if not exists idx_operations_tasks_case_id on operations_tasks(case_id);
create index if not exists idx_operations_tasks_load_id on operations_tasks(load_id);
create index if not exists idx_operations_tasks_intake_id on operations_tasks(intake_id);

create index if not exists idx_dispatcher_actions_case_id on dispatcher_actions(case_id);
create index if not exists idx_dispatcher_actions_load_id on dispatcher_actions(load_id);
create index if not exists idx_dispatcher_actions_intake_id on dispatcher_actions(intake_id);
create index if not exists idx_dispatcher_actions_created_at on dispatcher_actions(created_at desc);

create index if not exists idx_ai_recommendation_decisions_case_id on ai_recommendation_decisions(case_id);
create index if not exists idx_ai_recommendation_decisions_load_id on ai_recommendation_decisions(load_id);
create index if not exists idx_ai_recommendation_decisions_intake_id on ai_recommendation_decisions(intake_id);
create index if not exists idx_ai_recommendation_decisions_created_at on ai_recommendation_decisions(created_at desc);
