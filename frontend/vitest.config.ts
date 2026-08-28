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
    // Row 339, test5 loaded complete census: 329 suites / 617 cases passed while
    // the 48-core load average rose 5.95 -> 26.18.  History.confirm's allowlist
    // case was the worst at 8840.502ms; round that measurement up first, then
    // ceil(8841ms * 1.5) = 13262ms (50% headroom).  Two exact-base replays at
    // load 2.25 -> 10.95 and 7.09 -> 18.73 peaked at 7203.287ms, so the heavier
    // retained census governs.  13262ms remains below the 240000ms pytest item.
    testTimeout: 13_262,
  },
});
