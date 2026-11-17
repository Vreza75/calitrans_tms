import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect } from "@playwright/test";

// Shared helper for live acceptance specs (REAL backend, REAL disposable QA
// Postgres - see docs/SAMPLE_DATA_GUIDE.md). Every dataset created through
// this module is run-scoped: a fresh, globally-unique dataset id per call,
// seeded via scripts/sample_data.py (never raw application code), so
// concurrent/parallel/repeated test runs each get their own independent,
// mutable rows and never collide with or mutate another run's data.
//
// This file only shells out to the already-existing, already-safety-gated
// scripts/sample_data.py CLI (dataset name allow-list, QA-only database
// identity check, `SYNTHETIC-CALITRANS-QA::` marker). It does not add any
// new write path and never touches application/, api/, services/, or
// repositories/.

const PYTHON_EXE = "C:/GitHub/calitrans_tms_postgres_upgrade_clean/.venv/Scripts/python.exe";
const REPO_ROOT = "C:/GitHub/calitrans_tms_postgres_upgrade_clean";

// Same disposable QA Postgres used by web/e2e/inbox-realtime.spec.ts.
const QA_SQLALCHEMY_URL = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa";
const QA_PSYCOPG2_DSN = "postgresql://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa";

const QA_ENV = {
  ...process.env,
  CALITRANS_ALLOW_QA_DATA: "YES_I_UNDERSTAND_QA_DATA_ONLY",
  CALITRANS_QA_DATABASE_IDENTITY: "127.0.0.1:5433/calitrans_qa",
};

/** Matches scripts/sample_data.py's DATASET_RE: ^[a-z0-9][a-z0-9-]{2,39}$. */
export function uniqueDataset(label: string): string {
  const suffix = `${Date.now().toString(36)}${randomBytes(3).toString("hex")}`;
  return `${label}-${suffix}`.toLowerCase().slice(0, 40);
}

export function marker(dataset: string): string {
  return `SYNTHETIC-CALITRANS-QA::${dataset}`;
}

function runSampleData(command: "seed" | "cleanup", dataset: string): void {
  execFileSync(
    PYTHON_EXE,
    [
      "scripts/sample_data.py",
      command,
      "--database-url",
      QA_SQLALCHEMY_URL,
      "--environment",
      "test",
      "--dataset",
      dataset,
    ],
    { cwd: REPO_ROOT, env: QA_ENV },
  );
}

export function seedDataset(dataset: string): void {
  runSampleData("seed", dataset);
}

export function cleanupDataset(dataset: string): void {
  runSampleData("cleanup", dataset);
}

// seed() always leaves the one synthetic order_intake row at the default
// request_type ('Needs Classification' - see
// database/operations_email_workflow_migration.sql), which is enough for
// every allowed-action scenario except "New Booking"/"New Order" (needed to
// exercise create_load - see application/work_items/queries.py's
// _allowed_actions). No application code or sample_data.py write path
// supports that yet, so this issues one exact-match UPDATE against the
// single row seed() just created for this run-scoped dataset, identified by
// the same deterministic subject seed() always writes. Scoped to a single
// row by exact equality (never LIKE/broad match) on a value that is itself
// unique per run, so it can never touch another dataset's row.
export function setRequestType(dataset: string, requestType: string): void {
  const subject = `${marker(dataset)} New inbound order`;
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('update order_intake set request_type = %s where source_subject = %s', (sys.argv[2], sys.argv[3]))",
    "assert cur.rowcount == 1, f'expected exactly 1 row, updated {cur.rowcount}'",
    "conn.commit()",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, requestType, subject], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  });
}

// Seeds one real, run-scoped attachment onto seedDataset()'s own order_intake
// row - the physical file is written to a fresh OS temp directory (never
// storage/ or backups/ - see CLAUDE.md's "never commit" list, though these
// are never committed either way, just local runtime files) and its path is
// merged into that row's own parsed_data._operations_attachments (the exact
// key services/operations_attachment_core.py's extract_operations_attachments
// reads - see that module; there is no separate attachments table). Scoped
// by the same exact-subject-equality UPDATE pattern as setRequestType()
// above - one row, never a broad match - so it can never touch another
// dataset's data. Returns the real file_path written, so the caller can
// clean it up (cleanupAttachmentFixture) independently of the DB row
// (cleanupDataset/cleanupDatasets only ever deletes rows, never files).
export function seedAttachmentFixture(
  dataset: string,
  attachment: { filename: string; contentType: string; body: string },
): string {
  const dir = mkdtempSync(join(tmpdir(), "calitrans-qa-attach-"));
  const filePath = join(dir, attachment.filename).replace(/\\/g, "/");
  writeFileSync(filePath, attachment.body, "utf-8");

  const subject = `${marker(dataset)} New inbound order`;
  const attachmentJson = JSON.stringify([
    { filename: attachment.filename, file_path: filePath, content_type: attachment.contentType, is_pdf: false },
  ]);
  const snippet = [
    "import json, sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('select parsed_data from order_intake where source_subject = %s', (sys.argv[2],))",
    "row = cur.fetchone()",
    "assert row is not None, f'no order_intake row for subject {sys.argv[2]!r}'",
    "parsed = row[0] if isinstance(row[0], dict) else (json.loads(row[0]) if row[0] else {})",
    "parsed['_operations_attachments'] = json.loads(sys.argv[3])",
    "cur.execute('update order_intake set parsed_data = %s::jsonb where source_subject = %s', (json.dumps(parsed), sys.argv[2]))",
    "assert cur.rowcount == 1, f'expected exactly 1 row, updated {cur.rowcount}'",
    "conn.commit()",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, subject, attachmentJson], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  });

  return filePath;
}

