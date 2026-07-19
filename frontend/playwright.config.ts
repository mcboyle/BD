import { defineConfig, devices } from "@playwright/test";

// Playwright config for the D3 SPA e2e smoke tests.
// Run: BD_E2E_BASE=http://localhost:5555 npx playwright test
//
// The operator must have a real BulkDownloader running at the BASE
// URL with `frontend/dist/` built. This config does NOT start a
// server — too much variance across operator environments (stash
// vs local vs CI).

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.BD_E2E_BASE || "http://localhost:5555",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    // Mobile viewport — the SPA is mobile-first; smoke this shape too.
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
    },
  ],
});
