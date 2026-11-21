import { expect, test, type Page } from "@playwright/test";

// Phase 10C: Load Workspace acceptance - mirrors dispatch-board.spec.ts's
// mocked-backend pattern (no live FastAPI/Postgres/Supabase, run manually
// like the rest of this directory, not part of the automatic PR-validation
// CI gate).

const DISPATCHER_ME = {
  email: "dispatcher@calitrans.test",
  display_name: "Test Dispatcher",
  role: "dispatcher",
  permissions: ["load:view", "load:edit", "load:verify", "load:cancel", "document:attach", "dispatch:communication_log"],
};

const ACCOUNTING_ME = {
  email: "accounting@calitrans.test",
  display_name: "Test Accounting",
  role: "accounting",
  permissions: ["load:view", "document:attach"],
};

function loadDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    type: "Import",
    load_id: "1",
    booking_number: "BKG-001",
    reference_number: "REF-001",
    container_number: "CONT-001",
    customer: "Acme Imports",
    port: "Port of LA",
    warehouse: "Main Warehouse",
    address: "123 Harbor Blvd",
    document_cutoff: null,
    delivery_need_date: null,
    load_date: null,
    lfd: null,
    status: "Ready to Dispatch",
    driver_name: "",
    truck_assigned: "",
    chassis: "CH-1",
    size: "40",
    billing_notes: "",
    dispatcher_notes: "original note",
    invoice_status: "Not Invoiced",
    driver_pay_status: "Unpaid",
    closeout_stage: "Not Started",
    steamship_line: "MSC",
    vessel_name: "MSC Fantasia",
    terminal: "APM",
    pickup_appointment: null,
    delivery_appointment: null,
    empty_return_location: "",
    empty_return_date: null,
    parent_booking_key: "",
    container_sequence: null,
    container_total: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-15T12:00:00Z",
    ...overrides,
  };
}

function page1<T>(items: T[]) {
  return { items, page: 1, page_size: 50, total_items: items.length, total_pages: 1, sort_by: "", sort_direction: "desc" };
}

async function mockBackend(page: Page, me: Record<string, unknown>, detail: ReturnType<typeof loadDetail>): Promise<void> {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: String(me.email), role: me.role } }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: me }));
  await page.route("**/api/v1/loads/1/detail", (route) => route.fulfill({ json: detail }));
  await page.route("**/api/v1/loads/1/documents", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    return route.continue();
  });
  await page.route("**/api/v1/loads/1/communications**", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: page1([]) });
    return route.continue();
  });
  await page.route("**/api/v1/loads/1/timeline**", (route) =>
    route.fulfill({
      json: page1([{ event_type: "load.created", title: "Load created", details: "", actor: "dispatcher", created_at: "2026-08-01T00:00:00Z" }]),
    }),
  );
}

async function loginAs(page: Page, me: Record<string, unknown>): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(String(me.email));
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();
}

test("the full detail field set renders, including fields the old proof page never showed", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");

  await expect(page.getByText("MSC Fantasia")).toBeVisible();
  await expect(page.getByText("APM")).toBeVisible();
  await expect(page.getByText("Not Started")).toBeVisible();
});

test("editing a field saves and is reflected without a page reload", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  let saved = false;
  await page.route("**/api/v1/loads/1", (route) => {
    if (route.request().method() === "PUT") {
      saved = true;
      return route.fulfill({ json: { ok: true, load_id: 1, status: "Ready to Dispatch", reason: "" } });
    }
    return route.continue();
  });
  await page.route("**/api/v1/loads/1/detail", (route) =>
    route.fulfill({ json: loadDetail(saved ? { customer: "New Customer Name" } : {}) }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByLabel("Customer").fill("New Customer Name");
  await page.getByRole("button", { name: "Save Changes" }).click();

  await expect(page.getByText("Saved.")).toBeVisible();
  await expect(page.locator(".detail-grid")).toContainText("New Customer Name");
});

test("Mark Missing Info succeeds and is reflected on the detail grid", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  let marked = false;
  await page.route("**/api/v1/loads/1/mark-missing-info", (route) => {
    marked = true;
    return route.fulfill({ json: { ok: true, load_id: 1, status: "Hold/Need Info", reason: "" } });
  });
  await page.route("**/api/v1/loads/1/detail", (route) =>
    route.fulfill({ json: loadDetail(marked ? { status: "Hold/Need Info" } : {}) }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByRole("button", { name: "Mark Missing Info" }).click();

  await expect(page.locator(".detail-grid")).toContainText("Hold/Need Info");
});

test("Cancel Load is rejected server-side and surfaces the error verbatim", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail({ status: "Completed" }));
  await page.route("**/api/v1/loads/1/cancel", (route) =>
    route.fulfill({
      status: 409,
      json: { error: { code: "conflict", message: "Load is Completed; further operational status changes require an override." } },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByRole("button", { name: "Cancel Load" }).click();

  await expect(page.getByText("Load is Completed; further operational status changes require an override.")).toBeVisible();
});

test("logging a communication succeeds and appears in the Communications tab", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  let logged = false;
  await page.route("**/api/v1/loads/1/communications**", (route) => {
    if (route.request().method() === "POST") {
      logged = true;
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({
      json: page1(
        logged
          ? [{ id: 1, message_type: "note", direction: "internal", recipient: "", message_body: "called customer", sent_by: "dispatcher@calitrans.test", provider: "internal", delivery_status: "", provider_message_id: "", created_at: "2026-08-15T12:00:00Z" }]
          : [],
      ),
    });
  });
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByRole("button", { name: "Communications" }).click();
  await page.getByLabel("Note").fill("called customer");
  await page.getByRole("button", { name: "Save Note" }).click();

  await expect(page.getByText("called customer")).toBeVisible();
});

test("the Timeline tab renders existing events", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByRole("button", { name: "Timeline" }).click();

  await expect(page.getByText("Load created")).toBeVisible();
});

test("a dispatcher can upload a document and sees it pending", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, loadDetail());
  let uploaded = false;
  await page.route("**/api/v1/loads/1/documents", (route) => {
    if (route.request().method() === "POST") {
      uploaded = true;
      return route.fulfill({ json: { ok: true, document_id: 1, status: "pending" } });
    }
    return route.fulfill({
      json: uploaded
        ? [{ id: 1, document_type: "load_pdf", filename: "rate.pdf", source: "web", status: "pending", created_at: "2026-08-15T12:00:00Z" }]
        : [],
    });
  });
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/loads/1");
  await page.getByRole("button", { name: "Documents" }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "rate.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf bytes") });

  await expect(page.getByText("rate.pdf")).toBeVisible();
  await expect(page.getByText("pending")).toBeVisible();
});

test("a role without load:edit/verify/cancel cannot see those controls, but can still upload documents", async ({ page }) => {
  await mockBackend(page, ACCOUNTING_ME, loadDetail());
  await loginAs(page, ACCOUNTING_ME);

  await page.goto("/app/loads/1");

  await expect(page.getByRole("button", { name: "Save Changes" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Mark Missing Info" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Mark Booking Verified" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel Load" })).not.toBeVisible();

  await page.getByRole("button", { name: "Documents" }).click();
  await expect(page.getByLabel("Attach a document")).toBeVisible();
});