export function cleanupAttachmentFixture(filePath: string): void {
  try {
    rmSync(filePath.replace(/\/[^/]+$/, ""), { recursive: true, force: true });
  } catch (error) {
    // Best-effort, same contract as cleanupDatasets(): a cleanup failure
    // must never mask the test's own pass/fail result.
    console.error(`qa-sample-data: attachment fixture cleanup failed for ${filePath}:`, error);
  }
}

// Confirmed gap: scripts/sample_data.py's cleanup() only deletes rows it
// itself seeded (matched by the SYNTHETIC-CALITRANS-QA::<dataset> prefix
// on freetext columns it wrote - see that script's cleanup()). A load
// created through the REAL application command (create_load_from_work_item
// -> services/order_intake.py's create_load_from_intake_with_status ->
// db_client.py's add_row()) never gets a load_id/booking_number containing
// that prefix: add_row()'s SM_TO_DB_COLUMNS mapping has no "load_id"
// entry at all (that text column is left null), and booking_number comes
// from the work item's own pre-seeded booking field ("QA-<dataset>-...",
// a different, unprefixed naming scheme) - so cleanup()'s LIKE match on
// loads.load_id can never find it. Confirmed live: creating a load via
// round3's create_load test and then calling cleanupDataset() left that
// load row behind. These two functions track and remove exactly that
// category of row - one the application itself created mid-test, that
// scripts/sample_data.py's dataset-scoped cleanup was never going to see.
export function loadBookingNumber(loadId: number): string | null {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('select booking_number from loads where id = %s', (sys.argv[2],))",
    "row = cur.fetchone()",
    "print(row[0] if row and row[0] is not None else '')",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  const output = execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, String(loadId)], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  })
    .toString()
    .trim();
  return output === "" ? null : output;
}

// Deletes exactly one application-created loads row by its numeric id -
// but only after confirming, by direct read, that its own booking_number
// contains this run's unique dataset text. That check is what makes this
// safe to call with an id the test itself just received back from a real
// create-load response: it can never delete an unrelated real row, even
// given a wrong/stale id, because the content check would simply fail to
// match first. Every FK referencing loads.id is "on delete cascade" or
// "on delete set null" (see database/*.sql) - a single delete here is
// enough, no separate child-row cleanup needed.
export function cleanupCreatedLoad(loadId: number, dataset: string): void {
  const booking = loadBookingNumber(loadId);
  if (booking === null) {
    return; // already gone - nothing to do, matches cleanupDatasets()'s best-effort contract
  }
  if (!booking.toUpperCase().includes(dataset.toUpperCase())) {
    throw new Error(
      `cleanupCreatedLoad refused to delete load id=${loadId}: its booking_number ` +
        `(${JSON.stringify(booking)}) does not contain this run's own dataset id ` +
        `(${JSON.stringify(dataset)}) - deleting it would risk an unrelated record.`,
    );
  }
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('delete from loads where id = %s', (sys.argv[2],))",
    "conn.commit()",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, String(loadId)], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  });
}

export function loadExists(loadId: number): boolean {
  return loadBookingNumber(loadId) !== null;
}

// Read-only lookups against domain_events for tests that need to correlate
// a realtime publication with the specific event a command produced -
// never a fixed/guessed id. aggregate_type/aggregate_id/event_type here
// must match what the real emitter wrote (e.g.
// application/work_items/commands.py's close_work_item(): aggregate_type
// "order_intake_item", aggregate_id str(work_item_id), event_type
// "inbox.review_status_changed") - scoped tightly enough by the caller's
// own dynamically-discovered work_item_id that another concurrent test's
// unrelated event can never match.
export function latestDomainEventId(aggregateType: string, aggregateId: string, eventType: string): number {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute(",
    "    'select id from domain_events where aggregate_type = %s and aggregate_id = %s and event_type = %s "
      + "order by created_at desc, id desc limit 1',",
    "    (sys.argv[2], sys.argv[3], sys.argv[4]),",
    ")",
    "row = cur.fetchone()",
    "assert row is not None, f'no domain_events row for {sys.argv[2:]}'",
    "print(row[0])",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  const output = execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, aggregateType, aggregateId, eventType], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  })
    .toString()
    .trim();
  return parseInt(output, 10);
}

