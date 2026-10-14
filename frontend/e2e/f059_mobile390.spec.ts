import { test, expect, type Page } from "@playwright/test";

// F059 Mobile390: on a 390px-wide phone the five key pages must not scroll
// sideways, and the mobile "More" menu must open and close.
// Read-only against BD_E2E_BASE. The one route override appends a long site
// name to the /api/search/sites_available answer the page fetched itself, so
// the /queue site <select> is measured at a worst-case option width whatever
// sites the backend holds.
//
// Run: BD_E2E_BASE=http://127.0.0.1:PORT npx playwright test e2e/f059_mobile390.spec.ts

const BASE = process.env.BD_E2E_BASE || "http://localhost:5555";
const LONG_SITE =
  "An Exceptionally Long Site Name Used To Check Mobile Layout Width (F059)";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

// Each page is measured once its own content has rendered.
const PAGES: { path: string; ready: (p: Page) => ReturnType<Page["locator"]> }[] = [
  { path: "/", ready: (p) => p.getByRole("navigation", { name: "Primary" }) },
  { path: "/queue", ready: (p) => p.locator("select#site-search-site") },
  { path: "/history", ready: (p) => p.getByRole("button", { name: "Compact database" }) },
  { path: "/sites", ready: (p) => p.getByRole("navigation", { name: "Primary" }) },
  { path: "/settings", ready: (p) => p.getByRole("navigation", { name: "Primary" }) },
];

async function withLongSiteName(page: Page) {
  await page.route("**/api/search/sites_available*", async (route) => {
    const res = await route.fetch();
    const body = await res.json().catch(() => ({}));
    const available = Array.isArray(body.available) ? [...body.available] : [];
    available.push({ site_id: "f059-long-name", name: LONG_SITE });
    await route.fulfill({ response: res, json: { ...body, available } });
  });
}

// Page width versus viewport, plus the first elements whose right edge passes it.
async function measure(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const vw = doc.clientWidth;
    const offenders = Array.from(document.querySelectorAll("body *"))
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && r.right > vw + 0.5)
      .slice(0, 6)
      .map(({ el, r }) => `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}@${r.right.toFixed(1)}`);
    return { scrollWidth: doc.scrollWidth, clientWidth: vw, offenders };
  });
}

for (const { path, ready } of PAGES) {
  test(`F059 ${path}: no horizontal scroll at 390px`, async ({ page }) => {
    await withLongSiteName(page);
    const nav = await page.goto(`${BASE}${path}`);
    expect(nav?.status(), `GET ${path}`).toBe(200);
    await expect(ready(page)).toBeVisible({ timeout: 15_000 });
    if (path === "/queue") {
      // positive control: the long option reached the select being measured
      await expect(page.locator("select#site-search-site option", { hasText: LONG_SITE })).toHaveCount(1);
    }
    const m = await measure(page);
    expect(m.clientWidth).toBe(390);
    expect(
      m.scrollWidth,
      `${path} scrollWidth ${m.scrollWidth} > ${m.clientWidth}; past the edge: ${m.offenders.join(", ")}`,
    ).toBeLessThanOrEqual(m.clientWidth);
  });
}

test("F059 /: no horizontal scroll while the dashboard data arrives late", async ({ page }) => {
  // Widest the page ever got: checked on every DOM mutation (before any resize observer runs) and every frame;
  // also whether a dashboard grid tile was ever in the DOM (positive control: the grid path was exercised).
  await page.addInitScript(() => {
    const w = window as unknown as { __f059Max: number; __f059Grid: boolean };
    w.__f059Max = 0;
    w.__f059Grid = false;
    const note = () => {
      const sw = document.documentElement?.scrollWidth ?? 0;
      if (sw > w.__f059Max) w.__f059Max = sw;
      if (document.querySelector(".react-grid-item")) w.__f059Grid = true;
    };
    new MutationObserver(note).observe(document, { subtree: true, childList: true, attributes: true });
    const frame = () => {
      note();
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
  // API answers arrive 1.5 s after the shell paints, as on a busy box.
  await page.route("**/api/**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  // One history row: the instance is not first-run, so Home keeps the dashboard grid instead of onboarding.
  await page.route(
    (url) => url.pathname === "/api/history" && url.searchParams.get("limit") === "1",
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({ json: [{ id: "f059-history-probe" }] });
    },
  );
  await page.goto(`${BASE}/`);
  const tile = page.locator(".react-grid-item").first();
  await expect(tile).toBeVisible({ timeout: 20_000 });
  await expect.poll(async () => (await tile.boundingBox())?.width ?? 9999).toBeLessThanOrEqual(390);
  const { __f059Max: widest, __f059Grid: grid } = await page.evaluate(() => {
    const w = window as unknown as { __f059Max: number; __f059Grid: boolean };
    return { __f059Max: w.__f059Max, __f059Grid: w.__f059Grid };
  });
  expect(grid, "a dashboard grid tile rendered").toBe(true);
  expect(widest, "widest scrollWidth seen while the dashboard loaded").toBeLessThanOrEqual(390);
});

test("F059 /queue: the site select stays inside the viewport", async ({ page }) => {
  await withLongSiteName(page);
  await page.goto(`${BASE}/queue`);
  const select = page.locator("select#site-search-site");
  await expect(select.locator("option", { hasText: LONG_SITE })).toHaveCount(1);
  const box = await select.boundingBox();
  expect(box, "select#site-search-site has a box").not.toBeNull();
  expect(box!.x + box!.width, "select#site-search-site right edge").toBeLessThanOrEqual(390);
});

test("F059 /history: tabs and Compact database stay inside the viewport", async ({ page }) => {
  await page.goto(`${BASE}/history`);
  const compact = page.getByRole("button", { name: "Compact database" });
  await expect(compact).toBeVisible({ timeout: 15_000 });
  const list = await page.getByRole("tablist").boundingBox();
  for (const name of ["History & Search", "Events", "Logs", "Saved searches"]) {
    const tab = page.getByRole("tab", { name });
    await expect(tab).toBeVisible();
    const box = await tab.boundingBox();
    expect(box!.x + box!.width, `tab "${name}" right edge`).toBeLessThanOrEqual(390);
    // a wrapped row must stay inside the list, not spill over the panel below it
    expect(box!.y + box!.height, `tab "${name}" bottom inside the tab list`).toBeLessThanOrEqual(list!.y + list!.height + 0.5);
  }
  const cb = await compact.boundingBox();
  expect(cb!.x + cb!.width, "Compact database right edge").toBeLessThanOrEqual(390);
});

test("F059 mobile nav: More opens the menu and Escape closes it", async ({ page }) => {
  await page.goto(`${BASE}/`);
  const menu = page.getByRole("dialog", { name: "More navigation" });
  await expect(menu).toHaveCount(0);
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "More" }).click();
  await expect(menu).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
});

// Folded from F059-fix (ruling O3154): the measurement must be able to say "too wide".
test("F059 control: the measurement reports an element wider than the screen", async ({ page }) => {
  await page.goto(`${BASE}/history`);
  await expect(page.getByRole("button", { name: "Compact database" })).toBeVisible({ timeout: 15_000 });
  await page.evaluate(() => {
    const d = document.createElement("div");
    d.style.width = "600px";
    d.style.height = "1px";
    document.body.appendChild(d);
  });
  const m = await measure(page);
  expect(m.scrollWidth, "control: a 600px element makes the page wider than the screen").toBeGreaterThan(m.clientWidth);
});
