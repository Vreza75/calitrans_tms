import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";

import { cleanupDataset, marker, seedDataset, uniqueDataset } from "./fixtures/qa-sample-data";

// REAL local Supabase Realtime stack (remapped ports 64320-64329, avoiding
// the pre-existing unrelated victor-ai-os project's default-port instance -
// never touched), REAL FastAPI (REALTIME_ENABLED=true), REAL disposable
// Postgres. No mocking. Proves items 8 (worker-to-UI) and 9 (cross-window)
// via the actual production chain: application/work_items/commands.py's
// close_work_item() persists a pending domain_events row in the same
// transaction -> scripts/process_realtime_events.py (the exact script the
// scheduled GitHub Actions worker runs) claims and broadcasts it via
// Supabase Realtime -> both browser windows hold a real
// @supabase/supabase-js subscription (web/lib/realtime/client.ts) -> each
// window's React Query cache is invalidated and refetches -> both update
// with zero manual refresh.
//
// Fixture fix: this test used to depend on a fixed, externally pre-seeded
// "realtime-ci-02" dataset (manually seeded once outside this file, same
// one-shot problem PR #22's other acceptance specs had with fixed numeric
// ids). It now seeds its own run-scoped dataset through
// web/e2e/fixtures/qa-sample-data.ts - a fresh, globally-unique dataset id
// every run/worker/retry - so repeated and parallel runs each get their
// own independent row and never collide with or depend on anything seeded
// out of band. The work-item id is still never assumed: it's located by
// searching for this run's own unique SYNTHETIC-CALITRANS-QA::<dataset>
// marker through the real search endpoint, exactly as before.

const DISPATCHER = { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

async function loginAndOpenInbox(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DISPATCHER.email);
  await page.getByLabel("Password").fill(DISPATCHER.password);
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign In" }).click();
  await loginResponse;
  await page.goto("/app/inbox");
}

async function findMarkerWorkItemId(page: import("@playwright/test").Page, datasetMarker: string): Promise<number> {
  await page.getByPlaceholder("Search subject, sender, booking, container, reference...").fill(datasetMarker);
  const listResponse = page.waitForResponse((r) => r.url().includes("/api/v1/work-items?") && r.url().includes("search="));
  await page.getByRole("button", { name: "Search" }).click();
  const res = await listResponse;
  const body = await res.json();
  expect(body.items.length).toBe(1);
  return body.items[0].id as number;
}

test("worker-to-UI and cross-window realtime: both windows update without a manual refresh", async ({ browser }) => {
  const dataset = uniqueDataset("pr22-realtime");
  seedDataset(dataset);
  const datasetMarker = marker(dataset);

  try {
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    await loginAndOpenInbox(pageA);
    await loginAndOpenInbox(pageB);

    // Real websocket to the real local Supabase stack, not the default/
    // disconnected indicator state.
    await expect(pageA.getByText("Live updates connected")).toBeVisible({ timeout: 15_000 });
    await expect(pageB.getByText("Live updates connected")).toBeVisible({ timeout: 15_000 });

    const workItemId = await findMarkerWorkItemId(pageA, datasetMarker);
    await expect(pageB.getByText(datasetMarker).first()).toBeVisible();

    // Neither window ever navigates away from the list (/app/inbox) from
    // here on - navigating into the detail view would itself trigger a
    // normal (non-realtime) refetch on return, which would prove nothing
    // about the realtime path specifically. The dispatcher action is fired
    // as a direct authenticated request instead of a UI click-through, so
    // window A's own page never leaves the list it's being checked against.
    const token = await pageA.evaluate(() => sessionStorage.getItem("calitrans_web_session_token"));
    const closeRes = await pageA.request.post(`http://localhost:8000/api/v1/work-items/${workItemId}/close`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { reason: "" },
    });
    expect(closeRes.status()).toBeLessThan(300);

    // "The worker": run the exact script the scheduled GitHub Actions job
    // runs, once, synchronously - the only external action in this test.
    const repoRoot = process.cwd().replace(/web$/, "");
    const output = execFileSync(
      "C:/GitHub/calitrans_tms_postgres_upgrade_clean/.venv/Scripts/python.exe",
      ["scripts/process_realtime_events.py", "process"],
      {
        cwd: repoRoot,
        env: {
          ...process.env,
          DATABASE_URL: "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa",
          REALTIME_ENABLED: "true",
          SUPABASE_URL: "http://127.0.0.1:64321",
          SUPABASE_SERVICE_ROLE_KEY:
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU",
        },
      },
    ).toString();
    expect(output).toContain("outcome=published");
    console.log("process_realtime_events.py output:", output);

    // Neither window is refreshed. This run's own dataset has exactly one
    // matching item, so a successful close empties it from both windows'
    // lists - purely via the realtime invalidation path.
    await expect(pageA.getByText(datasetMarker)).toHaveCount(0, { timeout: 15_000 });
    await expect(pageB.getByText(datasetMarker)).toHaveCount(0, { timeout: 15_000 });

    await contextA.close();
    await contextB.close();
  } finally {
    cleanupDataset(dataset);
  }
});
