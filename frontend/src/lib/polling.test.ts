import { describe, it, expect } from "vitest";
import { adaptiveInterval } from "./polling";

// Behavioral coverage for adaptive polling. Replaces the legacy
// widgets.js `setInterval` source-grep (test_v3_62_2_guards.py), which
// only proved the *string* "setInterval" existed. This proves the
// actual contract: eager on first load, fast while busy, slow when idle.
describe("adaptiveInterval", () => {
  const opts = (data: { running: number } | undefined) => ({
    query: { state: { data } },
    isBusy: (d: { running: number }) => d.running > 0,
    fast: 2000,
    slow: 10000,
  });

  it("polls fast on first load (data not yet available)", () => {
    expect(adaptiveInterval(opts(undefined))).toBe(2000);
  });

  it("polls fast while work is active", () => {
    expect(adaptiveInterval(opts({ running: 3 }))).toBe(2000);
  });

  it("backs off to slow when idle", () => {
    expect(adaptiveInterval(opts({ running: 0 }))).toBe(10000);
  });
});
