import { execFileSync } from "node:child_process";

import { expect, gotoAuthenticated, test } from "./fixtures/auth";
import {
  cleanupCommandDomainEvents,
  cleanupDataset,
  deleteDomainEvent,
  domainEventStatus,
  findCommandDomainEventIds,
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

// Both browser windows below authenticate as the same dispatcher, reusing
// the one worker-scoped token (see web/e2e/fixtures/auth.ts) instead of
// two independent real UI logins - BrowserContexts never share
// sessionStorage with each other, but both can be seeded with the same
// already-issued token.
async function loginAndOpenInbox(page: import("@playwright/test").Page, token: string) {
  await gotoAuthenticated(page, token, "/app/inbox");
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

// "The worker": run the exact script the scheduled GitHub Actions job
// runs, once, synchronously - the only external action in this file.
// Shared by the main test (the real worker-to-UI path) and by the
// polling regression tests below that need to run a genuine concurrent
// claim attempt against their own controlled fixture rows to confirm
// those rows are actually immune to it (see insertDomainEvent's
// available_at doc comment in qa-sample-data.ts).
function runRealtimePublisher(): string {
  const repoRoot = process.cwd().replace(/web$/, "");
  return execFileSync(
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
}

test("worker-to-UI and cross-window realtime: both windows update without a manual refresh", async ({ browser, dispatcherSession }) => {
  const dataset = uniqueDataset("pr22-realtime");
  seedDataset(dataset);
  const datasetMarker = marker(dataset);

  let workItemId: number | undefined;
  let contextA: import("@playwright/test").BrowserContext | undefined;
  let contextB: import("@playwright/test").BrowserContext | undefined;
  try {
    contextA = await browser.newContext();
    contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    await loginAndOpenInbox(pageA, dispatcherSession.token);
    await loginAndOpenInbox(pageB, dispatcherSession.token);

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
    workItemId = await findMarkerWorkItemId(pageA, datasetMarker);
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

    const output = runRealtimePublisher();
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
  } finally {
    // Both context.close() calls moved here (from the end of the try
    // block) so a failed assertion above still closes the browser
    // contexts instead of leaking them.
    await contextA?.close();
    await contextB?.close();

    // close_work_item() publishes a domain_events row that
    // scripts/sample_data.py's cleanup() cannot find - see
    // cleanupCommandDomainEvents's doc comment in qa-sample-data.ts.
    // Previously this finally block only called cleanupDataset(), which
    // never touched this test's own command-generated event, leaving it
    // behind on every run (confirmed by the same pattern already fixed
    // in inbox-acceptance-live.spec.ts/-round3.spec.ts - this was the
    // one remaining call site). Scoped by this exact workItemId, which
    // this run already proved it owns via the marker search above -
    // never a broad event_type/status/timestamp predicate. Runs
    // unconditionally in `finally`, so a failed browser assertion above
    // still cleans up the event before the synthetic dataset is removed.
    if (workItemId !== undefined) {
      cleanupCommandDomainEvents("order_intake_item", String(workItemId));
      expect(findCommandDomainEventIds("order_intake_item", String(workItemId))).toEqual([]);
    }

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
  // Nobody ever claims this row on purpose (no worker invocation runs
  // against it with intent to process it) - simulates a lost/never-
  // processed event. Must fail with a clear timeout, not hang, not
  // silently pass, and not be confused with the terminal-failure case
  // below.
  const aggregateId = uniqueDataset("pr22-poll-pending");
  const eventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", aggregateId, "pending");
  try {
    // Confirm isolation (not just assume it): actually run the real
    // publisher once against the live queue while this fixture is
    // 'pending', the same way a concurrently-running test's own
    // invocation would. If available_at isolation (see
    // insertDomainEvent's doc comment) ever regressed, this row would
    // get claimed here and the assertions below would catch it directly
    // (claimed_at no longer null, status no longer 'pending') rather
    // than failing confusingly later or - worse - passing by accident.
    runRealtimePublisher();
    const afterPublisherRun = domainEventStatus(eventId);
    expect(afterPublisherRun.status).toBe("pending");
    expect(afterPublisherRun.claimedAt).toBeNull();

    let caught: unknown;
    try {
      await waitForDomainEventPublished(eventId, { timeoutMs: 2_000 });
    } catch (error) {
      caught = error;
    }
    expect(caught, "waitForDomainEventPublished must reject a row that never leaves pending, not resolve").toBeInstanceOf(Error);
    // expect.poll()'s own timeout message embeds Jest's character-level
    // diff highlighting as ANSI codes around just the differing part of
    // each quoted value (e.g. "p\x1b[7mending\x1b[27m"), splitting
    // "pending" across escape sequences - strip them before matching so
    // the check is against the real text, not an accident of whether
    // this run happened to colorize it the same way.
    const message = (caught as Error).message.replace(/\x1b\[[0-9;]*m/g, "");
    // The specific expect.poll() timeout diagnostic (see
    // waitForDomainEventPublished's doc comment on invokePollMatcher) -
    // not just "it rejected for some reason" - with the mismatch it
    // timed out on showing this row's own status, still "pending" the
    // whole time.
    expect(message).toMatch(/Timeout \d+ms exceeded while waiting on the predicate/);
    expect(message).toMatch(/Received:\s*"pending"/);
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
    // Same isolation confirmation as the pending-forever test above:
    // this fixture is genuinely 'pending' for the moment between insert
    // and the scheduled transition to 'failed' below - exactly the
    // window a real concurrent claim could otherwise race into - so
    // prove a real publisher run during that window still can't touch
    // it before scheduling the transition.
    runRealtimePublisher();
    const afterPublisherRun = domainEventStatus(eventId);
    expect(afterPublisherRun.status).toBe("pending");
    expect(afterPublisherRun.claimedAt).toBeNull();

    setTimeout(() => updateDomainEventStatus(eventId, "failed"), 500);
    await expect(waitForDomainEventPublished(eventId, { timeoutMs: 10_000 })).rejects.toThrow(/terminal status 'failed'/);
  } finally {
    deleteDomainEvent(eventId);
  }
});

// Regression coverage for cleanupCommandDomainEvents() itself (Codex P2:
// command-generated domain_events were never found by
// scripts/sample_data.py's dataset-scoped cleanup(), since a real
// command's idempotency_key is a content/time-bucketed hash, never the
// fixed "qa-sample:<dataset>:domain_events" key cleanup() matches on -
// see that function's own doc comment in qa-sample-data.ts). Each test
// below inserts two synthetic rows with DIFFERENT aggregate_ids - one
// this run "owns" (the id cleanupCommandDomainEvents() is scoped to),
// one deliberately unrelated - and proves cleanup deletes only the owned
// row, regardless of which of the three real lifecycle states
// (pending/processing, published, failed) it's currently in. No browser,
// login, or real command needed: the thing under test is the cleanup
// predicate itself, exercised directly against the real disposable QA
// Postgres the same way the polling tests above do.

test("command-event cleanup: deletes only the owned pending event, leaves an unrelated pending event untouched", async () => {
  const ownedAggregateId = uniqueDataset("pr23-cleanup-owned-pending");
  const unrelatedAggregateId = uniqueDataset("pr23-cleanup-unrelated-pending");
  const ownedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", ownedAggregateId, "pending");
  const unrelatedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", unrelatedAggregateId, "pending");
  try {
    expect(findCommandDomainEventIds("order_intake_item", ownedAggregateId)).toEqual([ownedEventId]);

    cleanupCommandDomainEvents("order_intake_item", ownedAggregateId);

    expect(findCommandDomainEventIds("order_intake_item", ownedAggregateId)).toEqual([]);
    // The unrelated row must survive untouched - same status, same id,
    // still readable.
    expect(domainEventStatus(unrelatedEventId).status).toBe("pending");
  } finally {
    deleteDomainEvent(unrelatedEventId);
    // Best-effort: already deleted by the cleanup call above if it
    // worked correctly, but this must not throw (or mask a real
    // assertion failure above) if that call somehow left it behind.
    try {
      deleteDomainEvent(ownedEventId);
    } catch {
      // already gone - expected on the success path
    }
  }
});

test("command-event cleanup: deletes only the owned published event, leaves an unrelated published event untouched", async () => {
  const ownedAggregateId = uniqueDataset("pr23-cleanup-owned-published");
  const unrelatedAggregateId = uniqueDataset("pr23-cleanup-unrelated-published");
  const ownedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", ownedAggregateId, "pending");
  const unrelatedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", unrelatedAggregateId, "pending");
  updateDomainEventStatus(ownedEventId, "published");
  updateDomainEventStatus(unrelatedEventId, "published");
  try {
    cleanupCommandDomainEvents("order_intake_item", ownedAggregateId);

    expect(findCommandDomainEventIds("order_intake_item", ownedAggregateId)).toEqual([]);
    expect(domainEventStatus(unrelatedEventId).status).toBe("published");
  } finally {
    deleteDomainEvent(unrelatedEventId);
    try {
      deleteDomainEvent(ownedEventId);
    } catch {
      // already gone - expected on the success path
    }
  }
});

test("command-event cleanup: deletes only the owned failed event, leaves an unrelated failed event untouched", async () => {
  const ownedAggregateId = uniqueDataset("pr23-cleanup-owned-failed");
  const unrelatedAggregateId = uniqueDataset("pr23-cleanup-unrelated-failed");
  const ownedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", ownedAggregateId, "pending");
  const unrelatedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", unrelatedAggregateId, "pending");
  updateDomainEventStatus(ownedEventId, "failed");
  updateDomainEventStatus(unrelatedEventId, "failed");
  try {
    cleanupCommandDomainEvents("order_intake_item", ownedAggregateId);

    expect(findCommandDomainEventIds("order_intake_item", ownedAggregateId)).toEqual([]);
    expect(domainEventStatus(unrelatedEventId).status).toBe("failed");
  } finally {
    deleteDomainEvent(unrelatedEventId);
    try {
      deleteDomainEvent(ownedEventId);
    } catch {
      // already gone - expected on the success path
    }
  }
});

test("command-event cleanup: never touches an unrelated event even when both share the same event_type and status", async () => {
  // The strongest version of "not a broad event_type/status predicate":
  // both rows below are identical in every column except aggregate_id -
  // same event_type, same status - so if cleanupCommandDomainEvents() ever
  // regressed to matching on event_type/status instead of aggregate_id,
  // this is the test that would catch it.
  const ownedAggregateId = uniqueDataset("pr23-cleanup-owned-samekind");
  const unrelatedAggregateId = uniqueDataset("pr23-cleanup-unrelated-samekind");
  const ownedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", ownedAggregateId, "pending");
  const unrelatedEventId = insertDomainEvent("inbox.review_status_changed", "order_intake_item", unrelatedAggregateId, "pending");
  try {
    cleanupCommandDomainEvents("order_intake_item", ownedAggregateId);

    expect(findCommandDomainEventIds("order_intake_item", ownedAggregateId)).toEqual([]);
    expect(findCommandDomainEventIds("order_intake_item", unrelatedAggregateId)).toEqual([unrelatedEventId]);
  } finally {
    deleteDomainEvent(unrelatedEventId);
    try {
      deleteDomainEvent(ownedEventId);
    } catch {
      // already gone - expected on the success path
    }
  }
});
