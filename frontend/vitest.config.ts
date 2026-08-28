/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

// Vitest harness (v3.66.x). run_tests.py (the Python suite) can't exercise
// React behavior — no DOM — so component/behavior contracts used to be
// pinned indirectly by grepping legacy static/*.js source strings. Those
// greps die with the legacy shell (Phase 4). This config gives jsdom +
// Testing Library so the real behaviors run as tests, gated pre-deploy in
// bd-cut (where node_modules exists). NOTE: on-stash capture.sh does NOT
// run vitest — the deploy zip excludes node_modules — this is a
// sandbox-cut gate only.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    globals: true,
    css: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Row 329, test5 loaded measurements (ms): 12 parallel T3/T4 runs put
    // Library at 2294.762-3137.827 and Maintenance at 1537.648-2248.980;
    // five same-CPU runs put them at 4554.031-4815.732 and
    // 3001.935-3355.211.  Two loaded 617-case censuses found the unbounded
    // T5 Maintenance case at 4857.209 and 5216.919, so this is config-wide.
    // The worst completed same-load replay was Maintenance at 7169ms with four
    // same-CPU load workers: ceil(7169 * 1.5) = 10754ms (50% headroom).
    testTimeout: 10_754,
  },
});
