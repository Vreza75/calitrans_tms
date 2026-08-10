-- database/app_users_migration.sql
--
-- Purpose: back the Streamlit login layer (ui_components/auth_gate.py) with
-- real, per-person accounts. No Supabase Auth project exists in this repo
-- (see api/auth.py's docstring) - this is a plain Postgres table with
-- bcrypt-hashed passwords, matching the role model already canonical in
-- application/auth/models.py (dispatcher, manager, accounting, admin).
--
-- Idempotent: safe to re-run against a database that already has this
-- table (create table/index use if not exists).
--
-- Rollback: drop table if exists app_users;
-- (Destructive - only run if the auth feature itself is being reverted.
-- No other table references app_users via foreign key.)

create table if not exists app_users (
    id serial primary key,
    email text not null,
    display_name text not null,
    password_hash text not null,
    role text not null check (role in ('dispatcher', 'manager', 'accounting', 'admin')),
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists app_users_lower_email_idx on app_users (lower(email));