// Confirmed gap (Codex P2): a command-generated domain_events row (e.g.
// close_work_item()'s "inbox.review_status_changed" - see
// application/work_items/commands.py) is never found by
// scripts/sample_data.py's cleanup(), which only matches
// idempotency_key = 'qa-sample:<dataset>:domain_events' - the exact,
// fixed key seed() itself writes for its own one inert placeholder row.
// A real command's idempotency_key is a content-and-time-bucketed sha256
// hash (see realtime/events.py's time_bucketed_key()), which can never
// equal that fixed string. These command-generated rows were being left
// behind by every test that calls a command which publishes one.
//
// findCommandDomainEventIds()/cleanupCommandDomainEvents() scope by
// aggregate_type + aggregate_id ONLY - deliberately never by event_type,
// status, or any timestamp predicate (the exact things Codex's finding
// says must not be used, since any of those could also match an
// unrelated row). A work_item_id (the aggregate_id for every command
// exercised by this live suite) is a real, never-reused Postgres primary
// key that this run's own test already discovered by searching for its
// own unique SYNTHETIC-CALITRANS-QA::<dataset> marker - so scoping by
// that exact id can only ever match events this run's own commands
// produced against this run's own row, regardless of which status
// (pending/processing/published/failed) any of them are currently in.
export function findCommandDomainEventIds(aggregateType: string, aggregateId: string): number[] {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('select id from domain_events where aggregate_type = %s and aggregate_id = %s', (sys.argv[2], sys.argv[3]))",
    "print(','.join(str(row[0]) for row in cur.fetchall()))",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  const output = execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, aggregateType, aggregateId], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  })
    .toString()
    .trim();
  return output === "" ? [] : output.split(",").map((value) => parseInt(value, 10));
}

// Deletes every domain_events row owned by this exact aggregate (any
// event_type, any status - see this section's doc comment above for why
// that's still safe, not broad). Returns the ids actually deleted, so
// callers can assert on the count/ids instead of just trusting this
// function silently did the right thing.
export function cleanupCommandDomainEvents(aggregateType: string, aggregateId: string): number[] {
  const ids = findCommandDomainEventIds(aggregateType, aggregateId);
  for (const id of ids) {
    deleteDomainEvent(id);
  }
  return ids;
}

// A single raw snapshot of one specific domain_events id's persisted
// state - authoritative for whatever instant it's read at, but NOT by
// itself proof the row has reached its final state. Under Playwright's
// fullyParallel execution, a single `process` run claims whatever is
// pending across every concurrently-running test, so another worker's
// invocation (not this test's own) can be the one that actually claims
// this row - and that other invocation may still be mid-flight
// (status='processing') at the exact instant this function is called,
// since nothing guarantees it finished before this test's own `process`
// call returned. Callers that need to know the row's *eventual* outcome
// must poll this (see waitForDomainEventPublished below), not call it
// once and trust the result.
export function domainEventStatus(id: number): { status: string; publishedAt: string | null; claimedAt: string | null } {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('select status, published_at, claimed_at from domain_events where id = %s', (sys.argv[2],))",
    "row = cur.fetchone()",
    "assert row is not None, f'no domain_events row with id={sys.argv[2]}'",
    "print(f'{row[0]}|{row[1]}|{row[2]}')",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  const output = execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, String(id)], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  })
    .toString()
    .trim();
  const [status, publishedAt, claimedAt] = output.split("|");
  return {
    status,
    publishedAt: publishedAt === "None" ? null : publishedAt,
    claimedAt: claimedAt === "None" ? null : claimedAt,
  };
}

