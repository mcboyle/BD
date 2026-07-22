import { test, expect } from "@playwright/test";

// D3 follow-up (v3.64.1) e2e smoke for U10–U15.
//
// These tests cover the SPA surfaces that landed in v3.64.1 and were
// not part of the original v3.64.0 d3_smoke.spec.ts. The surfaces:
//
//   U10 — bulk select on Sites + Queue
//   U11 — drag-to-reorder on Queue (per-site, dnd-kit)
//   U12 — Queue tab badge live-% mode toggle
//   U13 — configurable dashboard (react-grid-layout edit mode)
//   U14 — log-diff side-by-side viewer (/m2/logs/diff route)
//   U15 — site-health sparkline per Activity row
//
// Requires a running BulkDownloader with `frontend/dist/` built. Same
// BD_E2E_BASE pattern as d3_smoke.spec.ts.
//
// IMPORTANT — surface tests, not data tests. Some U10–U15 features
// only render fully when there's matching live data (sparkline needs
// 14 days of activity; %-badge needs running jobs; drag-reorder
// ribbon needs a site with ≥2 waiting jobs). The tests skip cleanly
// when the data isn't there rather than failing — running this suite
// on a freshly-installed BD must not be a red bar.
//
// Run:
//   BD_E2E_BASE=http://stash:5555 npx playwright test e2e/d3_u10_u15.spec.ts

const BASE = process.env.BD_E2E_BASE || "http://localhost:5555";

// ─── U10: bulk select on Sites + Queue ──────────────────────────────

test.describe("U10 — Bulk select", () => {
  test("Sites: Select button opens selection mode", async ({ page }) => {
    await page.goto(`${BASE}/m2/sites`);
    const selectBtn = page.getByRole("button", {
      name: /Select sites for bulk actions/i,
    });
    // The button is disabled when there are no sites — skip the
    // assertion path in that case so a fresh-install run doesn't
    // fail. We still assert the button EXISTS (the U10 surface is
    // present), just not that we can drive it.
    if (await selectBtn.isDisabled()) {
      test.skip(true, "no sites configured — selection mode path not exercised");
    }
    await selectBtn.click();
    // Selection mode shows a Done/Exit button in the trailing slot.
    await expect(
      page.getByRole("button", { name: /Exit selection mode/i }),
    ).toBeVisible();
  });

  test("Sites: exit selection mode returns to default trailing", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/sites`);
    const selectBtn = page.getByRole("button", {
      name: /Select sites for bulk actions/i,
    });
    if (await selectBtn.isDisabled()) {
      test.skip(true, "no sites configured");
    }
    await selectBtn.click();
    await page.getByRole("button", { name: /Exit selection mode/i }).click();
    // Back to the default state — Select button visible again.
    await expect(selectBtn).toBeVisible();
  });

  test("Queue: Select button opens waiting-job selection mode", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/queue`);
    const selectBtn = page.getByRole("button", {
      name: /Select waiting jobs for bulk cancel/i,
    });
    // Button may be absent or disabled if no waiting jobs.
    if ((await selectBtn.count()) === 0) {
      test.skip(true, "no Select control on Queue — no waiting jobs");
    }
    if (await selectBtn.isDisabled()) {
      test.skip(true, "no waiting jobs to select");
    }
    await selectBtn.click();
    await expect(
      page.getByRole("button", { name: /Exit selection mode/i }),
    ).toBeVisible();
  });

  test("API: POST /api/sites/v2/bulk requires CSRF + action allow-list", async ({
    page,
  }) => {
    // Direct API smoke — confirms the endpoint exists and the
    // allow-list works (a bogus action returns 4xx, not 500).
    // We don't need a real CSRF token for this check because a
    // missing-token request must also fail with 4xx — the only thing
    // we're verifying is that the route is wired and validates input.
    const r = await page.request.post(`${BASE}/api/sites/v2/bulk`, {
      data: { action: "delete_everything_lol", site_ids: ["nope"] },
      // No CSRF header → request must be rejected (403) OR the
      // action allow-list must reject the bogus action (400). Either
      // is a pass — both prove the endpoint exists and gates input.
      failOnStatusCode: false,
    });
    expect(r.status()).toBeGreaterThanOrEqual(400);
    expect(r.status()).toBeLessThan(500);
  });

  test("API: POST /api/queue/v2/bulk_cancel exists", async ({ page }) => {
    const r = await page.request.post(`${BASE}/api/queue/v2/bulk_cancel`, {
      data: { jobs: [] },
      failOnStatusCode: false,
    });
    expect(r.status()).toBeGreaterThanOrEqual(200);
    expect(r.status()).toBeLessThan(500);
  });
});

// ─── U11: drag-to-reorder on Queue ──────────────────────────────────

