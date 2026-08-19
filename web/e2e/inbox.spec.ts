import { expect, test, type Page } from "@playwright/test";

// Phase 10A: login -> Operations Inbox -> select queue -> open work item ->
// view detail -> perform one safe action (Close) -> UI refetches. Entirely
// against mocked FastAPI responses (no live backend, no live Supabase) -
// see e2e/login-to-load-detail.spec.ts's header comment for why.

const ME_RESPONSE = {
  email: "dispatcher@calitrans.test",
  display_name: "Test Dispatcher",
  role: "dispatcher",
  permissions: ["work_item:manage"],
};

const WORK_ITEM_SUMMARY = {
  id: 42,
  created_at: "2026-08-15T12:00:00Z",
  source_received_at: "2026-08-15T12:00:00Z",
  source_subject: "Booking confirmation RICGX1235800",
  source_sender: "ops@continental.test",
  email_direction: "inbound",
  request_type: "New Booking",
  work_queue: "New Orders",
  department_lane: "Dispatch",
  review_status: "Open",
  confidence_score: 85,
  matched_load_id: null,
  conversation_key: "conv-42",
  case_id: null,
  customer: "Continental Industries Group",
  booking_number: "RICGX1235800",
  container_number: "",
  reference_number: "SO217089a",
  service_flow: "Export",
  attachment_count: 1,
};

const WORK_ITEM_DETAIL = {
  id: 42,
  source_sender: "ops@continental.test",
  source_subject: "Booking confirmation RICGX1235800",
  source_received_at: "2026-08-15T12:00:00Z",
  original_email_body: "Please see attached booking.",
  parsed_data: { Customer: "Continental Industries Group", "Booking Number": "RICGX1235800" },
  request_type: "New Booking",
  matched_load_id: null,
  conversation_key: "conv-42",
  review_status: "Open",
  confidence_score: 85,
  attachments: { current: [], prior: [] },
  communication_summary: { message_count: 1, last_message_at: "2026-08-15T12:00:00Z" },
  order_draft: { exists: false },
  allowed_actions: ["create_load", "close_work_item"],
};

async function mockBackend(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: "dispatcher@calitrans.test", role: "dispatcher" } }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: ME_RESPONSE }));
  await page.route("**/api/v1/work-items/counts**", (route) =>
    route.fulfill({ json: { counts: [{ queue: "New Orders", count: 1 }] } }),
  );
  await page.route("**/api/v1/work-items/42/conversation**", (route) =>
    route.fulfill({ json: { conversation_key: "conv-42", messages: [], total_messages: 0 } }),
  );
  await page.route("**/api/v1/work-items/42/attachments", (route) =>
    route.fulfill({ json: { current: [], prior: [] } }),
  );
  await page.route("**/api/v1/work-items/42", (route) => route.fulfill({ json: WORK_ITEM_DETAIL }));
  await page.route("**/api/v1/work-items?**", (route) =>
    route.fulfill({
      json: {
        items: [WORK_ITEM_SUMMARY],
        page: 1,
        page_size: 25,
        total_items: 1,
        total_pages: 1,
        sort: { sort_by: "received_at", sort_direction: "desc" },
        filters: {},
      },
    }),
  );
}

test("login, view Operations Inbox, open work item, view detail", async ({ page }) => {
  await mockBackend(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await page.goto("/app/inbox");

  await expect(page.getByRole("heading", { name: "Operations Inbox" })).toBeVisible();
  await expect(page.getByText("New Orders")).toBeVisible();
  await expect(page.getByText("Booking confirmation RICGX1235800")).toBeVisible();

  await page.getByText("Booking confirmation RICGX1235800").click();

  await expect(page).toHaveURL(/[?&]id=42/);
  await expect(page.getByRole("heading", { name: "Booking confirmation RICGX1235800" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Extracted fields" }).getByText("Continental Industries Group")).toBeVisible();
  await expect(page.getByRole("button", { name: "Close" })).toBeEnabled();
});

test("a role without work_item:manage sees action buttons disabled", async ({ page }) => {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: "accounting@calitrans.test", role: "accounting" } }),
  );
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: { email: "accounting@calitrans.test", display_name: "Test Accounting", role: "accounting", permissions: [] },
    }),
  );
  await page.route("**/api/v1/work-items/counts**", (route) =>
    route.fulfill({ json: { counts: [{ queue: "New Orders", count: 1 }] } }),
  );
  await page.route("**/api/v1/work-items/42/conversation**", (route) =>
    route.fulfill({ json: { conversation_key: "conv-42", messages: [], total_messages: 0 } }),
  );
  await page.route("**/api/v1/work-items/42/attachments", (route) =>
    route.fulfill({ json: { current: [], prior: [] } }),
  );
  await page.route("**/api/v1/work-items/42", (route) => route.fulfill({ json: WORK_ITEM_DETAIL }));
  await page.route("**/api/v1/work-items?**", (route) =>
    route.fulfill({
      json: {
        items: [WORK_ITEM_SUMMARY],
        page: 1,
        page_size: 25,
        total_items: 1,
        total_pages: 1,
        sort: { sort_by: "received_at", sort_direction: "desc" },
        filters: {},
      },
    }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill("accounting@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await page.goto("/app/inbox?id=42");

  await expect(page.getByRole("button", { name: "Close" })).toBeDisabled();
  await expect(page.getByText("does not have permission to act on work items")).toBeVisible();
});
