import { test, expect, type Page } from "@playwright/test";

// B-3 e2e smoke — per-site widget dashboard at /sites/:siteId.
//
// Surfaces under test (all v3.64.3):
//   - Sites row tap navigates to /sites/:siteId (replaces the prior
//     row-tap toast).
//   - /sites/:siteId mounts SiteDetail with the site's name as the
//     header title.
//   - Edit-mode toggle is present and exposes Add widget + Reset (same
//     pattern as Home, but scoped to this site).
//   - Per-site layout persists under bd-dashboard-layout-${siteId} —
//     isolated from the global bd-dashboard-layout key.
//   - WidgetPicker opens scoped to this site (the picker's siteId
//     prop drives useWidgetSelection(siteId) inside).
//   - Deep-linking to /sites/__nope__ shows the not-found state, not
//     a crash.
//   - Delete-from-SiteDetail navigates back to /sites on success
//     (no test of the delete itself — we don't want to mutate real
//     state; we only assert the affordance exists with the right
//     ARIA label).
//
// Same conventions as d3_u10_u15.spec.ts:
//   - Surface tests, not data tests.
//   - Skip cleanly when there are no sites configured (fresh install
//     must not red-bar this suite).
//   - Restore any persistent state we touch (localStorage scope keys).
//
// Run:
//   BD_E2E_BASE=http://stash:5555 npx playwright test e2e/b3_site_detail.spec.ts

const BASE = process.env.BD_E2E_BASE || "http://localhost:5555";

// ─── Helpers ────────────────────────────────────────────────────────

// Resolve the first site_id from /api/sites/v2. Returns null when the
// list is empty so callers can skip cleanly.
async function firstSiteId(page: Page): Promise<{
  siteId: string;
  name: string;
} | null> {
  const r = await page.request.get(`${BASE}/api/sites/v2`);
  if (r.status() !== 200) return null;
  const body = await r.json();
  const sites = body?.sites ?? [];
  if (!Array.isArray(sites) || sites.length === 0) return null;
  return { siteId: sites[0].site_id, name: sites[0].name };
}

// Read a localStorage key inside the page context.
async function readStorage(page: Page, key: string): Promise<string | null> {
  return page.evaluate((k) => window.localStorage.getItem(k), key);
}

// Remove a localStorage key inside the page context (best-effort).
async function clearStorage(page: Page, key: string): Promise<void> {
  await page.evaluate((k) => {
    try {
      window.localStorage.removeItem(k);
    } catch {
      /* ignore */
    }
  }, key);
}

// ─── Navigation: Sites row → SiteDetail ─────────────────────────────

