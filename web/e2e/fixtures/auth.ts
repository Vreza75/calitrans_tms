import { test as base, expect, type Page } from "@playwright/test";

// Shared worker-isolated authentication for the live acceptance suite
// (inbox-acceptance-live/-round3, inbox-attachment-link, inbox-realtime).
//
// P1 fix: every live spec file previously logged in independently, once
// (sometimes twice - see git history) per TEST, all against the single
// shared "dispatcher.synthetic-qa@calitrans.invalid" account. Playwright's
// fullyParallel execution runs every test in every live file at once, so a
// full concurrent run of the whole live suite pushed that one account's
// combined login-attempt count well past api/rate_limit.py's real
// 5-attempts/60s sliding-window limit (confirmed live: real 429s). That
// limiter is a production protection (see CLAUDE.md's "never weaken
// production protections" rule) - not something to loosen.
//
// The fix has two parts, combined:
//   1. Worker isolation: DISPATCHER_ACCOUNTS is a pool of independently
//      provisioned synthetic accounts (same password, disposable QA data
//      only - see docs/SAMPLE_DATA_GUIDE.md). Each Playwright worker picks
//      one deterministically from workerInfo.parallelIndex, so concurrent
//      workers never share a rate-limit bucket (the limiter keys on
//      client+email - see api/rate_limit.py's _client_key()).
//   2. Coordinated reuse: dispatcherSession is a WORKER-scoped fixture, so
//      it logs in at most ONCE per worker process no matter how many
//      tests that worker runs - every test in that worker reuses the same
//      cached token instead of calling /api/v1/auth/login again.
//
// Together: total real login calls for a full concurrent run of the whole
// live suite = at most one per worker, spread across up to 6 independent
// accounts - not one per test against one shared account.
export const DISPATCHER_ACCOUNTS = [
  { email: "dispatcher.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" },
  { email: "dispatcher.synthetic-qa-1@calitrans.invalid", password: "Qa-Test-Pass-123!" },
  { email: "dispatcher.synthetic-qa-2@calitrans.invalid", password: "Qa-Test-Pass-123!" },
  { email: "dispatcher.synthetic-qa-3@calitrans.invalid", password: "Qa-Test-Pass-123!" },
  { email: "dispatcher.synthetic-qa-4@calitrans.invalid", password: "Qa-Test-Pass-123!" },
  { email: "dispatcher.synthetic-qa-5@calitrans.invalid", password: "Qa-Test-Pass-123!" },
];

export const ACCOUNTING = { email: "accounting.synthetic-qa@calitrans.invalid", password: "Qa-Test-Pass-123!" };

export const API_BASE = "http://localhost:8000";

// The exact sessionStorage key web/lib/auth/AuthContext.tsx reads on
// mount - see that file's SESSION_STORAGE_KEY constant.
const SESSION_STORAGE_KEY = "calitrans_web_session_token";

export type DispatcherSession = { token: string; email: string };

export const test = base.extend<object, { dispatcherSession: DispatcherSession }>({
  dispatcherSession: [
    // Playwright's fixture resolver statically parses this parameter's
    // destructuring pattern to determine which fixtures this fixture
    // itself depends on (none here) - a named parameter instead of `{}`
    // breaks that detection.
    async ({}, use, workerInfo) => {
      const creds = DISPATCHER_ACCOUNTS[workerInfo.parallelIndex % DISPATCHER_ACCOUNTS.length];
      const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: creds.email, password: creds.password }),
      });
      if (!res.ok) {
        throw new Error(`Worker-scoped dispatcher login failed for ${creds.email}: HTTP ${res.status}`);
      }
      const body = (await res.json()) as { token: string };
      await use({ token: body.token, email: creds.email });
    },
    { scope: "worker" },
  ],
});

export { expect };

// Injects an already-issued session token directly into the browser's
// sessionStorage instead of performing a second real login network call.
// sessionStorage is origin-scoped, so the app's own origin must be
// visited once before it can be written; the subsequent goto(targetPath)
// is a real navigation, so AuthContext's mount-time bootstrap effect
// reads the now-present token and calls the (unthrottled) GET /api/v1/me
// to reach the authenticated state - the same end state a real UI login
// would reach, without a second /api/v1/auth/login call.
export async function gotoAuthenticated(page: Page, token: string, targetPath: string): Promise<void> {
  await page.goto("/login");
  await page.evaluate(
    ({ key, value }) => window.sessionStorage.setItem(key, value),
    { key: SESSION_STORAGE_KEY, value: token },
  );
  await page.goto(targetPath);
}
