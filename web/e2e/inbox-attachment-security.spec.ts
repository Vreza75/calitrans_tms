import { expect, test, type Page } from "@playwright/test";

// Mocked FastAPI, no live backend (same convention as
// web/e2e/inbox-acceptance-extra.spec.ts) - these cover PR #23's P2
// findings that don't require a real two-origin/real-Postgres stack:
// (1) a blocked popup must never be treated as success, (2) every failure
// path must close the temporary tab, and (3) execution must be checked
// INSIDE the popup itself, not only via the opener's title (the old
// web/e2e/inbox-attachment-link.spec.ts hostile-HTML test only checked
// `page.title()` on the opener - a script that targeted the popup's own
// document, rather than reaching back through window.opener, would have
// passed that test while still having executed). The live, real-backend
// cross-origin/auth/hostile-HTML coverage in inbox-attachment-link.spec.ts
// is unchanged and still the source of truth for the real network path.

const ME_RESPONSE = {
  email: "dispatcher@calitrans.test",
  display_name: "Test Dispatcher",
  role: "dispatcher",
  permissions: ["work_item:manage"],
};

const WORK_ITEM_DETAIL = {
  id: 42,
  source_sender: "ops@continental.test",
  source_subject: "Booking confirmation RICGX1235800",
  source_received_at: "2026-08-15T12:00:00Z",
  original_email_body: "Please see attached booking.",
  parsed_data: { Customer: "Continental Industries Group" },
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

const ATTACHMENT_REF = "att-under-test";

const ATTACHMENTS_SUMMARY = {
  current: [{ attachment_ref: ATTACHMENT_REF, filename: "under-test.bin", content_type: "application/octet-stream" }],
  prior: [],
};

async function mockLoginAndDetail(page: Page): Promise<void> {
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
  await page.route("**/api/v1/work-items/42/attachments", (route) => route.fulfill({ json: ATTACHMENTS_SUMMARY }));
  await page.route("**/api/v1/work-items/42", (route) => route.fulfill({ json: WORK_ITEM_DETAIL }));
}

async function loginAndOpenDetail(page: Page): Promise<void> {
  await mockLoginAndDetail(page);
  await page.goto("/login");
  await page.getByLabel("Email").fill("dispatcher@calitrans.test");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.goto("/app/inbox?id=42");
  await expect(page.getByRole("heading", { name: "Booking confirmation RICGX1235800" })).toBeVisible();
}

test("blocked popup: never fetches attachment bytes and surfaces an error instead of silent success", async ({
  page,
}) => {
  // Deterministic popup-blocked simulation - real popup-blocker heuristics
  // are browser/OS-dependent and Playwright's automated Chromium does not
  // reliably reproduce them, so this overrides window.open() itself,
  // exactly as a real blocker would be observed from the page's
  // perspective: it returns null.
  await page.addInitScript(() => {
    window.open = () => null;
  });

  let contentFetchCalled = false;
  await page.route("**/api/v1/attachments/**/content**", (route) => {
    contentFetchCalled = true;
    return route.fulfill({ status: 200, contentType: "application/octet-stream", body: "should never be requested" });
  });

  await loginAndOpenDetail(page);
  await page.getByRole("button", { name: "under-test.bin" }).click();

  // Scoped past the Next.js route-announcer live region, which also has
  // role="alert" - the specific error text is what matters here.
  await expect(page.locator("p.field-error")).toBeVisible();
  await expect(page.getByRole("button", { name: "under-test.bin" })).toBeVisible(); // reverted from "Opening..."
  expect(contentFetchCalled).toBe(false); // finding #1: must not fetch when the popup was blocked
});

test("network failure while fetching attachment bytes closes the temporary tab and surfaces an error", async ({
  page,
}) => {
  await page.route("**/api/v1/attachments/**/content**", (route) => route.abort("failed"));

  await loginAndOpenDetail(page);
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button", { name: "under-test.bin" }).click(),
  ]);

  // tab.close() in the catch block runs fast enough that the popup can
  // already be closed before this test even gets to listen for the
  // "close" event - poll the window's own state instead of racing a
  // one-shot event that may have already fired.
  await expect.poll(() => popup.isClosed(), { timeout: 10_000 }).toBe(true); // finding #2: the blank tab must not be left orphaned
  await expect(page.locator("p.field-error")).toBeVisible();
});

test("an HTTP error while fetching attachment bytes closes the temporary tab (existing behavior preserved)", async ({
  page,
}) => {
  await page.route("**/api/v1/attachments/**/content**", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { message: "not found" } }) }),
  );

  await loginAndOpenDetail(page);
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button", { name: "under-test.bin" }).click(),
  ]);

  await expect.poll(() => popup.isClosed(), { timeout: 10_000 }).toBe(true);
  await expect(page.locator("p.field-error")).toBeVisible();
});

const XSS_PAYLOAD = '<script>document.title = "XSS-EXECUTED"; if (window.opener) { try { window.opener.document.title = "XSS-EXECUTED"; } catch (e) {} }</script>';

test("hostile HTML attachment does not execute inside the popup itself (not just the opener)", async ({ page }) => {
  await page.route("**/api/v1/attachments/**/content**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: XSS_PAYLOAD }),
  );

  await loginAndOpenDetail(page);
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button", { name: "under-test.bin" }).click(),
  ]);

  await page.waitForTimeout(1500); // give a real execution a fair chance to run
  expect(await popup.title()).not.toBe("XSS-EXECUTED"); // the strengthened check: inside the popup, not the opener
  expect(await page.title()).not.toBe("XSS-EXECUTED");
});

test("hostile SVG attachment does not execute inside the popup itself", async ({ page }) => {
  const svgPayload = `<svg xmlns="http://www.w3.org/2000/svg"><script>document.title = "XSS-EXECUTED"; if (window.opener) { try { window.opener.document.title = "XSS-EXECUTED"; } catch (e) {} }</script></svg>`;
  await page.route("**/api/v1/attachments/**/content**", (route) =>
    route.fulfill({ status: 200, contentType: "image/svg+xml", body: svgPayload }),
  );

  await loginAndOpenDetail(page);
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button", { name: "under-test.bin" }).click(),
  ]);

  await page.waitForTimeout(1500);
  expect(await popup.title()).not.toBe("XSS-EXECUTED");
  expect(await page.title()).not.toBe("XSS-EXECUTED");
});

test("MIME-spoofed attachment (HTML bytes reported as application/pdf) does not execute inside the popup", async ({
  page,
}) => {
  // The allowlist passes application/pdf through unchanged (it is a
  // genuinely inert-to-render type) - this proves that trust is still
  // safe even when the bytes behind that reported type are actually a
  // live script, not real PDF content: the browser's PDF viewer, not the
  // HTML engine, is what ends up handling the navigation.
  await page.route("**/api/v1/attachments/**/content**", (route) =>
    route.fulfill({ status: 200, contentType: "application/pdf", body: XSS_PAYLOAD }),
  );

  await loginAndOpenDetail(page);
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button", { name: "under-test.bin" }).click(),
  ]);

  await page.waitForTimeout(1500);
  expect(await popup.title()).not.toBe("XSS-EXECUTED");
  expect(await page.title()).not.toBe("XSS-EXECUTED");
});
