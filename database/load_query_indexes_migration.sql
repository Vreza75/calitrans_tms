-- Calitrans TMS: Phase 8 read-model index support
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- repositories/load_query_repo.py's new paginated/filtered/sorted/
-- searched load collection (GET /api/v1/loads/search) needs these:
--
-- idx_loads_updated_at: updated_at is the default (and most common)
-- sort column for this endpoint - unindexed until now, meaning every
-- paginated request would have sorted the full table.
--
-- idx_loads_container_number / idx_loads_reference_number /
-- idx_loads_driver_name: search-by-identifier and driver-name-filter
-- both use these columns; booking_number and status already had indexes
-- (database/schema.sql) but these three didn't.
--
-- idx_documents_load_id: repositories/document_repo.py::
-- list_documents_for_load (GET /api/v1/loads/{id}/documents) filters by
-- load_id - documents.status is indexed already, load_id was not.
--
-- ILIKE search (services/load_query_repo.py::build_load_filters'
-- `search`/customer/driver_name/port/warehouse filters) does not benefit
-- from a plain btree index the way exact/prefix matches do - a
-- trigram (pg_trgm) index would, but that requires an extension not
-- currently enabled in this database and is a bigger infrastructure
-- decision than this phase's scope; not added here, documented as a
-- follow-up if ILIKE search performance becomes a real problem at
-- higher data volume than this business's current scale.
create index if not exists idx_loads_updated_at on loads(updated_at);
create index if not exists idx_loads_container_number on loads(container_number);
create index if not exists idx_loads_reference_number on loads(reference_number);
create index if not exists idx_loads_driver_name on loads(driver_name);
create index if not exists idx_documents_load_id on documents(load_id);
