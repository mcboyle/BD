import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { routeRisk } from "./routeRisk";

// Cut A (Cut 1 adoption remainder) — the GatedWriteBanner tier must be driven by
// the `routeRisk` source-of-truth (banner `shape={routeRisk(path).bannerShape}`),
// not by a hand-set `level` literal, so banner tiering can't drift per page.
//
// This is a structural/source pin (mirrors the codebase's grep-style guards):
// the banner-bearing routes that routeRisk classifies as `chip` must reference
// `routeRisk` rather than hardcode their tier. The runtime contract that
// `shape` overrides `level` is covered by GatedWriteBanner.test.tsx.

const here = dirname(fileURLToPath(import.meta.url));
const routesDir = join(here, "..", "routes");

// Routes that render a GatedWriteBanner AND are classified `chip` by routeRisk.
// These previously hardcoded level="chip"; Cut A re-sources them from routeRisk.
const CHIP_BANNER_ROUTES: Array<{ file: string; path: string }> = [
  { file: "Integrations.tsx", path: "/integrations" },
  { file: "History.tsx", path: "/history" },
];

describe("banner tier is routeRisk-driven (Cut A)", () => {
  it("routeRisk classifies the chip-tier routes as chip (sanity)", () => {
    for (const { path } of CHIP_BANNER_ROUTES) {
      expect(routeRisk(path).bannerShape).toBe("chip");
    }
  });

  it("each chip-banner route sources its banner tier from routeRisk", () => {
    const offenders: string[] = [];
    for (const { file } of CHIP_BANNER_ROUTES) {
      const fp = join(routesDir, file);
      if (!existsSync(fp)) {
        offenders.push(`${file} (missing)`);
        continue;
      }
      const src = readFileSync(fp, "utf8");
      if (!/routeRisk/.test(src)) offenders.push(file);
    }
    expect(offenders).toEqual([]);
  });

  it("chip-banner routes no longer hardcode a level=\"chip\" literal", () => {
    const offenders: string[] = [];
    for (const { file } of CHIP_BANNER_ROUTES) {
      const fp = join(routesDir, file);
      if (!existsSync(fp)) continue;
      const src = readFileSync(fp, "utf8");
      if (/level=["']chip["']/.test(src)) offenders.push(file);
    }
    expect(offenders).toEqual([]);
  });
});
