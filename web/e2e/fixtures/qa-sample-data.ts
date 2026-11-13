import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";

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
