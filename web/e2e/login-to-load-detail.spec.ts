import { expect, test, type Page } from "@playwright/test";

// Phase 10 STEP 39: login -> loads list -> load detail, entirely against
// mocked FastAPI responses (no live backend, no live Supabase). Realtime
// stays unconfigured in this env (no NEXT_PUBLIC_SUPABASE_* vars), so the
// ConnectionIndicator renders "disconnected" - this also incidentally
// covers STEP 34 (app remains usable with realtime unavailable).

const ME_RESPONSE = {
  email: "dispatcher@calitrans.test",
  display_name: "Test Dispatcher",
  role: "dispatcher",
  permissions: ["load:view", "load:edit"],
};

const LOAD_LIST_ITEM = {
  id: 124,
  type: "import",
  booking_number: "BKG-124",
  reference_number: "REF-124",
  container_number: "CONT-124",
  customer: "Acme Imports",
  port: "Port of LA",
  warehouse: "Main Warehouse",
  status: "in_transit",
  driver_name: "Jane Driver",
  truck_assigned: "Truck 12",
  delivery_need_date: null,
  document_cutoff: null,
  invoice_status: "not_invoiced",
  driver_pay_status: "unpaid",
  updated_at: "2026-08-15T12:00:00Z",
};

const LOAD_DETAIL = {
  id: 124,
  type: "import",
  load_id: "124",
  booking_number: "BKG-124",
  reference_number: "REF-124",
  container_number: "CONT-124",
  customer: "Acme Imports",
  port: "Port of LA",
  warehouse: "Main Warehouse",
  status: "in_transit",
  driver_name: "Jane Driver",
  truck_assigned: "Truck 12",
  address: "123 Harbor Blvd",
  delivery_need_date: null,
  document_cutoff: null,
  updated_at: "2026-08-15T12:00:00Z",
};

async function mockBackend(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: "dispatcher@calitrans.test", role: "dispatcher" } }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: ME_RESPONSE }));
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: { items: [LOAD_LIST_ITEM], page: 1, page_size: 25, total_items: 1, total_pages: 1, sort_by: "", sort_direction: "asc" },
    }),
  );
  await page.route("**/api/v1/loads/124/detail", (route) => route.fulfill({ json: LOAD_DETAIL }));
}

test("login, view loads list, open load detail", async ({ page }) => {
  await mockBackend(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await expect(page).toHaveURL(/\/app\/loads$/);
  await expect(page.getByRole("heading", { name: "Loads" })).toBeVisible();
  await expect(page.getByRole("link", { name: "BKG-124" })).toBeVisible();

  await page.getByRole("link", { name: "BKG-124" }).click();

  await expect(page).toHaveURL(/\/app\/loads\/124$/);
  await expect(page.getByRole("heading", { name: "Load BKG-124" })).toBeVisible();
  await expect(page.getByText("Acme Imports")).toBeVisible();
});

test("invalid credentials show an inline error and stay on login", async ({ page }) => {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ status: 401, json: { error: { code: "invalid_credentials", message: "Invalid email or password." } } }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await expect(page.getByText("Invalid email or password.")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
