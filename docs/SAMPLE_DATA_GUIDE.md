# Safe sample-data guide

`scripts/sample_data.py` creates deterministic records only after a target passes strict safety checks. It never falls back to the application's `DATABASE_URL`.

## What it currently covers

- Fictional customer, warehouse, carrier, and three driver profiles.
- Four loads using supported statuses: New, Assigned, Delivered, Cancelled.
- One inbound synthetic intake record and one linked open task when migrated.
- One inert synthetic row in each available worker/outbox/domain-event table. Event types are deliberately unregistered `qa.sample.*` records and are inserted in terminal states so they cannot trigger a real provider.
- Reserved `.invalid` email addresses and North American fictional 555 numbers only.

Vehicles/trailers are not normalized tables in the current schema; tractor identifiers remain load/driver text fields. Synthetic active login users, real provider messages, and actual documents are intentionally not seeded yet because they require password ownership, provider sandboxing, and validated storage/upload controls.

## Required safety setup

Use a disposable database whose name contains `qa`, `test`, `dev`, `sandbox`, `scratch`, or `disposable`. Generic names such as `postgres` are always rejected.

```powershell
$env:CALITRANS_ALLOW_QA_DATA = "YES_I_UNDERSTAND_QA_DATA_ONLY"
$env:CALITRANS_QA_DATABASE_IDENTITY = "127.0.0.1:5433/calitrans_qa"
$qaDatabaseUrl = "postgresql+psycopg2://qa_user:REDACTED@127.0.0.1:5433/calitrans_qa"
```

The identity contains no username or password. The command prints it before doing anything. For a remote QA database, use its exact lower-case host, optional port, and database name. Never approve a production host or a generic `postgres` database.

## Commands

Always begin with dry-run:

```powershell
python scripts/sample_data.py seed --database-url $qaDatabaseUrl --environment test --dataset baseline-01 --dry-run
```

Seed or safely repeat the same dataset:

```powershell
python scripts/sample_data.py seed --database-url $qaDatabaseUrl --environment test --dataset baseline-01
```

List dataset-scoped counts or validate that records exist:

```powershell
python scripts/sample_data.py list --database-url $qaDatabaseUrl --environment test --dataset baseline-01
python scripts/sample_data.py validate --database-url $qaDatabaseUrl --environment test --dataset baseline-01
```

Cleanup only the named dataset:

```powershell
python scripts/sample_data.py cleanup --database-url $qaDatabaseUrl --environment test --dataset baseline-01 --dry-run
python scripts/sample_data.py cleanup --database-url $qaDatabaseUrl --environment test --dataset baseline-01
```

Reset is cleanup followed by seed in the same named dataset:

```powershell
python scripts/sample_data.py reset --database-url $qaDatabaseUrl --environment test --dataset baseline-01
```

## Identity and cleanup contract

Business records use `SYNTHETIC-CALITRANS-QA::<dataset>` in a uniquely identifying text field. Async records use exact keys `qa-sample:<dataset>:<table>`. Cleanup deletes only those markers, in dependency order. It does not use broad date ranges, truncate tables, reset sequences, or delete unknown rows.

Before accepting cleanup on a disposable database:

1. Seed `baseline-01` twice and confirm the second summary reports reused records.
2. Insert or identify one unrelated control row.
3. Seed `alternate-01`.
4. Cleanup `baseline-01`.
5. Confirm the unrelated row and every `alternate-01` count remain unchanged.

These database-backed checks were not run during the 2026-08-18 review because no disposable database was positively established.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Requested operation or dry-run succeeded (for `validate`, only when the dataset is complete - see below) |
| 1 | Connection/schema/operation failure; external error details are not printed |
| 2 | Invalid dataset identifier |
| 3 | Safety refusal before database access |
| 4 | `validate` only: dataset absent - every required table exists but every count for this dataset is zero (never seeded, or cleaned up) |
| 5 | `validate` only: dataset partially present - at least one table's count doesn't match seed()'s exact expected shape |
| 6 | `validate` only: required tables missing from the schema entirely - migrations were never applied, or applied against the wrong database |

`validate` never treats "the count queries executed" as success by itself. It compares actual counts against `seed()`'s exact deterministic shape (customers=1, warehouses=1, carriers=1, drivers=3, loads=4, order_intake=1, and 1 each for any present optional table) and only exits `0` when every table matches exactly. `cleanup` followed by `validate` is expected to exit `4`, not `0` - cleanup does not drop tables, only rows. `reset` followed by `validate` is expected to exit `0`, since reset re-seeds the exact same shape.

## Schema expectations

Apply the repository migrations to a disposable database first. `seed` requires `customers`, `warehouses`, `carriers`, `drivers`, `loads`, and `order_intake`; optional tasks and queue/event tables are used when present. The script checks the live database name after connecting and before writes.

## Adding scenarios

- Reuse a real application command or repository when it cleanly supports deterministic identifiers.
- If direct SQL is necessary for a failure state, keep it in this script, label the row synthetic, and add an exact cleanup predicate.
- Never register a QA outbox event type that can reach Twilio, email, Motive, Port Houston, or another provider.
- Add both production-refusal and dataset-scoped cleanup tests for every new table.
- Do not commit generated customer documents or large binaries; represent oversized cases with in-memory test streams.
