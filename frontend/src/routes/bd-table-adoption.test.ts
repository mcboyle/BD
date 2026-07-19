import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Cut 9.1 — sticky-header consolidation: every hand-rolled route <table> adopts
// the shared bd-table class (no per-table header treatment drift).
const FILES = [
  "Secrets", "Budget", "PoolsMacros", "Schedules", "Maintenance",
  "SitePayloadActions", "Dedup", "Cluster", "Settings", "Vpn",
  "History", "AlertRules", "BatchOps", "ImportsCenter",
];

describe("bd-table adoption on route tables (Cut 9.1)", () => {
  for (const f of FILES) {
    it(`${f}: every <table> carries bd-table`, () => {
      const src = readFileSync(join(process.cwd(), `src/routes/${f}.tsx`), "utf8");
      const tags = src.match(/<table\b[^>]*>/g) || [];
      expect(tags.length).toBeGreaterThan(0);
      for (const tag of tags) expect(tag).toMatch(/\bbd-table\b/);
    });
  }
});
