import { defineConfig, devices } from "@playwright/test";

// Phase 10 STEP 39: one browser E2E flow (login -> loads -> load detail).
// The backend and Supabase realtime are mocked per-test via page.route -
// this suite must never require a live FastAPI process or live Supabase
// project, so it runs the same in CI as it does locally.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
