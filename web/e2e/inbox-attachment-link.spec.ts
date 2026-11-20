import { API_BASE, expect, gotoAuthenticated, test } from "./fixtures/auth";
import {
  cleanupAttachmentFixture,
  cleanupDataset,
  marker,
  seedAttachmentFixture,
  seedDataset,
  uniqueDataset,
} from "./fixtures/qa-sample-data";

// Regression test for the attachment-open defect: the old code used a
// relative `<a href="/api/v1/attachments/.../content">`, which only ever
// worked if Next.js and FastAPI shared an origin. This repo's own
// CORS_ALLOWED_ORIGINS config (api/main.py) proves cross-origin is the
// actual supported topology (Next.js on :3000, FastAPI on :8000 by
// default - see web/lib/api/client.ts's own fallback). A relative href
// also can't carry the required Bearer token at all (api/auth.py's
// require_auth reads only the Authorization header, never a cookie), so
// this test must run against the real two-origin, real-auth stack - not
// mocked - to actually prove the fix.
//
// Both attachment tests below used to assume a pre-existing, hand-seeded
// work item at the fixed id=1 with specific attachment files already on
// disk - that row/those files existed nowhere in this repo's seed path
// (scripts/sample_data.py deliberately does not seed real documents - see
// docs/SAMPLE_DATA_GUIDE.md), so it only worked against whichever
// database happened to have that exact manual setup, never a fresh
// disposable QA database. Each test now seeds its own run-scoped dataset
// and a real attachment file via seedAttachmentFixture() (writes an
// actual file, merges its path into that row's own parsed_data - see
// that function's own comment) and discovers the real work-item id
// through the API, the same pattern every other live spec in this repo
// already uses.

async function findWorkItemId(page: import("@playwright/test").Page, token: string, dataset: string): Promise<number> {
  const res = await page.request.get(`${API_BASE}/api/v1/work-items?search=${encodeURIComponent(marker(dataset))}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await res.json();
  expect(body.items.length).toBe(1);
  return body.items[0].id as number;
}

test("attachment opens successfully across origins with the real bearer token", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr23-attach-cross");
  seedDataset(dataset);
  const filePath = seedAttachmentFixture(dataset, {
    filename: "qa_cross_origin_test.txt",
    contentType: "text/plain",
    body: `${marker(dataset)} cross-origin test document.`,
  });
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);
    await expect(page.getByRole("heading", { name: `${marker(dataset)} New inbound order` })).toBeVisible();

    const contentFetch = page.waitForResponse(
      (r) => r.url().includes("/api/v1/attachments/") && r.url().includes("/content"),
    );
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      page.getByRole("button", { name: "qa_cross_origin_test.txt" }).click(),
    ]);
    const contentRes = await contentFetch;

    // The request must have actually hit FastAPI's origin (:8000), not
    // Next.js's own (:3000) - this is what the relative href got wrong.
    expect(new URL(contentRes.url()).origin).toBe("http://localhost:8000");
    expect(contentRes.status()).toBe(200);

    // The new tab must have received the real attachment bytes as a blob,
    // not a 404 page from Next.js's own server.
    // The popup opens blank first (synchronously, to preserve the user
    // gesture) and only navigates to the blob: URL once the fetch
    // resolves - wait for that navigation specifically, not just the
    // popup's initial about:blank load.
    await popup.waitForURL((url) => url.protocol === "blob:");
    const bodyText = await popup.evaluate(() => document.body.innerText);
    expect(bodyText).toContain(`${marker(dataset)} cross-origin test document.`);
  } finally {
    cleanupAttachmentFixture(filePath);
    cleanupDataset(dataset);
  }
});

test("attachment content request is rejected without a token (auth still enforced after the fix)", async ({ page }) => {
  // No seeded fixture needed: router-level require_role() (api/routers/
  // attachments.py) runs before the handler ever resolves attachment_ref
  // or work_item_id, so an unauthenticated request 401s regardless of
  // whether either value corresponds to anything real.
  const res = await page.request.get("http://localhost:8000/api/v1/attachments/f202f4f025d9f1be3ce43ef8/content", {
    failOnStatusCode: false,
  });
  expect(res.status()).toBe(401);
});

test("hostile HTML attachment does not execute script in this page's origin", async ({ page, dispatcherSession }) => {
  const dataset = uniqueDataset("pr23-attach-hostile");
  seedDataset(dataset);
  const xssPayload =
    '<script>document.title = "XSS-EXECUTED"; if (window.opener) { try { window.opener.document.title = "XSS-EXECUTED"; } catch (e) {} }</script>';
  const filePath = seedAttachmentFixture(dataset, {
    filename: "hostile.html",
    contentType: "text/html",
    body: xssPayload,
  });
  try {
    const token = dispatcherSession.token;
    const workItemId = await findWorkItemId(page, token, dataset);

    await gotoAuthenticated(page, token, `/app/inbox?id=${workItemId}`);
    await expect(page.getByRole("button", { name: "hostile.html" })).toBeVisible();

    const contentFetch = page.waitForResponse(
      (r) => r.url().includes("/attachments/") && r.url().includes("/content"),
    );
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      page.getByRole("button", { name: "hostile.html" }).click(),
    ]);
    const contentRes = await contentFetch;
    expect(contentRes.status()).toBe(200);
    // Server reports text/html; the client-side mitigation must not pass
    // that through to the blob the browser acts on.
    expect((contentRes.headers()["content-type"] ?? "")).toContain("text/html");

    // The forced-safe blob type (application/octet-stream) makes Chrome
    // treat the navigation as a download rather than a document load - it
    // never becomes a renderable HTML document, so the embedded <script>
    // never runs. (Confirmed empirically: navigating the popup to this
    // blob aborts the navigation entirely, rather than loading a page -
    // the opposite of what happened with the plain-text attachment.)
    //
    // Checking only the opener's title (page.title()) is not sufficient: a
    // hostile payload that targets its own document - rather than reaching
    // back through window.opener - would execute undetected by that check
    // alone. The popup itself, not just the opener, is asserted here.
    await page.waitForTimeout(1000);
    expect(await popup.title()).not.toBe("XSS-EXECUTED");
    expect(await page.title()).not.toBe("XSS-EXECUTED");
  } finally {
    cleanupAttachmentFixture(filePath);
    cleanupDataset(dataset);
  }
});
