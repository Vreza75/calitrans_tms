import { expect, test } from "@playwright/test";

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

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

test("attachment opens successfully across origins with the real bearer token", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DISPATCHER.email);
  await page.getByLabel("Password").fill(DISPATCHER.password);
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign In" }).click();
  const loginRes = await loginResponse;
  expect(loginRes.status()).toBeLessThan(300);

  await page.goto("/app/inbox?id=1");
  await expect(page.getByRole("heading", { name: "SYNTHETIC-CALITRANS-QA::attachment-fix-verify New inbound order" })).toBeVisible();

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
  expect(bodyText).toContain("SYNTHETIC-CALITRANS-QA::attachment-fix-verify cross-origin test document.");
});

test("attachment content request is rejected without a token (auth still enforced after the fix)", async ({ page }) => {
  const res = await page.request.get("http://localhost:8000/api/v1/attachments/f202f4f025d9f1be3ce43ef8/content", {
    failOnStatusCode: false,
  });
  expect(res.status()).toBe(401);
});
