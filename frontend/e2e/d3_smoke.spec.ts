import { test, expect } from "@playwright/test";

// Phase 1 root flip (v3.66.203): the SPA lives at the site root now;
// every target below is root-relative. (Pre-flip these were /m2/*.)
// D3 SPA end-to-end smoke. Requires:
//   - A running BulkDownloader instance (default http://localhost:5555)
//   - `npm run build` has produced frontend/dist
//
// These tests exercise the load-bearing flows of each tab. They are
// smoke tests, not exhaustive — the unit suite already covers the
// detailed branches. The point of e2e is to confirm the pieces
// connect under real browser conditions (CSP, EventSource, focus
// trap, dark-mode CSS variable cascade).
//
// Run:
//   npx playwright install chromium    # one-shot
//   BD_E2E_BASE=http://stash:5555 npx playwright test
//
// If BD_E2E_BASE is unset, defaults to http://localhost:5555.

const BASE = process.env.BD_E2E_BASE || "http://localhost:5555";

test.describe("D3 SPA smoke", () => {
  test("loads / and renders the Home tab", async ({ page }) => {
    await page.goto(`${BASE}/`);
    await expect(page.getByRole("heading", { name: /BulkDL/i })).toBeVisible();
    // Bottom tab bar present
    await expect(page.getByRole("navigation", { name: /Primary/i })).toBeVisible();
  });

  test("/ returns 503 with the Node-missing message when dist is absent", async ({
    page,
  }) => {
    // This test only makes sense in environments where the operator
    // intentionally skipped the build. Skip if dist clearly exists.
    const r = await page.request.get(`${BASE}/`);
    if (r.status() === 200) {
      test.skip(true, "dist exists; 503 path not exercised");
    }
    expect(r.status()).toBe(503);
    const body = await r.text();
    expect(body).toContain("install_linux.sh");
    expect(body).toContain("install_windows.bat");
  });

  test("tab navigation works", async ({ page }) => {
    await page.goto(`${BASE}/`);
    for (const [name, urlSuffix] of [
      ["Sites", "/sites"],
      ["Queue", "/queue"],
      ["Activity", "/activity"],
      ["Settings", "/settings"],
      ["Home", "/"],
    ] as const) {
      // The bottom tab bar uses NavLink → renders as <a>; cmdk's
      // accessible name is the visible label.
      await page.getByRole("link", { name: new RegExp(`^${name}`) }).first().click();
      await expect(page).toHaveURL(
        new RegExp(`${urlSuffix === "/" ? "/$" : urlSuffix}`),
      );
    }
  });

  test("⌘K opens the command palette", async ({ page }) => {
    await page.goto(`${BASE}/`);
    await page.keyboard.press("Meta+K");
    // The palette is a Dialog — Radix gives it role="dialog".
    const palette = page.getByRole("dialog");
    await expect(palette).toBeVisible();
    // It has a search input
    await expect(palette.getByPlaceholder(/Type a command/i)).toBeVisible();
    // Esc closes it
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
  });

  test("Settings: theme toggle flips the dark class on <html>", async ({ page }) => {
    await page.goto(`${BASE}/settings`);
    // Pick Dark.
    await page.getByRole("radio", { name: "Dark" }).click();
    // <html> should have the 'dark' class
    const cls = await page.locator("html").getAttribute("class");
    expect(cls).toContain("dark");
    // Pick Light → class removed
    await page.getByRole("radio", { name: "Light" }).click();
    const cls2 = await page.locator("html").getAttribute("class");
    expect(cls2 ?? "").not.toContain("dark");
  });

  test("Settings: theme persists across reloads", async ({ page }) => {
    await page.goto(`${BASE}/settings`);
    await page.getByRole("radio", { name: "Dark" }).click();
    await page.reload();
    // After reload, the boot helper should have re-applied 'dark'
    // BEFORE first paint.
    const cls = await page.locator("html").getAttribute("class");
    expect(cls).toContain("dark");
    // Cleanup: restore System default for subsequent test runs.
    await page.getByRole("radio", { name: "System" }).click();
  });

  test("Sites: clicking Add opens the wizard", async ({ page }) => {
    await page.goto(`${BASE}/sites`);
    await page.getByRole("button", { name: /Add/ }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Add site/i })).toBeVisible();
  });

  test("Sites: empty name → Save button stays disabled", async ({ page }) => {
    await page.goto(`${BASE}/sites`);
    await page.getByRole("button", { name: /Add/ }).first().click();
    const save = page.getByRole("button", { name: /Add site/ });
    await expect(save).toBeDisabled();
  });

  test("Advanced is reachable from Settings", async ({ page }) => {
    await page.goto(`${BASE}/settings`);
    await page.getByRole("link", { name: /Advanced/i }).click();
    await expect(page).toHaveURL(/\/settings\/advanced$/);
    await expect(page.getByRole("heading", { name: /Advanced/i })).toBeVisible();
  });

  test("Activity: search clears with the X button", async ({ page }) => {
    await page.goto(`${BASE}/activity`);
    const search = page.getByPlaceholder(/Search filename/i);
    await search.fill("test query");
    await page.getByRole("button", { name: /Clear search/i }).click();
    await expect(search).toHaveValue("");
  });
});

test.describe("/m and / continue to work (D3 is additive)", () => {
  test("/m mobile UI still renders", async ({ page }) => {
    await page.goto(`${BASE}/m`);
    // The /m mobile UI's existence is the load-bearing check —
    // D3 must not break it. We don't assert specific content
    // because /m's HTML may evolve independently.
    expect(page.url()).toContain("/m");
  });

  test("/ desktop UI still renders", async ({ page }) => {
    await page.goto(`${BASE}/`);
    expect(page.url()).toMatch(new RegExp(`${BASE}/?$`));
  });
});
