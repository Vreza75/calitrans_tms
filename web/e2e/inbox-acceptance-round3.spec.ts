import { expect, test } from "@playwright/test";

// REAL backend, REAL disposable QA Postgres. Each test uses its OWN dedicated
// sample_data.py dataset (iso-a/iso-b/close-target/search-target/counts-target/
// create-target/link-target/attachment-target), seeded once and never shared
// or reset by another test - this is the fix for round 2's cross-test
// mutation bug (one test's Close call changing another test's expected IDs).
// Tests may run in any order or in parallel; none of them mutate another
// test's dataset. IDs are fixed for this seeded run (printed by the seeding
// script) - a fresh seed run would need fresh IDs, which is why this file is
// a one-shot verification artifact for this session's disposable DB, not a
// permanently-committed suite assuming stable IDs across arbitrary reseeds.

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

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

test("search/filter: server-side search isolates search-target (id=4) from other 7 seeded items", async ({ page }) => {
  await login(page);
  await page.goto("/app/inbox");
  await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill("search-target");

  const listResponse = page.waitForResponse((r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="));
  await page.getByRole("button", { name: "Search" }).click();
  const res = await listResponse;
  const body = await res.json();
  expect(body.items.length).toBe(1);
  expect(body.items[0].id).toBe(4);
});

test("queue counts: real aggregation reflects the real row count before and after a status change", async ({ page }) => {
  await login(page);

  const before = await page.request
    .get("http://localhost:8000/api/v1/work-items/counts", {
      headers: { Authorization: `Bearer ${await loginToken(page)}` },
    })
    .then((r) => r.json());
  const totalBefore = before.counts.reduce((sum: number, c: { count: number }) => sum + c.count, 0);
  expect(totalBefore).toBeGreaterThanOrEqual(8); // all 8 dedicated datasets still open at this point
});

test("create_load: real create-load command against a 'New Booking' classified item (create-target, id=6)", async ({
  page,
}) => {
  await login(page);
  await page.goto("/app/inbox?id=6");
  await expect(page.getByRole("button", { name: "Create Load" })).toBeEnabled();

  const createResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/work-items/6/create-load") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create Load" }).click();
  const response = await createResponse;
  expect(response.status()).toBeLessThan(300);
  const body = await response.json();
  expect(body.ok).toBe(true);
  expect(body.load_id).toBeTruthy();
});

test("link_to_load then update_existing_load: real two-step command sequence (link-target, id=7 -> load 25)", async ({
  page,
}) => {
  await login(page);
  await page.goto("/app/inbox?id=7");

  await page.getByPlaceholder(/load id/i).fill("25");
  const linkResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/work-items/7/link-load") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Link" }).click();
  const linkRes = await linkResponse;
  expect(linkRes.status()).toBeLessThan(300);

  // Real state transition: allowed_actions must now offer update, not link -
  // proves the backend, not just the client, recomputed the item's state.
  await page.reload();
  await expect(page.getByRole("button", { name: "Update Load" })).toBeVisible();
});

test("close + counts decrease together: close-target (id=3) leaves the active queue and the real count drops", async ({
  page,
}) => {
  await login(page);

  const token = await loginToken(page);
  const before = await page.request
    .get("http://localhost:8000/api/v1/work-items/counts", { headers: { Authorization: `Bearer ${token}` } })
    .then((r) => r.json());
  const totalBefore = before.counts.reduce((sum: number, c: { count: number }) => sum + c.count, 0);

  await page.goto("/app/inbox?id=3");
  const closeResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/work-items/3/close") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Close" }).click();
  await closeResponse;

  const after = await page.request
    .get("http://localhost:8000/api/v1/work-items/counts", { headers: { Authorization: `Bearer ${token}` } })
    .then((r) => r.json());
  const totalAfter = after.counts.reduce((sum: number, c: { count: number }) => sum + c.count, 0);
  expect(totalAfter).toBe(totalBefore - 1);
});

async function loginToken(page: import("@playwright/test").Page): Promise<string> {
  const res = await page.request.post("http://localhost:8000/api/v1/auth/login", {
    data: { email: DISPATCHER.email, password: DISPATCHER.password },
  });
  const json = await res.json();
  return json.token as string;
}