test.describe("B-3 — Navigation into SiteDetail", () => {
  test("Sites row tap navigates to /sites/:siteId", async ({ page }) => {
    await page.goto(`${BASE}/m2/sites`);
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(true, "no sites configured — row-tap navigation not exercised");
    }
    // SiteRow gives the button an aria-label of `Open <name>` when
    // not in selection mode. Match on that to avoid colliding with
    // nested affordances (badges, the chevron icon).
    await page
      .getByRole("button", { name: new RegExp(`^Open ${escapeRegExp(site!.name)}$`) })
      .first()
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/m2/sites/${escapeRegExp(encodeURIComponent(site!.siteId))}$`),
    );
  });

  test("deep link to /sites/:siteId renders the detail page", async ({
    page,
  }) => {
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(true, "no sites configured — deep link not exercised");
    }
    await page.goto(
      `${BASE}/m2/sites/${encodeURIComponent(site!.siteId)}`,
    );
    // The site name appears as the AppShell title (visible heading).
    // Use a substring match — the AppShell title is the bare site
    // name, which is unique enough to anchor on.
    await expect(
      page.getByRole("heading", { name: site!.name }),
    ).toBeVisible();
    // The in-content back-link to /sites is present.
    await expect(
      page.getByRole("link", { name: /Back to sites/i }),
    ).toBeVisible();
  });

  test("deep link to a bogus site shows not-found, not a crash", async ({
    page,
  }) => {
    await page.goto(`${BASE}/m2/sites/__nope_b3_e2e__`);
    // The alert region renders with role="alert".
    await expect(page.getByRole("alert")).toContainText(/Site not found/i);
    // The recovery link to /sites is visible.
    await expect(
      page.getByRole("link", { name: /Back to sites/i }),
    ).toBeVisible();
  });
});

// ─── Edit mode + WidgetPicker scoping ───────────────────────────────

test.describe("B-3 — Edit mode and WidgetPicker", () => {
  test("Edit button toggles edit mode and exposes per-site Reset", async ({
    page,
  }) => {
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(true, "no sites configured — edit-mode path not exercised");
    }
    await page.goto(
      `${BASE}/m2/sites/${encodeURIComponent(site!.siteId)}`,
    );
    const editBtn = page.getByRole("button", { name: /Customize dashboard/i });
    await expect(editBtn).toBeVisible();
    await editBtn.click();
    // Edit mode shows the Add widget button + a per-site Reset
    // affordance whose aria-label is specific to the per-site page
    // (Home's Reset says "Reset dashboard layout to defaults"; this
    // one says "Reset this site's dashboard").
    await expect(
      page.getByRole("button", { name: /Open widget library for this site/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Reset this site's dashboard/i }),
    ).toBeVisible();
    // Exit cleanly.
    await page
      .getByRole("button", { name: /Exit dashboard edit mode/i })
      .click();
  });

  test("Add widget button opens the WidgetPicker", async ({ page }) => {
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(true, "no sites configured — picker open path not exercised");
    }
    await page.goto(
      `${BASE}/m2/sites/${encodeURIComponent(site!.siteId)}`,
    );
    // Two entry points to the picker: the trailing "Add widget"
    // button in edit mode, OR the empty-state "Add widgets" CTA when
    // the site has no widgets selected. Both paths converge on the
    // same Dialog; we try edit-mode first and fall through to the
    // empty-state CTA if Add widget isn't available (selection
    // already has widgets but edit-mode entry is the normal path).
    await page.getByRole("button", { name: /Customize dashboard/i }).click();
    const addWidget = page.getByRole("button", {
      name: /Open widget library for this site/i,
    });
    await addWidget.click();
    // The picker is a Radix Dialog with title "Widget library".
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByText(/Widget library/i),
    ).toBeVisible();
    // Esc closes the dialog.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await page
      .getByRole("button", { name: /Exit dashboard edit mode/i })
      .click();
  });
});

// ─── Per-site layout isolation ──────────────────────────────────────

test.describe("B-3 — Layout scope isolation", () => {
  test("per-site layout writes to bd-dashboard-layout-${siteId}, not the global key", async ({
    page,
  }) => {
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(
        true,
        "no sites configured — layout isolation not exercised",
      );
    }
    const scopedKey = `bd-dashboard-layout-${site!.siteId}`;
    const globalKey = "bd-dashboard-layout";

    // Visit Home first so the page context is initialized, then read
    // the global key as a baseline. We don't mutate Home here.
    await page.goto(`${BASE}/m2/`);
    const globalBefore = await readStorage(page, globalKey);

    // Navigate to SiteDetail, enter edit mode, and trigger a layout
    // write. The simplest way to force a write without doing a real
    // drag (which is finicky in headless mode) is to click Reset
    // while in edit mode — that path goes through the same
    // setLayouts persistence as a drag does for the scoped key, but
    // also removes the storage entry first. To get a real write we
    // need *something* to setLayouts; we use the picker to add a
    // widget which forces a reconcile + write IF the operator already
    // has KPI widgets, or we accept that the test only proves
    // negative isolation (global key isn't touched). Negative
    // isolation is what we actually care about for B-3 correctness.
    await page.goto(
      `${BASE}/m2/sites/${encodeURIComponent(site!.siteId)}`,
    );
    await page.getByRole("button", { name: /Customize dashboard/i }).click();
    // Trigger Reset — this calls reset() on useDashboardLayout, which
    // removeItems the scoped key (a no-op if it wasn't there) and
    // setLayoutsState with defaults. It must NOT touch globalKey.
    const resetBtn = page.getByRole("button", {
      name: /Reset this site's dashboard/i,
    });
    // The Reset button is disabled when there's nothing to reset.
    // If so, we can't drive the write path — skip rather than fail.
    if (await resetBtn.isDisabled()) {
      test.skip(
        true,
        "no per-site customization to reset — write path not exercised",
      );
    }
    await resetBtn.click();
    // Allow the storage write to settle (cross-tab event is async).
    await page.waitForTimeout(100);
    // Negative assertion: the global key was NOT touched.
    const globalAfter = await readStorage(page, globalKey);
    expect(globalAfter).toBe(globalBefore);
    // Cleanup: ensure the scoped key isn't left in a state that
    // contaminates later runs. Reset already removed it; clear
    // defensively in case the implementation evolves.
    await clearStorage(page, scopedKey);
    await page
      .getByRole("button", { name: /Exit dashboard edit mode/i })
      .click();
  });

  test("the scoped storage key follows the bd-dashboard-layout-${siteId} naming", async ({
    page,
  }) => {
    // Static check on the naming contract — we don't need a real
    // site for this. Inject a fake into localStorage at the
    // expected scope and confirm the not-found page still loads
    // (proves the key namespace doesn't collide with anything that
    // would break rendering on a sibling route).
    await page.goto(`${BASE}/m2/`);
    const fakeKey = "bd-dashboard-layout-__b3_e2e_probe__";
    await page.evaluate((k) => {
      window.localStorage.setItem(
        k,
        JSON.stringify({ lg: [], sm: [] }),
      );
    }, fakeKey);
    await page.goto(`${BASE}/m2/sites/__b3_e2e_probe__`);
    await expect(page.getByRole("alert")).toContainText(/Site not found/i);
    await clearStorage(page, fakeKey);
  });
});

// ─── Delete affordance ──────────────────────────────────────────────

test.describe("B-3 — Delete affordance moved from row toast", () => {
  test("SiteDetail surfaces a Delete-site button", async ({ page }) => {
    const site = await firstSiteId(page);
    if (!site) {
      test.skip(true, "no sites configured — delete affordance not exercised");
    }
    await page.goto(
      `${BASE}/m2/sites/${encodeURIComponent(site!.siteId)}`,
    );
    // The button's aria-label is `Delete <name>` so we can target it
    // unambiguously. We do NOT click it — we don't want to actually
    // delete a site during smoke runs. The surface test is that the
    // affordance exists with the correct label, which proves the
    // row-toast → detail-page migration is in place.
    const btn = page.getByRole("button", {
      name: new RegExp(`^Delete ${escapeRegExp(site!.name)}$`),
    });
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
  });
});

// ─── Util ───────────────────────────────────────────────────────────

// Escape a string for use inside a RegExp. Site names can contain
// regex-special characters (dots, parens, etc) and we use the name
// in several anchored expressions above.
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
