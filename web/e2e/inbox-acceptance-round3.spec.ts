import { expect, test } from "@playwright/test";

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

import { cleanupDataset, marker, seedDataset, setRequestType, uniqueDataset } from "./fixtures/qa-sample-data";

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };
const API_BASE = "http://localhost:8000";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DISPATCHER.email);
  await page.getByLabel("Password").fill(DISPATCHER.password);
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign In" }).click();
  const response = await loginResponse;
  if (response.status() >= 300) throw new Error(`Real login failed: HTTP ${response.status()}`);
}

async function loginToken(page: import("@playwright/test").Page): Promise<string> {
  const res = await page.request.post(`${API_BASE}/api/v1/auth/login`, {
    data: { email: DISPATCHER.email, password: DISPATCHER.password },
  });
  const json = await res.json();
  return json.token as string;
}

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

test("search/filter: server-side search isolates this run's own item from everything else", async ({ page }) => {
  const dataset = uniqueDataset("pr22-search");
  seedDataset(dataset);
  try {
    await login(page);
    await page.goto("/app/inbox");
    await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(marker(dataset));

    const listResponse = page.waitForResponse((r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="));
    await page.getByRole("button", { name: "Search" }).click();
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
}) => {
  const dataset = uniqueDataset("pr22-counts");
  seedDataset(dataset);
  try {
    const token = await loginToken(page);
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

test("create_load: real create-load command against a 'New Booking' classified item", async ({ page }) => {
  const dataset = uniqueDataset("pr22-create");
  seedDataset(dataset);
  setRequestType(dataset, "New Booking");
  try {
    const token = await loginToken(page);
    const workItemId = await findWorkItemId(page, token, dataset);

    await login(page);
    await page.goto(`/app/inbox?id=${workItemId}`);
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
  } finally {
    cleanupDataset(dataset);
  }
});

test("link_to_load then update_existing_load: real two-step command sequence, verified by the persisted load row", async ({
  page,
}) => {
  const dataset = uniqueDataset("pr22-link");
  seedDataset(dataset);
  try {
    const token = await loginToken(page);
    const workItemId = await findWorkItemId(page, token, dataset);
    const newLoadBooking = `QA-${dataset.toUpperCase()}-NEW`;
    const loadId = await findLoadIdByBooking(page, token, newLoadBooking);

    await login(page);
    await page.goto(`/app/inbox?id=${workItemId}`);

    await page.getByPlaceholder(/load id/i).first().fill(String(loadId));
    const linkResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/link-load`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Link" }).click();
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
    cleanupDataset(dataset);
  }
});

test("close command: succeeds against the real command boundary, then and only then does the queue count reflect it", async ({
  page,
}) => {
  const dataset = uniqueDataset("pr22-close");
  seedDataset(dataset);
  try {
    const token = await loginToken(page);
    const workItemId = await findWorkItemId(page, token, dataset);
    const before = await scopedOpenCount(page, token, dataset);

    await login(page);
    await page.goto(`/app/inbox?id=${workItemId}`);

    const closeResponse = page.waitForResponse(
      (r) => r.url().includes(`/api/v1/work-items/${workItemId}/close`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Close" }).click();
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
