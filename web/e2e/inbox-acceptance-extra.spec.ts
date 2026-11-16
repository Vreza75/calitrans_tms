import { expect, test, type Page } from "@playwright/test";

// Acceptance-checklist automation extending web/e2e/inbox.spec.ts: covers
// search/filter usability (item 3 of the PR #12 manual checklist) and a
// full click-through of one mutation action (Close) confirming the UI
// actually calls the application-command endpoint and reflects the result
// (contributes evidence toward item 7, "create/update/link/close all work
// end-to-end"). Mocked FastAPI, no live backend - same convention as
// e2e/inbox.spec.ts.

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

const OTHER_SUMMARY = {
  ...WORK_ITEM_SUMMARY,
  id: 99,
  source_subject: "Appointment request for BKG-900",
  customer: "Other Customer LLC",
  booking_number: "BKG-900",
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

async function mockLogin(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ json: { token: "test-session-token", actor: "dispatcher@calitrans.test", role: "dispatcher" } }),
  );
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: ME_RESPONSE }));
  await page.route("**/api/v1/work-items/counts**", (route) =>
    route.fulfill({ json: { counts: [{ queue: "New Orders", count: 2 }] } }),
  );
  await page.route("**/api/v1/work-items/42/conversation**", (route) =>
    route.fulfill({ json: { conversation_key: "conv-42", messages: [], total_messages: 0 } }),
  );
  await page.route("**/api/v1/work-items/42/attachments", (route) =>
    route.fulfill({ json: { current: [], prior: [] } }),
  );
  await page.route("**/api/v1/work-items/42", (route) => route.fulfill({ json: WORK_ITEM_DETAIL }));
}

test("search filters the work-item list client-request (item 3: search/filter usable)", async ({ page }) => {
  await mockLogin(page);

  let lastSearchParam: string | null = null;
  await page.route("**/api/v1/work-items?**", (route) => {
    const url = new URL(route.request().url());
    lastSearchParam = url.searchParams.get("search");
    const items = lastSearchParam ? [OTHER_SUMMARY] : [WORK_ITEM_SUMMARY, OTHER_SUMMARY];
    return route.fulfill({
      json: {
        items,
        page: 1,
        page_size: 25,
        total_items: items.length,
        total_pages: 1,
        sort: { sort_by: "received_at", sort_direction: "desc" },
        filters: {},
      },
    });
  });

  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await page.goto("/app/inbox");
  await expect(page.getByText("Booking confirmation RICGX1235800")).toBeVisible();
  await expect(page.getByText("Appointment request for BKG-900")).toBeVisible();

  await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill("BKG-900");
  await page.getByRole("button", { name: "Search" }).click();

  // The search term must actually reach the FastAPI request (server-side
  // filtering, not a client-side substring hack duplicating business logic).
  await expect.poll(() => lastSearchParam).toBe("BKG-900");
  await expect(page.getByText("Appointment request for BKG-900")).toBeVisible();
  await expect(page.getByText("Booking confirmation RICGX1235800")).not.toBeVisible();
});

test("Close action calls the command endpoint and the item leaves the queue (item 7: close end-to-end)", async ({
  page,
}) => {
  await mockLogin(page);

  let closeCalled = false;
  let closeBody: unknown = null;
  let itemClosed = false;

  await page.route("**/api/v1/work-items?**", (route) =>
    route.fulfill({
      json: {
        items: itemClosed ? [] : [WORK_ITEM_SUMMARY],
        page: 1,
        page_size: 25,
        total_items: itemClosed ? 0 : 1,
        total_pages: 1,
        sort: { sort_by: "received_at", sort_direction: "desc" },
        filters: {},
      },
    }),
  );

  await page.route("**/api/v1/work-items/42/close", async (route) => {
    closeCalled = true;
    closeBody = route.request().postDataJSON();
    itemClosed = true;
    await route.fulfill({ json: { ok: true, work_item_id: 42 } });
  });

  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();

  await page.goto("/app/inbox?id=42");
  await expect(page.getByRole("heading", { name: "Booking confirmation RICGX1235800" })).toBeVisible();

  await page.getByRole("button", { name: "Close" }).click();

  await expect.poll(() => closeCalled).toBe(true);
  expect(closeBody).toEqual({ reason: "" });

  // After the mutation, the query invalidation path must refetch the list
  // through the same application-command boundary - not a client-side
  // optimistic delete that could drift from the server's real state. Scoped
  // to the list row specifically: the detail pane legitimately keeps
  // showing the now-closed item's heading until the dispatcher navigates
  // away - that's expected UX, not the thing under test here.
  await expect(page.locator(".inbox-work-item-row__subject")).not.toBeVisible();
});
