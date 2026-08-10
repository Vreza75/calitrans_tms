-- Adds detailed driver and tractor fields for CaliTrans master data.
-- Safe to run more than once.

alter table drivers add column if not exists dashcam_number text;
alter table drivers add column if not exists connector_type text;
alter table drivers add column if not exists driver_license text;
alter table drivers add column if not exists dob date;
alter table drivers add column if not exists license_plate text;
alter table drivers add column if not exists vin text;
alter table drivers add column if not exists truck_year integer;
alter table drivers add column if not exists truck_make text;
alter table drivers add column if not exists truck_weight numeric(12,2);
alter table drivers add column if not exists truck_value numeric(12,2);
alter table drivers add column if not exists hire_date date;
alter table drivers add column if not exists motive_id text;
alter table drivers add column if not exists motive_password text;
alter table drivers add column if not exists status text not null default 'Active';
alter table drivers add column if not exists notes text;
alter table drivers add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_drivers_driver_name_lower on drivers (lower(driver_name));
create index if not exists idx_drivers_truck_number on drivers (truck_number);
create index if not exists idx_drivers_status on drivers (status);

create or replace function set_driver_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_drivers_updated_at on drivers;
create trigger trg_drivers_updated_at
before update on drivers
for each row
execute function set_driver_updated_at();
