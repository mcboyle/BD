import { describe, it, expect } from "vitest";
import { formatRate, formatCount, formatPercent, formatTimestamp } from "./format";

// Behavioral coverage for the SPA's throughput/count formatters. Carries
// forward the legacy app.js dashboard-widget source-greps that were
// retired in the P4-A app.js tranche (test_v3_43_38_dashboard_widgets.py:
// `fmtRate`/`renderHealthBar`), which only proved the helper *names*
// existed in app.js. These prove the actual SPA contract that those
// widgets now render through (lib/format.ts).
describe("formatRate", () => {
  it("renders an em-dash for non-positive or non-finite rates", () => {
    expect(formatRate(0)).toBe("\u2014");
    expect(formatRate(-5)).toBe("\u2014");
    expect(formatRate(Number.NaN)).toBe("\u2014");
    expect(formatRate(Number.POSITIVE_INFINITY)).toBe("\u2014");
  });

  it("appends a /s suffix and scales the unit for positive rates", () => {
    expect(formatRate(512)).toBe("512.0 B/s");
    expect(formatRate(2048)).toBe("2.0 KB/s");
    expect(formatRate(5 * 1024 * 1024)).toBe("5.0 MB/s");
  });
});

describe("formatCount", () => {
  it("passes small integers through unchanged", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(42)).toBe("42");
    expect(formatCount(999)).toBe("999");
  });

  it("abbreviates thousands and millions", () => {
    expect(formatCount(1500)).toBe("1.5k");
    expect(formatCount(2_000_000)).toBe("2.0M");
  });

  it("renders an em-dash for non-finite counts", () => {
    expect(formatCount(Number.NaN)).toBe("\u2014");
  });
});

// ── P6-5 formatter-sweep additions (RED-first) ───────────────────────
// These two exports do not exist yet; they centralize formatting logic
// currently re-implemented inline across the SPA, found by the P6-5 grep:
//   formatPercent — NowRunningList:71, RunningJobRow:49 (toFixed(0)%),
//     Advanced:134 (toFixed(1)% free), widgetCatalog pct() (5 specs),
//     BottomTabBar queueRunningPct%.
//   formatTimestamp — the bug-prone "epoch in s-or-ms" normalize
//     (ts > 1e12 ? ts : ts*1000) duplicated in JobErrorModal:343 / LogDiff:252
//     (time style) and ApiTokensPanel:211 / Secrets:645 / Maintenance:691
//     (datetime style via toLocaleString).
describe("formatPercent", () => {
  it("renders an em-dash for null/undefined/non-finite", () => {
    expect(formatPercent(null)).toBe("\u2014");
    expect(formatPercent(undefined)).toBe("\u2014");
    expect(formatPercent(Number.NaN)).toBe("\u2014");
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe("\u2014");
  });

  it("rounds to 0 decimals by default and appends %", () => {
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(42.4)).toBe("42%");
    expect(formatPercent(99.6)).toBe("100%");
  });

  it("honours an explicit decimals argument", () => {
    expect(formatPercent(33.333, 1)).toBe("33.3%");
    expect(formatPercent(100, 1)).toBe("100.0%");
  });
});

describe("formatTimestamp", () => {
  it("renders an em-dash for falsy/non-finite epochs", () => {
    expect(formatTimestamp(0)).toBe("\u2014");
    expect(formatTimestamp(null)).toBe("\u2014");
    expect(formatTimestamp(undefined)).toBe("\u2014");
    expect(formatTimestamp(Number.NaN)).toBe("\u2014");
  });

  it("normalizes a seconds epoch and a millis epoch to the same instant", () => {
    const sec = 1_700_000_000; // seconds
    const ms = 1_700_000_000_000; // same instant in millis
    expect(formatTimestamp(sec)).toBe(formatTimestamp(ms));
  });

  it("time style renders zero-padded HH:MM:SS", () => {
    // 1970-01-01T00:00:05Z + tz; assert shape, not tz-specific value
    const out = formatTimestamp(5, { style: "time" });
    expect(out).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it("datetime style is the default and differs from time style", () => {
    const t = 1_700_000_000;
    expect(formatTimestamp(t)).toBe(formatTimestamp(t, { style: "datetime" }));
    expect(formatTimestamp(t, { style: "datetime" })).not.toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