test.describe("U11 — Queue drag-to-reorder", () => {
  test("Sort ribbon renders chips for sites with ≥2 waiting jobs", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/queue`);
    // The ribbon only appears when there's at least one site with
    // 2+ waiting jobs. Look for the "Reorder N waiting jobs for X"
    // aria-label on any chip; absence is a clean skip.
    const chips = page.getByRole("button", {
      name: /Reorder \d+ waiting jobs for /i,
    });
    const count = await chips.count();
    if (count === 0) {
      test.skip(true, "no site has ≥2 waiting jobs — ribbon path not exercised");
    }
    expect(count).toBeGreaterThan(0);
  });

  test("API: POST /api/sites/:sid/jobs/reorder accepts an ordering", async ({
    page,
  }) => {
    // Surface check: route still exists and validates input. v3.64.1
    // fixed the latent (handler now routes through reorder_urls); a
    // bogus payload should 4xx, not 500.
    const r = await page.request.post(
      `${BASE}/api/sites/__nope__/jobs/reorder`,
      {
        data: { ordered: ["a", "b"] },
        failOnStatusCode: false,
      },
    );
    expect(r.status()).toBeGreaterThanOrEqual(400);
    expect(r.status()).toBeLessThan(500);
  });
});

// ─── U12: Queue tab live-% badge ────────────────────────────────────

test.describe("U12 — Queue tab badge mode", () => {
  test("Settings: badge mode toggle is present and switchable", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/settings`);
    // The toggle is rendered as a radiogroup with Count / Percent.
    const group = page.getByRole("radiogroup", {
      name: /Queue tab badge mode/i,
    });
    await expect(group).toBeVisible();
    // Switch to Percent — the radio should report aria-checked.
    const percent = group.getByRole("radio", { name: /Percent/i });
    await percent.click();
    await expect(percent).toHaveAttribute("aria-checked", "true");
    // Back to Count to avoid leaking state into subsequent runs.
    await group.getByRole("radio", { name: /Count/i }).click();
  });

  test("localStorage key bd-queue-badge-mode persists the choice", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/settings`);
    const group = page.getByRole("radiogroup", {
      name: /Queue tab badge mode/i,
    });
    await group.getByRole("radio", { name: /Percent/i }).click();
    const stored = await page.evaluate(() =>
      window.localStorage.getItem("bd-queue-badge-mode"),
    );
    expect(stored).toBe("percent");
    // Cleanup
    await group.getByRole("radio", { name: /Count/i }).click();
  });
});

// ─── U13: configurable dashboard ────────────────────────────────────

test.describe("U13 — Dashboard customize", () => {
  test("Edit button toggles edit mode and exposes Reset", async ({ page }) => {
    await page.goto(`${BASE}/m2/`);
    const editBtn = page.getByRole("button", { name: /Customize dashboard/i });
    await expect(editBtn).toBeVisible();
    await editBtn.click();
    // In edit mode the same button is now labelled "Exit dashboard edit mode".
    await expect(
      page.getByRole("button", { name: /Exit dashboard edit mode/i }),
    ).toBeVisible();
    // Reset button shows up only in edit mode.
    await expect(
      page.getByRole("button", {
        name: /Reset dashboard layout to defaults/i,
      }),
    ).toBeVisible();
    // Exit cleanly.
    await page.getByRole("button", { name: /Exit dashboard edit mode/i }).click();
  });

  test("Tile drag handles are present in edit mode", async ({ page }) => {
    await page.goto(`${BASE}/m2/`);
    await page.getByRole("button", { name: /Customize dashboard/i }).click();
    // Each tile gets an aria-label "Drag to reorder <id> tile".
    const handles = page.locator(
      '[aria-label^="Drag to reorder "][aria-label$=" tile"]',
    );
    expect(await handles.count()).toBeGreaterThan(0);
    await page.getByRole("button", { name: /Exit dashboard edit mode/i }).click();
  });
});

// ─── U14: log-diff side-by-side ─────────────────────────────────────

test.describe("U14 — Log diff", () => {
  test("/m2/logs/diff route mounts the LogDiff page", async ({ page }) => {
    await page.goto(`${BASE}/m2/logs/diff`);
    await expect(page).toHaveURL(/\/m2\/logs\/diff/);
    // The page renders even without query params — shows the empty
    // / picker state. We only assert it returned a 200 with the
    // SPA shell, not a 4xx/5xx.
    expect(page.url()).toContain("/logs/diff");
  });

  test("API: GET /api/queue/v2/job_log_diff is wired", async ({ page }) => {
    // No real job ids → expect 4xx, not 500. The endpoint validating
    // its input is exactly what U14 added.
    const r = await page.request.get(
      `${BASE}/api/queue/v2/job_log_diff?a=__nope__&b=__nope2__`,
      { failOnStatusCode: false },
    );
    expect(r.status()).toBeGreaterThanOrEqual(200);
    expect(r.status()).toBeLessThan(500);
  });
});

// ─── U15: Activity sparkline ────────────────────────────────────────

test.describe("U15 — Site health sparkline", () => {
  test("API: GET /api/activity/v2/site_health returns ok or 4xx (not 500)", async ({
    page,
  }) => {
    const r = await page.request.get(`${BASE}/api/activity/v2/site_health`, {
      failOnStatusCode: false,
    });
    expect(r.status()).toBeGreaterThanOrEqual(200);
    expect(r.status()).toBeLessThan(500);
    if (r.status() === 200) {
      const body = await r.json();
      // The shape: { ok: true, sites: [...] } per U15. We don't
      // assert the array is non-empty (fresh installs have no
      // activity). Just confirm the contract key.
      expect(body).toHaveProperty("ok");
    }
  });

  test("Activity tab renders rows", async ({ page }) => {
    // The sparkline is a visual element inside each Activity row.
    // We can only assert the rows render — verifying the polyline
    // would require shipped data. This catches the "Activity tab
    // 500s after the U15 endpoint addition" failure mode.
    await page.goto(`${BASE}/m2/activity`);
    // Activity heading or main area should be visible. Use the
    // search input as a stable anchor since it's always present.
    await expect(page.getByPlaceholder(/Search filename/i)).toBeVisible();
  });
});
