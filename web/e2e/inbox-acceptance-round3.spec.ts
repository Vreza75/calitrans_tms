// REAL backend, REAL disposable QA Postgres. Each test seeds its OWN
// run-scoped dataset through web/e2e/fixtures/qa-sample-data.ts - a fresh,
// globally-unique dataset id every run, every worker, every retry - and
// discovers every work-item/load id it needs by searching for that
// dataset's unique SYNTHETIC-CALITRANS-QA::<dataset> marker through the
// real API, never by assuming a fixed numeric id. This replaces round 2/3's
// fixed, one-shot-seeded ids (which broke on every reseed and could not run
// in parallel with each other): every test here is independent and
// disposable, so repeated runs and Playwright's fullyParallel execution
// (web/playwright.config.ts) are both safe - no test can read or mutate
// another test's rows, and a count aggregation is always scoped to this
// test's own marker via the `search` filter so concurrent tests never shift
// each other's before/after numbers.
//
// Authentication: see web/e2e/fixtures/auth.ts's own comment - the
// worker-scoped `dispatcherSession` fixture replaces this file's old
// per-test login()/loginToken() pair (every dual-need test used to make
// two real /api/v1/auth/login calls against one shared account, which
// tripped api/rate_limit.py's real limiter once the live suite ran
// concurrently).

import { API_BASE, expect, gotoAuthenticated, test } from "./fixtures/auth";
import {
  cleanupCreatedLoad,
  cleanupDataset,
  loadExists,
  marker,
  seedDataset,
  setRequestType,
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

async function findLoadIdByBooking(
  page: import("@playwright/test").Page,
  token: string,
  booking: string,
): Promise<number> {
  const res = await page.request.get(`${API_BASE}/api/v1/loads/search?search=${encodeURIComponent(booking)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await res.json();
  expect(body.items.length).toBe(1);
  return body.items[0].id as number;
}

async function scopedOpenCount(
  page: import("@playwright/test").Page,
  token: string,
  dataset: string,
): Promise<number> {
  const res = await page.request.get(
    `${API_BASE}/api/v1/work-items/counts?search=${encodeURIComponent(marker(dataset))}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const body = await res.json();
  return (body.counts as { queue: string; count: number }[]).reduce((sum, c) => sum + c.count, 0);
}

test("search/filter: server-side search isolates this run's own item from everything else", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr22-search");
  seedDataset(dataset);
  try {
    await gotoAuthenticated(page, dispatcherSession.token, "/app/inbox");
    await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(marker(dataset));

    const listResponse = page.waitForResponse((r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="));
    // exact: true - this test's own dataset is named "pr22-search-*", which
    // substring-matches the "Search" button's own accessible name via the
    // sidebar work-item-row button (confirmed live: strict-mode violation).
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const res = await listResponse;
    const body = await res.json();
    expect(body.items.length).toBe(1);
    expect(body.items[0].source_subject).toContain(marker(dataset));
  } finally {
    cleanupDataset(dataset);
  }
});

test("queue counts: a real status transition changes the real aggregate, scoped to this run's own item", async ({
  page,
  dispatcherSession,
}) => {
  const dataset = uniqueDataset("pr22-counts");
  seedDataset(dataset);
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);

    const before = await scopedOpenCount(page, token, dataset);
    expect(before).toBe(1); // this run's own item, open, nothing else matches its unique marker

    const closeRes = await page.request.post(`${API_BASE}/api/v1/work-items/${workItemId}/close`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { reason: "" },
    });
    expect(closeRes.status()).toBeLessThan(300); // the real transition must succeed before counts are trusted

    const after = await scopedOpenCount(page, token, dataset);
    expect(after).toBe(before - 1);
  } finally {
    cleanupDataset(dataset);
  }
});

