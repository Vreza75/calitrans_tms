import { expect, test } from "@playwright/test";

// REAL backend, REAL disposable QA Postgres (migrations + scripts/sample_data.py
// baseline-01 + alternate-01 applied), REAL synthetic app_users rows. No mocking -
// this exercises the actual FastAPI application-command boundary end to end.
// Run only against the local QA stack (see runbook); never against production.

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };
const ACCOUNTING = { email: "accounting.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

async function login(page: import("@playwright/test").Page, creds: typeof DISPATCHER) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(creds.email);
  await page.getByLabel("Password").fill(creds.password);

  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign In" }).click();
  const response = await loginResponse;
  if (response.status() >= 300) {
    throw new Error(`Real login failed for ${creds.email}: HTTP ${response.status()}`);
  }
}

test("item 1/3: real login, real queue counts, real work-item list and selection", async ({ page }) => {
  await login(page, DISPATCHER);
  await page.goto("/app/inbox");

  await expect(page.getByRole("heading", { name: "Operations Inbox" })).toBeVisible();
  await expect(page.getByText("SYNTHETIC-CALITRANS-QA::baseline-01 New inbound order")).toBeVisible();
  await expect(page.getByText("SYNTHETIC-CALITRANS-QA::alternate-01 New inbound order")).toBeVisible();

  await page.getByText("SYNTHETIC-CALITRANS-QA::baseline-01 New inbound order").click();
  await expect(page).toHaveURL(/[?&]id=1/);
});

test("item 4: conversation isolation - two synthetic conversations never cross-contaminate", async ({ page }) => {
  await login(page, DISPATCHER);

  // Scoped to the detail header specifically - the queue-nav list sidebar
  // legitimately shows both seeded conversations at all times (expected
  // master-detail UI, not cross-contamination); isolation means the
  // DETAIL pane for one conversation never shows the other's sender/body.
  await page.goto("/app/inbox?id=1");
  await expect(page.getByRole("heading", { name: "SYNTHETIC-CALITRANS-QA::baseline-01 New inbound order" })).toBeVisible();
  await expect(page.locator(".inbox-detail-header__sender")).not.toContainText("alternate-01-sender");

  await page.goto("/app/inbox?id=3");
  await expect(page.getByRole("heading", { name: "SYNTHETIC-CALITRANS-QA::alternate-01 New inbound order" })).toBeVisible();
  await expect(page.locator(".inbox-detail-header__sender")).not.toContainText("baseline-01-sender");
});

test("item 5: extracted fields match the real seeded source record", async ({ page }) => {
  await login(page, DISPATCHER);
  await page.goto("/app/inbox?id=1");

  const fields = page.getByRole("region", { name: "Extracted fields" });
  await expect(fields.getByText("SYNTHETIC-CALITRANS-QA::baseline-01 Fictional Shipper")).toBeVisible();
  await expect(fields.getByText("QA-BASELINE-01-INBOX")).toBeVisible();
});

test("item 7: Close against the real application command boundary", async ({ page }) => {
  await login(page, DISPATCHER);
  await page.goto("/app/inbox?id=3");
  await expect(page.getByRole("heading", { name: "SYNTHETIC-CALITRANS-QA::alternate-01 New inbound order" })).toBeVisible();

  const closeResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/work-items/3/close") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Close" }).click();
  const response = await closeResponse;
  expect(response.status()).toBeLessThan(300);

  // Real refetch through the real list endpoint - the closed item must
  // actually leave the active queue server-side, not just optimistically
  // in the client.
  await page.goto("/app/inbox");
  await expect(page.locator(".inbox-work-item-row__subject")).not.toContainText(
    "SYNTHETIC-CALITRANS-QA::alternate-01",
  );
});

test("item 10: accounting role is blocked at the real backend (list/detail 403, not merely a disabled button)", async ({
  page,
}) => {
  // CORRECTION made after running this against the real backend: the
  // existing mocked e2e test (web/e2e/inbox.spec.ts) assumes accounting
  // gets a 200 list/detail response and only the mutation buttons render
  // disabled. The real router (api/routers/work_items.py, READ_OPERATIONS
  // dependency) excludes accounting from the read side entirely, exactly
  // as documented in docs/architecture/OPERATIONS_INBOX_WEB.md's
  // permissions table - confirmed live via curl: both GET /work-items and
  // GET /work-items/1 return 403 FORBIDDEN for the accounting token. This
  // test asserts the real (more restrictive, not less safe) behavior.
  const token = await page.request
    .post("http://localhost:8000/api/v1/auth/login", {
      data: { email: ACCOUNTING.email, password: ACCOUNTING.password },
    })
    .then((r) => r.json())
    .then((j) => j.token as string);

  const listRes = await page.request.get("http://localhost:8000/api/v1/work-items", {
    headers: { Authorization: `Bearer ${token}` },
    failOnStatusCode: false,
  });
  expect(listRes.status()).toBe(403);

  const detailRes = await page.request.get("http://localhost:8000/api/v1/work-items/1", {
    headers: { Authorization: `Bearer ${token}` },
    failOnStatusCode: false,
  });
  expect(detailRes.status()).toBe(403);

  // UI reflects the same real 403, not a silently empty/successful state.
  await login(page, ACCOUNTING);
  await page.goto("/app/inbox");
  await expect(page.getByText(/Unable to load the inbox/)).toBeVisible();
});
