// REAL backend, REAL disposable QA Postgres. Each test seeds its OWN
// run-scoped dataset(s) through web/e2e/fixtures/qa-sample-data.ts (a
// fresh, globally-unique dataset id every run/worker/retry) and discovers
// every work-item id it needs by searching for that dataset's unique
// SYNTHETIC-CALITRANS-QA::<dataset> marker through the real API - never a
// fixed numeric id. This exercises the actual FastAPI
// application-command boundary end to end, with no mocking, and is safe
// under Playwright's fullyParallel execution (web/playwright.config.ts)
// and under repeated runs, since no test shares or mutates another test's
// rows.
//
// Authentication: see web/e2e/fixtures/auth.ts's own comment for the full
// rationale - every test here used to make its own independent real
// /api/v1/auth/login call(s) against one shared account, which tripped
// api/rate_limit.py's real limiter once the whole live suite ran
// concurrently. `dispatcherSession` is a worker-scoped fixture (one real
// login per worker process, from a pool of independent accounts);
// `gotoAuthenticated` reuses its token for every test in that worker
// instead of logging in again.

import { ACCOUNTING, API_BASE, expect, gotoAuthenticated, test } from "./fixtures/auth";
import {
  cleanupCommandDomainEvents,
  cleanupDatasets,
  findCommandDomainEventIds,
  marker,
  seedDataset,
  uniqueDataset,
} from "./fixtures/qa-sample-data";

async function findWorkItemId(page: import("@playwright/test").Page, token: string, dataset: string): Promise<number> {
  const res = await page.request.get(`${API_BASE}/api/v1/work-items?search=${encodeURIComponent(marker(dataset))}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await res.json();
  expect(body.items.length).toBe(1);
  return body.items[0].id as number;
}

test("item 1/3: real login, real queue counts, real work-item list and selection", async ({ page, dispatcherSession }) => {
  // Both datasets share one run-scoped namespace (rather than two fully
  // independent uniqueDataset() calls) specifically so this test can
  // filter the list down to exactly its own two seeded rows. Asserting
  // against the unfiltered first page of /app/inbox was unsafe: any
  // unrelated open work item (another parallel test, leftover QA data, or
  // simply more total open items than fit on one page) could push either
  // seeded row off page 1 under the default sort and fail this test for a
  // reason that has nothing to do with the behavior under test.
  const namespace = uniqueDataset("pr22-live1").slice(0, 36);
  const datasetA = `${namespace}-a`;
  const datasetB = `${namespace}-b`;
  seedDataset(datasetA);
  seedDataset(datasetB);
  try {
    const token = dispatcherSession.token;
    const idA = await findWorkItemId(page, token, datasetA);

    await gotoAuthenticated(page, token, "/app/inbox");
    await expect(page.getByRole("heading", { name: "Operations Inbox" })).toBeVisible();

    // Filter by this run's own unique namespace - a substring of both
    // seeded subjects' markers and nothing else's - instead of relying on
    // an unfiltered page/default sort. No unrelated record can ever match
    // this filter, and pagination is irrelevant since the server-side
    // filter, not page position, is what the assertions below depend on.
    await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(namespace);
    const listResponse = page.waitForResponse(
      (r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="),
    );
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const res = await listResponse;
    const body = await res.json();
    expect(body.items.length).toBe(2); // exactly this run's two seeded rows, scoped by namespace - nothing else can match

    await expect(page.getByText(`${marker(datasetA)} New inbound order`)).toBeVisible();
    await expect(page.getByText(`${marker(datasetB)} New inbound order`)).toBeVisible();

    await page.getByText(`${marker(datasetA)} New inbound order`).click();
    await expect(page).toHaveURL(new RegExp(`[?&]id=${idA}(?:&|$)`));
  } finally {
    cleanupDatasets([datasetA, datasetB]);
  }
});

test("item 4: conversation isolation - two synthetic conversations never cross-contaminate", async ({ page, dispatcherSession }) => {
  const datasetA = uniqueDataset("pr22-live4a");
  const datasetB = uniqueDataset("pr22-live4b");
  seedDataset(datasetA);
  seedDataset(datasetB);
  try {
    const token = dispatcherSession.token;
    const idA = await findWorkItemId(page, token, datasetA);
    const idB = await findWorkItemId(page, token, datasetB);

    // Scoped to the detail header specifically - the queue-nav list sidebar
    // legitimately shows both seeded conversations at all times (expected
    // master-detail UI, not cross-contamination); isolation means the
    // DETAIL pane for one conversation never shows the other's sender/body.
    await gotoAuthenticated(page, token, `/app/inbox?id=${idA}`);
    await expect(page.getByRole("heading", { name: `${marker(datasetA)} New inbound order` })).toBeVisible();
    await expect(page.locator(".inbox-detail-header__sender")).not.toContainText(`${datasetB}-sender`);

    await page.goto(`/app/inbox?id=${idB}`);
    await expect(page.getByRole("heading", { name: `${marker(datasetB)} New inbound order` })).toBeVisible();
    await expect(page.locator(".inbox-detail-header__sender")).not.toContainText(`${datasetA}-sender`);
  } finally {
    cleanupDatasets([datasetA, datasetB]);
  }
});

test("item 5: extracted fields match the real seeded source record", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr22-live5");
  seedDataset(dataset);
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);

    const fields = page.getByRole("region", { name: "Extracted fields" });
    await expect(fields.getByText(`${marker(dataset)} Fictional Shipper`)).toBeVisible();
    await expect(fields.getByText(`QA-${dataset.toUpperCase()}-INBOX`)).toBeVisible();
  } finally {
    cleanupDatasets([dataset]);
  }
});

test("item 7: Close against the real application command boundary", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr22-live7");
  seedDataset(dataset);
  let workItemId: number | undefined;
  try {
    const token = dispatcherSession.token;
    workItemId = await findWorkItemId(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);
    await expect(page.getByRole("heading", { name: `${marker(dataset)} New inbound order` })).toBeVisible();

    const closeResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/close`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const response = await closeResponse;
    expect(response.status()).toBeLessThan(300);

    // Real refetch through the real list endpoint - the closed item must
    // actually leave the active queue server-side, not just optimistically
    // in the client. Scoped by this dataset's own unique marker (same
    // pattern as item 1) rather than asserting on the unfiltered
    // `.inbox-work-item-row__subject` DOM list: that locator's match count
    // depends on every other work item currently open across the whole
    // database, including rows seeded by other tests running concurrently
    // under Playwright's fullyParallel execution. When no other open item
    // happens to exist at that instant (e.g. this test running in
    // isolation, or simply unlucky timing), the locator resolves to zero
    // elements and `.not.toContainText()` times out as a failure instead
    // of passing - a false negative that depends on unrelated parallel
    // test state, not on whether close() actually worked. The server-side
    // search (only_open=True by default - see
    // application/work_items/queries.py) deterministically excludes the
    // now-closed row regardless of what else is open concurrently.
    await page.goto("/app/inbox");
    await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(marker(dataset));
    const searchResponse = page.waitForResponse(
      (r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="),
    );
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const searchRes = await searchResponse;
    const searchBody = await searchRes.json();
    expect(searchBody.items.length).toBe(0);
  } finally {
    // close_work_item() publishes a domain_events row (event_type
    // "inbox.review_status_changed" - see application/work_items/
    // commands.py) that scripts/sample_data.py's cleanup() can never find
    // (its idempotency_key is a content/time-bucketed hash, not the fixed
    // "qa-sample:<dataset>:domain_events" key cleanup() matches on -
    // confirmed live). Scoped by this exact work_item_id, which this run
    // already proved is its own via the marker search above - never a
    // broad event_type/status/timestamp predicate. Before/after proof: a
    // real run-owned event must exist here (the close above just created
    // it) before cleanup, and none after.
    if (workItemId !== undefined) {
      const beforeCleanup = findCommandDomainEventIds("order_intake_item", String(workItemId));
      expect(beforeCleanup.length).toBeGreaterThan(0);
      cleanupCommandDomainEvents("order_intake_item", String(workItemId));
      expect(findCommandDomainEventIds("order_intake_item", String(workItemId))).toEqual([]);
    }

    cleanupDatasets([dataset]);
  }
});

