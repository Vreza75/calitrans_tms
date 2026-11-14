import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";

import {
  cleanupDataset,
  deleteDomainEvent,
  insertDomainEvent,
  latestDomainEventId,
  marker,
  seedDataset,
  uniqueDataset,
  updateDomainEventStatus,
  waitForDomainEventPublished,
} from "./fixtures/qa-sample-data";

// REAL local Supabase Realtime stack (remapped ports 64320-64329, avoiding
// the pre-existing unrelated victor-ai-os project's default-port instance -
// never touched), REAL FastAPI (REALTIME_ENABLED=true), REAL disposable
// Postgres. No mocking. Proves items 8 (worker-to-UI) and 9 (cross-window)
// via the actual production chain: application/work_items/commands.py's
// close_work_item() persists a pending domain_events row in the same
// transaction -> scripts/process_realtime_events.py (the exact script the
// scheduled GitHub Actions worker runs) claims and broadcasts it via
// Supabase Realtime -> both browser windows hold a real
// @supabase/supabase-js subscription (web/lib/realtime/client.ts) -> each
// window's React Query cache is invalidated and refetches -> both update
// with zero manual refresh.
//
// Fixture fix: this test used to depend on a fixed, externally pre-seeded
// "realtime-ci-02" dataset (manually seeded once outside this file, same
// one-shot problem PR #22's other acceptance specs had with fixed numeric
// ids). It now seeds its own run-scoped dataset through
// web/e2e/fixtures/qa-sample-data.ts - a fresh, globally-unique dataset id
// every run/worker/retry - so repeated and parallel runs each get their
// own independent row and never collide with or depend on anything seeded
// out of band. The work-item id is still never assumed: it's located by
// searching for this run's own unique SYNTHETIC-CALITRANS-QA::<dataset>
// marker through the real search endpoint, exactly as before.
//
// Correlation fix: both windows now apply that same marker filter before
// the close happens (not just window A), and before anything else both
// must show exactly this run's one work item under the same id. After
// the close, the test captures the specific domain_events row id
// close_work_item() just persisted and checks that exact row's final
// persisted status, instead of accepting any "outcome=published" line
// anywhere in a worker invocation's output - a check an unrelated pending
// event (another parallel test's, or leftover) could otherwise satisfy
// without this test's own event having published at all.

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

async function loginAndOpenInbox(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DISPATCHER.email);
  await page.getByLabel("Password").fill(DISPATCHER.password);
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign In" }).click();
  await loginResponse;
  await page.goto("/app/inbox");
}