test("create_load: real create-load command against a 'New Booking' classified item", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr22-create");
  seedDataset(dataset);
  setRequestType(dataset, "New Booking");
  let loadId: number | undefined;
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);
    await expect(page.getByRole("button", { name: "Create Load" })).toBeEnabled();

    const createResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/create-load`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Create Load" }).click();
    const response = await createResponse;
    expect(response.status()).toBeLessThan(300);
    const body = await response.json();
    expect(body.ok).toBe(true);
    expect(body.load_id).toBeTruthy();
    loadId = body.load_id as number;

    // Independent persistence verification - the response body alone
    // (status + echoed load_id) does not prove anything landed in the
    // database. Two separate real reads, neither trusting the create
    // response's own claims:
    //   1. The load itself exists, fetched fresh by id.
    //   2. The SOURCE work item's own matched_load_id (persisted by
    //      services/operations_inbox_service.py's review_status_sql in
    //      the same transaction as the load insert) points back at
    //      exactly this load - proving the link, not just the load row,
    //      was actually committed.
    const loadRes = await page.request.get(`${API_BASE}/api/v1/loads/${loadId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(loadRes.status()).toBe(200);
    const loadBody = await loadRes.json();
    expect(loadBody.id).toBe(loadId);

    const workItemRes = await page.request.get(`${API_BASE}/api/v1/work-items/${workItemId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const workItemBody = await workItemRes.json();
    expect(workItemBody.matched_load_id).toBe(loadId);
  } finally {
    // Cleanup verification, before and after: scripts/sample_data.py's
    // dataset-scoped cleanup() never finds this row (see
    // cleanupCreatedLoad's own comment in qa-sample-data.ts for why) - it
    // is a real row the application itself created mid-test, tracked and
    // removed explicitly here instead of being silently left behind.
    if (loadId !== undefined) {
      expect(loadExists(loadId), "sanity: the created load must still exist right before its own cleanup").toBe(true);
      cleanupCreatedLoad(loadId, dataset);
      expect(loadExists(loadId), "the application-created load must be gone after cleanup, not merely the seeded dataset rows").toBe(false);
    }
    cleanupDataset(dataset);
  }
});

test("link_to_load then update_existing_load: real two-step command sequence, verified by the persisted load row", async ({
  page,
  dispatcherSession,
}) => {
  const dataset = uniqueDataset("pr22-link");
  seedDataset(dataset);
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);
    const newLoadBooking = `QA-${dataset.toUpperCase()}-NEW`;
    const loadId = await findLoadIdByBooking(page, token, newLoadBooking);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);

    await page.getByPlaceholder(/load id/i).first().fill(String(loadId));
    const linkResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/link-load`) && r.request().method() === "POST",
    );
    // exact: true - this test's own dataset is literally named "pr22-link-*"
    // and the sidebar's work-item-row button's accessible name includes the
    // full seeded subject (containing that dataset id), so a substring
    // match against "Link" resolves to two elements (the real Link button
    // and this row) and throws a strict-mode violation - confirmed live.
    await page.getByRole("button", { name: "Link", exact: true }).click();
    const linkRes = await linkResponse;
    expect(linkRes.status()).toBeLessThan(300);

    // Real state transition: allowed_actions must now offer update, not
    // link - proves the backend, not just the client, recomputed the
    // item's state from the persisted matched_load_id.
    await page.reload();
    await expect(page.getByRole("button", { name: "Update Load" })).toBeVisible();

    // "Fill blank fields only" is checked by default and every field this
    // item would map onto the load is already non-blank (seed() fills every
    // load column) - leaving it checked would make update_existing_load a
    // true no-op and prove nothing was persisted. Uncheck it so the command
    // actually overwrites, then verify the overwrite landed in the database
    // through a second, independent read - not just a 2xx response.
    await page.getByRole("checkbox", { name: "Fill blank fields only" }).uncheck();

    const updateResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/update-load`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Update Load" }).click();
    const updateRes = await updateResponse;
    expect(updateRes.status()).toBeLessThan(300);
    const updateBody = await updateRes.json();
    expect(updateBody.updated_fields).toContain("booking_number");

    const loadRes = await page.request.get(`${API_BASE}/api/v1/loads/${loadId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const loadBody = await loadRes.json();
    expect(loadBody.booking_number).toBe(`QA-${dataset.toUpperCase()}-INBOX`);
  } finally {
    // loadId here is one of seed()'s own 4 pre-seeded loads (load_id text
    // column already prefixed with this dataset's marker - see
    // scripts/sample_data.py's seed()), so cleanupDataset() alone already
    // covers it; no cleanupCreatedLoad() needed in this test.
    cleanupDataset(dataset);
  }
});

test("close command: succeeds against the real command boundary, then and only then does the queue count reflect it", async ({
  page,
  dispatcherSession,
}) => {
  const dataset = uniqueDataset("pr22-close");
  seedDataset(dataset);
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);
    const before = await scopedOpenCount(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);

    const closeResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/close`) && r.request().method() === "POST",
    );
    // exact: true - same substring-collision risk as the Link button above:
    // this test's own dataset is named "pr22-close-*".
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const response = await closeResponse;

    // The command must be verified successful before its side effect
    // (the count drop) is ever asserted - a failed close that still
    // happened to coincide with a count change would otherwise pass.
    expect(response.status()).toBeLessThan(300);

    const after = await scopedOpenCount(page, token, dataset);
    expect(after).toBe(before - 1);
  } finally {
    cleanupDataset(dataset);
  }
});