test("item 10: accounting role is blocked at the real backend (list/detail 403, not merely a disabled button)", async ({
  page,
  dispatcherSession,
}) => {
  // CORRECTION made after running this against the real backend: the
  // existing mocked e2e test (web/e2e/inbox.spec.ts) assumes accounting
  // gets a 200 list/detail response and only the mutation buttons render
  // disabled. The real router (api/routers/work_items.py, READ_OPERATIONS
  // dependency) excludes accounting from the read side entirely, exactly
  // as documented in docs/architecture/OPERATIONS_INBOX_WEB.md's
  // permissions table - confirmed live via curl: both GET /work-items and
  // GET /work-items/{id} return 403 FORBIDDEN for the accounting token.
  // This test asserts the real (more restrictive, not less safe) behavior
  // against a real, run-scoped work-item id rather than an assumed one.
  const dataset = uniqueDataset("pr22-live10");
  seedDataset(dataset);
  try {
    const workItemId = await findWorkItemId(page, dispatcherSession.token, dataset);

    // ACCOUNTING is a single dedicated account, not pooled: this is the
    // only test in the whole live suite that uses it, so it never
    // contends with anything, and this is the only real login call this
    // test makes for it (the UI check below reuses this same token via
    // gotoAuthenticated instead of logging in a second time).
    const accountingLoginRes = await page.request.post(`${API_BASE}/api/v1/auth/login`, {
      data: { email: ACCOUNTING.email, password: ACCOUNTING.password },
    });
    const token = (await accountingLoginRes.json()).token as string;

    const listRes = await page.request.get(`${API_BASE}/api/v1/work-items`, {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(listRes.status()).toBe(403);

    const detailRes = await page.request.get(`${API_BASE}/api/v1/work-items/${workItemId}`, {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(detailRes.status()).toBe(403);

    // UI reflects the same real 403, not a silently empty/successful state.
    await gotoAuthenticated(page, token, "/app/inbox");
    await expect(page.getByText(/Unable to load the inbox/)).toBeVisible();
  } finally {
    cleanupDatasets([dataset]);
  }
});
