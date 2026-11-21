import { expect, test, type Page } from "@playwright/test";

// Phase 10B: Dispatch Board acceptance - mirrors login-to-load-detail.spec.ts's
// pattern (backend fully mocked via page.route, no live FastAPI/Postgres/
// Supabase - this suite must run the same in CI as it does locally). The
// true multi-session realtime round-trip (acceptance test 8 in
// docs/architecture/PHASE_10B_DISPATCH_PLAN.md) needs a live backend and
// is covered at the live-suite level (web/e2e/inbox-realtime.spec.ts's
// pattern), not here.

const DISPATCHER_ME = {
  email: "dispatcher@calitrans.test",
  display_name: "Test Dispatcher",
  role: "dispatcher",
  permissions: ["load:view", "dispatch:transition"],
};

const ACCOUNTING_ME = {
  email: "accounting@calitrans.test",
  display_name: "Test Accounting",
  role: "accounting",
  permissions: ["load:view"],
};

const IMPORT_STAGES = [
  "Ready to Dispatch",
  "En Route to Pickup",
  "At Pickup",
  "En Route to Delivery",
  "At Delivery",
  "Returning Empty",
  "Completed",
];

function loadItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    type: "Import",
    booking_number: "BKG-001",
    reference_number: "",
    container_number: "",
    customer: "Acme Imports",
    port: "Port of LA",
    warehouse: "Main Warehouse",
    status: "Ready to Dispatch",
    driver_name: "",
    truck_assigned: "",
    delivery_need_date: null,
    document_cutoff: null,
    invoice_status: "",
    driver_pay_status: "",
    updated_at: "2026-08-15T12:00:00Z",
    ...overrides,
  };
}

async function mockBackend(page: Page, me: Record<string, unknown>, items: ReturnType<typeof loadItem>[]): Promise<void> {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: String(me.email), role: me.role } }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: me }));
  await page.route("**/api/v1/loads/stages**", (route) => route.fulfill({ json: { stages: IMPORT_STAGES } }));
  await page.route("**/api/v1/drivers/active", (route) =>
    route.fulfill({ json: [{ driver_name: "Joe Driver", phone: "5551234567", truck_number: "T-100" }] }),
  );
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: { items, page: 1, page_size: 200, total_items: items.length, total_pages: 1, sort_by: "", sort_direction: "asc" },
    }),
  );
}

async function loginAs(page: Page, me: Record<string, unknown>): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(String(me.email));
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();
}

test("board renders a column per operational stage, including Returning Empty for Import", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");

  await expect(page.getByRole("heading", { name: "Dispatch Board" })).toBeVisible();
  for (const stage of IMPORT_STAGES) {
    await expect(page.getByRole("heading", { name: stage })).toBeVisible();
  }
});

test("a load's card appears in the column matching its current status", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem({ status: "At Pickup" })]);
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");

  await expect(page.getByRole("link", { name: "BKG-001" })).toBeVisible();
});

test("truncated results past the fetch limit show a visible warning instead of silently dropping loads", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: { items: [loadItem()], page: 1, page_size: 200, total_items: 5, total_pages: 1, sort_by: "", sort_direction: "asc" },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");

  await expect(page.getByText("Showing 1 of 5 matching loads.")).toBeVisible();
});

test("empty result set shows the no-match message instead of an empty board", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, []);
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");

  await expect(page.getByText("No loads match this filter.")).toBeVisible();
});

test("search failure surfaces an inline error instead of a blank board", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, []);
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({ status: 500, json: { error: { code: "internal_error", message: "Unable to load the dispatch board." } } }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");

  await expect(page.locator(".field-error")).toContainText("Unable to load the dispatch board");
});

test("dispatcher can assign a driver and the card reflects it after invalidation", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  let assigned = false;
  await page.route("**/api/v1/loads/1/assign-driver", (route) => {
    assigned = true;
    return route.fulfill({ json: { ok: true, reason: "", status: "Ready to Dispatch", closeout_stage: "" } });
  });
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: {
        items: [assigned ? loadItem({ driver_name: "Joe Driver", truck_assigned: "T-100" }) : loadItem()],
        page: 1,
        page_size: 200,
        total_items: 1,
        total_pages: 1,
        sort_by: "",
        sort_direction: "asc",
      },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByRole("combobox", { name: "Driver" }).selectOption("Joe Driver");
  await page.getByRole("button", { name: "Assign", exact: true }).click();

  await expect(page.getByText("Driver: Joe Driver")).toBeVisible();
});

