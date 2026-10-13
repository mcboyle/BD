import { test, expect } from "@playwright/test";

// stb-fix F6 e2e: the built SPA's /budget page loads history for ANY site id -- including ids
// a URL path cannot carry ("..", "/../x": browsers normalize them; %2F is decoded before routing).
// hooks/useBudgetHistory.ts sends GET /api/daily_budget/history?site=<encodeURIComponent(id)>&days=30.
//
// Surface test: every id must round-trip (HTTP 200, exact site_id echoed, no error alert). When the
// harness seeded the scratch DB (BD_E2E_F6_SEEDED=1: bytes 1000+i on BD_E2E_F6_YMD for IDS[i]),
// the page must also show that id's own row -- a wrong-id answer cannot pass.
//
// Run: BD_E2E_BASE=http://127.0.0.1:PORT npx playwright test e2e/f6_budget_history_site_id.spec.ts --project=chromium

const IDS = ["7642222c", "a/b", "..", "/../x", "a//b", "trail/", "x?days=1", "frag#1", "50%", "%2F", "sp ace", "café-サイト"];
const SEEDED = process.env.BD_E2E_F6_SEEDED === "1";
const YMD = process.env.BD_E2E_F6_YMD || "2026-10-13";

for (const [i, sid] of IDS.entries()) {
  test(`budget history for site id ${JSON.stringify(sid)}`, async ({ page }) => {
    await page.goto("/budget");
    await page.getByLabel("site id").fill(sid);
    // Any daily_budget response: a browser-normalized path ("..") no longer contains "history".
    const resp = page.waitForResponse((r) => r.url().includes("/api/daily_budget/"));
    await page.getByRole("button", { name: "Load" }).click();
    const r = await resp;
    expect(r.status(), `F6_E2E_NOT_ROUTED ${JSON.stringify(sid)} ${r.url()}`).toBe(200);
    const j = await r.json();
    expect(j.site_id, `F6_E2E_WRONG_SITE_ID ${JSON.stringify(sid)}`).toBe(sid);
    await expect(page.getByText(/Could not load usage/)).toHaveCount(0);
    if (SEEDED) {
      expect(j.history, `F6_E2E_WRONG_HISTORY ${JSON.stringify(sid)}`).toContainEqual({ ymd: YMD, bytes: 1000 + i });
      await expect(page.getByTestId("budget-table")).toContainText(YMD);
    }
  });
}