async function findMarkerWorkItemId(page: import("@playwright/test").Page, datasetMarker: string): Promise<number> {
  await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(datasetMarker);
  const listResponse = page.waitForResponse((r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="));
  await page.getByRole("button", { name: "Search" }).click();
  const res = await listResponse;
  const body = await res.json();
  expect(body.items.length).toBe(1);
  return body.items[0].id as number;
}

test("worker-to-UI and cross-window realtime: both windows update without a manual refresh", async ({ browser }) => {
  const dataset = uniqueDataset("pr22-realtime");
  seedDataset(dataset);
  const datasetMarker = marker(dataset);

  try {
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    await loginAndOpenInbox(pageA);
    await loginAndOpenInbox(pageB);

    // Real websocket to the real local Supabase stack, not the default/
    // disconnected indicator state.
    await expect(pageA.getByText("Live updates connected")).toBeVisible({ timeout: 15_000 });
    await expect(pageB.getByText("Live updates connected")).toBeVisible({ timeout: 15_000 });

    // Both windows filter by this run's own unique marker before anything
    // else happens, and both must see exactly one - the same - work item.
    // Checking pageB only via an unfiltered page.getByText(...).first()
    // (as before) proves the marker is somewhere on the page, not that
    // this window's own list is scoped to this run's single item the same
    // way pageA's is.
    const workItemId = await findMarkerWorkItemId(pageA, datasetMarker);
    await expect(pageA.getByText(datasetMarker).first()).toBeVisible();

    const workItemIdFromB = await findMarkerWorkItemId(pageB, datasetMarker);
    expect(workItemIdFromB).toBe(workItemId);
    await expect(pageB.getByText(datasetMarker).first()).toBeVisible();

    // Neither window ever navigates away from the list (/app/inbox) from
    // here on - navigating into the detail view would itself trigger a
    // normal (non-realtime) refetch on return, which would prove nothing
    // about the realtime path specifically. The dispatcher action is fired
    // as a direct authenticated request instead of a UI click-through, so
    // window A's own page never leaves the list it's being checked against.
    const token = await pageA.evaluate(() => sessionStorage.getItem("calitrans_web_session_token"));
    const closeRes = await pageA.request.post(`http://localhost:8000/api/v1/work-items/${workItemId}/close`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { reason: "" },
    });
    expect(closeRes.status()).toBeLessThan(300);

    // close_work_item() persists this exact domain_events row - event_type
    // "inbox.review_status_changed", aggregate_type "order_intake_item",
    // aggregate_id = this run's own dynamically-discovered workItemId (see
    // application/work_items/commands.py) - in the same transaction as the
    // close write above, so it already exists here. Capturing its id is
    // what lets the assertion below confirm the publisher processed *this*
    // event specifically, rather than merely that some outcome=published
    // line appeared anywhere in a worker invocation's output - which an
    // unrelated pending event (another parallel test's, or leftover) could
    // satisfy without this test's own event having been published at all.
    const domainEventId = latestDomainEventId("order_intake_item", String(workItemId), "inbox.review_status_changed");

    // "The worker": run the exact script the scheduled GitHub Actions job
    // runs, once, synchronously - the only external action in this test.
    const repoRoot = process.cwd().replace(/web$/, "");
    const output = execFileSync(
      "C:/GitHub/calitrans_tms_postgres_upgrade_clean/.venv/Scripts/python.exe",
      ["scripts/process_realtime_events.py", "process"],
      {
        cwd: repoRoot,
        env: {
          ...process.env,
          DATABASE_URL: "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa",
          REALTIME_ENABLED: "true",
          SUPABASE_URL: "http://127.0.0.1:64321",
          SUPABASE_SERVICE_ROLE_KEY:
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU",
        },
      },
    ).toString();
    console.log("process_realtime_events.py output:", output);

    // Authoritative check against the persisted row itself, not this
    // particular invocation's stdout (see qa-sample-data.ts's
    // domainEventStatus doc comment for why) - but a single immediate
    // check still races another concurrently-running worker that may
    // still be claiming/publishing this exact row at the instant it's
    // checked. Polling the same id until it settles (see
    // waitForDomainEventPublished's doc comment) is what makes this
    // correct under genuine parallel contention, not merely "usually
    // fast enough": success only when this exact event reaches
    // 'published', fast failure if it reaches the terminal 'failed'
    // status, a clear timeout if it's still pending/processing.
    await waitForDomainEventPublished(domainEventId, { timeoutMs: 20_000 });

    // Neither window is refreshed. This run's own dataset has exactly one
    // matching item, so a successful close empties it from both windows'
    // lists - purely via the realtime invalidation path.
    await expect(pageA.getByText(datasetMarker)).toHaveCount(0, { timeout: 15_000 });
    await expect(pageB.getByText(datasetMarker)).toHaveCount(0, { timeout: 15_000 });

    await contextA.close();
    await contextB.close();
  } finally {
    cleanupDataset(dataset);
  }
});

// Regression coverage for waitForDomainEventPublished itself - the fix
// for the race the test above used to have. These exercise the real
// disposable QA Postgres directly (no mocking, same as the rest of this
// file) but need no browser, login, or the realtime publisher script:
// the thing under test is the polling behavior against domain_events
// state transitions, which is cheaper and more precise to drive directly
// than through the full UI/worker flow. Each uses its own
// uniqueDataset()-generated aggregate_id, so none can collide with the
// main test above or with each other under parallel execution.

test("domain event polling: succeeds even when another worker still owns the event", async () => {
  // status: "processing" (claimed_at set, not yet published) is exactly
  // what "another worker owns the event" means in this queue's lifecycle
  // (see database/domain_events_migration.sql's status comment) - this
  // simulates a concurrently-running test's own `process` invocation
  // having claimed the row first, finishing shortly after this test
  // starts polling. A single immediate check would see "processing" and
  // (with the old code) either misreport failure or race; the poll must
  // wait through it and only succeed once it actually reaches published.
  const aggregateId = uniqueDataset("pr22-poll-processing");
  const eventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", aggregateId, "processing");
  try {
    setTimeout(() => updateDomainEventStatus(eventId, "published"), 1_500);
    await waitForDomainEventPublished(eventId, { timeoutMs: 10_000 });
  } finally {
    deleteDomainEvent(eventId);
  }
});

test("domain event polling: times out clearly if the event stays pending forever", async () => {
  // Nobody ever claims this row (no worker invocation runs against it in
  // this test) - simulates a lost/never-processed event. Must fail with
  // a clear timeout, not hang, not silently pass, and not be confused
  // with the terminal-failure case below.
  const aggregateId = uniqueDataset("pr22-poll-pending");
  const eventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", aggregateId, "pending");
  try {
    await expect(waitForDomainEventPublished(eventId, { timeoutMs: 2_000 })).rejects.toThrow();
  } finally {
    deleteDomainEvent(eventId);
  }
});

test("domain event polling: fails fast if the event reaches the terminal failed status", async () => {
  // Simulates a worker claiming the row and exhausting its retries
  // (status -> 'failed', the queue's terminal non-success state). Must
  // fail with a specific "terminal status 'failed'" error - not pass,
  // and not be indistinguishable from the pending-forever timeout above.
  const aggregateId = uniqueDataset("pr22-poll-failed");
  const eventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", aggregateId, "pending");
  try {
    setTimeout(() => updateDomainEventStatus(eventId, "failed"), 500);
    await expect(waitForDomainEventPublished(eventId, { timeoutMs: 10_000 })).rejects.toThrow(/terminal status 'failed'/);
  } finally {
    deleteDomainEvent(eventId);
  }
});