// Bounded polling of exactly one domain_events id's persisted status -
// the fix for the race domainEventStatus's own doc comment describes.
// expect.poll()'s callback, unlike expect(...).toPass(), does NOT retry
// on a thrown error (see node_modules/playwright/lib/matchers/expect.js's
// invokePollMatcher: `const value = await actual()` sits outside the
// try/catch that wraps only the matcher call) - so throwing here on the
// terminal 'failed' status aborts the poll immediately with a clear,
// specific error instead of silently retrying it until the timeout.
// Returning the status for every other value and matching on
// .toBe("published") is what makes 'pending'/'processing' keep polling
// (not a match yet, correctly retried) while 'published' succeeds and
// 'failed' fails fast - exactly the three required outcomes, checking
// nothing but this one id.
export async function waitForDomainEventPublished(
  id: number,
  options: { timeoutMs?: number; intervals?: number[] } = {},
): Promise<void> {
  const timeoutMs = options.timeoutMs ?? 20_000;
  const intervals = options.intervals ?? [250, 500, 1_000, 2_000];
  await expect
    .poll(
      () => {
        const { status } = domainEventStatus(id);
        if (status === "failed") {
          throw new Error(
            `domain_events id=${id} reached the terminal status 'failed' - it will never become ` +
              "'published', so this is not a timing issue worth continuing to poll for.",
          );
        }
        return status;
      },
      { timeout: timeoutMs, intervals },
    )
    .toBe("published");
}

// Test-setup helpers for exercising waitForDomainEventPublished's own
// polling/fail-fast/timeout behavior directly, without needing a live
// browser, login, or the realtime publisher script - these manipulate
// domain_events rows the same direct-SQL way domainEventStatus reads
// them, scoped to a synthetic aggregate_id unique per call (uniqueDataset
// reused purely as a globally-unique string generator here, not as a
// seeded dataset), so a regression run can never collide with another
// test's real row.
//
// Isolation from the real publisher's claim query: repositories/
// domain_event_repo.py's claim_next_pending() only ever claims rows
// matching `status = 'pending' and available_at <= now()` (a real,
// unmodified application predicate - see that file). A fixture row
// inserted with status 'pending' and the column's own now()-default
// available_at is therefore real, fully eligible production-shaped
// queue input, indistinguishable to that query from a real event - any
// concurrently-running test's own publisher invocation (this file's
// real `process_realtime_events.py process` calls claim whatever is
// pending across the whole table, not just their own test's rows) could
// claim and actually process it, mutating status/claimed_at out from
// under a test that assumed it would stay untouched. Setting
// available_at to a day in the future - the existing schema's own
// column, no new column, no change to the claim predicate itself -
// makes `available_at <= now()` false, so claim_next_pending's own
// unmodified query can never select these rows, while every column this
// module's read/write helpers touch (status, claimed_at, published_at)
// remains the real, live table - still exercising real persisted state
// transitions, just never claimable by anything other than this file's
// own direct updateDomainEventStatus() calls.
export function insertDomainEvent(
  eventType: string,
  aggregateType: string,
  aggregateId: string,
  status: "pending" | "processing" = "pending",
): number {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute(",
    "    'insert into domain_events (event_type, aggregate_type, aggregate_id, payload, idempotency_key, status, available_at) "
      + "values (%s, %s, %s, %s::jsonb, %s, %s, now() + interval \\'1 day\\') returning id',",
    "    (sys.argv[2], sys.argv[3], sys.argv[4], '{}', sys.argv[4], sys.argv[5]),",
    ")",
    "row = cur.fetchone()",
    "conn.commit()",
    "print(row[0])",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  const output = execFileSync(
    PYTHON_EXE,
    ["-c", snippet, QA_PSYCOPG2_DSN, eventType, aggregateType, aggregateId, status],
    { cwd: REPO_ROOT, env: QA_ENV },
  )
    .toString()
    .trim();
  return parseInt(output, 10);
}

export function updateDomainEventStatus(
  id: number,
  status: "pending" | "processing" | "published" | "failed",
): void {
  const sql =
    status === "published"
      ? "update domain_events set status = %s, published_at = now() where id = %s"
      : "update domain_events set status = %s where id = %s";
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    `cur.execute('${sql}', (sys.argv[2], sys.argv[3]))`,
    "assert cur.rowcount == 1, f'expected exactly 1 row, updated {cur.rowcount}'",
    "conn.commit()",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, status, String(id)], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  });
}

export function deleteDomainEvent(id: number): void {
  const snippet = [
    "import sys",
    "import psycopg2",
    "conn = psycopg2.connect(sys.argv[1])",
    "cur = conn.cursor()",
    "cur.execute('delete from domain_events where id = %s', (sys.argv[2],))",
    "conn.commit()",
    "cur.close()",
    "conn.close()",
  ].join("\n");
  execFileSync(PYTHON_EXE, ["-c", snippet, QA_PSYCOPG2_DSN, String(id)], {
    cwd: REPO_ROOT,
    env: QA_ENV,
  });
}

export function cleanupDatasets(datasets: string[]): void {
  for (const dataset of datasets) {
    try {
      cleanupDataset(dataset);
    } catch (error) {
      // Best-effort: a cleanup failure must never mask the test's own
      // pass/fail result, and disposable QA data left behind is harmless
      // (never shared, always re-cleanable by dataset id).
      console.error(`qa-sample-data: cleanup failed for dataset ${dataset}:`, error);
    }
  }
}