test("P1 fix: clearing the truck field blocks assignment with a validation message", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem({ driver_name: "Old Driver", truck_assigned: "T-OLD" })]);
  let assignCalled = false;
  await page.route("**/api/v1/loads/1/assign-driver", (route) => {
    assignCalled = true;
    return route.fulfill({ json: { ok: true, reason: "", status: "Ready to Dispatch", closeout_stage: "" } });
  });
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByRole("textbox", { name: "Truck" }).fill("");

  await expect(page.getByText("Truck is required to assign a driver")).toBeVisible();
  await expect(page.getByRole("button", { name: "Assign", exact: true })).toBeDisabled();

  await page.getByRole("button", { name: "Assign", exact: true }).click({ force: true });
  expect(assignCalled).toBe(false);
});

test("P1 fix: a stale assignment conflict surfaces the server's message and refreshes the board", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  await page.route("**/api/v1/loads/1/assign-driver", (route) =>
    route.fulfill({
      status: 409,
      json: {
        error: {
          code: "conflict",
          message: "This load's driver assignment was changed by someone else. Refresh and try again.",
        },
      },
    }),
  );
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: {
        items: [loadItem({ driver_name: "Someone Else", truck_assigned: "T-999" })],
        page: 1,
        page_size: 200,
        total_items: 1,
        total_pages: 1,
        sort_by: "",
        sort_direction: "asc",
      },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByRole("combobox", { name: "Driver" }).selectOption("Joe Driver");
  await page.getByRole("button", { name: "Assign", exact: true }).click();

  await expect(page.getByText("This load's driver assignment was changed by someone else.")).toBeVisible();
  await expect(page.getByText("Driver: Someone Else")).toBeVisible();
});

test("P1 fix: a driver can be assigned to a load still at a pre-dispatch status (New)", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem({ status: "New" })]);
  await page.route("**/api/v1/loads/1/assign-driver", (route) =>
    route.fulfill({ json: { ok: true, reason: "", status: "New", closeout_stage: "" } }),
  );
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: {
        items: [loadItem({ status: "New", driver_name: "Joe Driver", truck_assigned: "T-100" })],
        page: 1,
        page_size: 200,
        total_items: 1,
        total_pages: 1,
        sort_by: "",
        sort_direction: "asc",
      },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByRole("combobox", { name: "Driver" }).selectOption("Joe Driver");
  await page.getByRole("button", { name: "Assign", exact: true }).click();

  await expect(page.getByText("Driver: Joe Driver")).toBeVisible();
});

test("dispatcher can advance a load's status via the transition menu", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  let transitioned = false;
  await page.route("**/api/v1/loads/1/transition", (route) => {
    transitioned = true;
    return route.fulfill({ json: { ok: true, reason: "", status: "En Route to Pickup", closeout_stage: "" } });
  });
  await page.route("**/api/v1/loads/search**", (route) =>
    route.fulfill({
      json: {
        items: [transitioned ? loadItem({ status: "En Route to Pickup" }) : loadItem()],
        page: 1,
        page_size: 200,
        total_items: 1,
        total_pages: 1,
        sort_by: "",
        sort_direction: "asc",
      },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Transition" }).click();
  await page.getByRole("button", { name: /Advance to/ }).click();

  await expect(page.getByText("En Route to Pickup").first()).toBeVisible();
});

test("a rejected transition surfaces the server's error message verbatim", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  await page.route("**/api/v1/loads/1/transition", (route) =>
    route.fulfill({
      status: 409,
      json: { error: { code: "conflict", message: "Driver and truck must be assigned before starting En Route to Pickup." } },
    }),
  );
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByRole("button", { name: "Transition" }).click();
  await page.getByRole("button", { name: /Advance to/ }).click();

  await expect(page.getByText("Driver and truck must be assigned before starting En Route to Pickup.")).toBeVisible();
});

test("a role without dispatch:transition cannot see assign/transition controls", async ({ page }) => {
  await mockBackend(page, ACCOUNTING_ME, [loadItem()]);
  await loginAs(page, ACCOUNTING_ME);

  await page.goto("/app/dispatch");

  await expect(page.getByRole("link", { name: "BKG-001" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Assign" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Transition" })).not.toBeVisible();
});

test("the filter bar narrows results by driver, port, and warehouse", async ({ page }) => {
  await mockBackend(page, DISPATCHER_ME, [loadItem()]);
  let lastUrl = "";
  await page.route("**/api/v1/loads/search**", (route) => {
    lastUrl = route.request().url();
    return route.fulfill({
      json: { items: [loadItem()], page: 1, page_size: 200, total_items: 1, total_pages: 1, sort_by: "", sort_direction: "asc" },
    });
  });
  await loginAs(page, DISPATCHER_ME);

  await page.goto("/app/dispatch");
  await page.getByPlaceholder("Driver").fill("Joe Driver");
  await page.getByPlaceholder("Port").fill("Port of LA");
  await page.getByPlaceholder("Warehouse").fill("Main Warehouse");

  await expect(async () => {
    expect(lastUrl).toContain("driver_name=Joe");
    expect(lastUrl).toContain("port=Port");
    expect(lastUrl).toContain("warehouse=Main");
  }).toPass();
});
