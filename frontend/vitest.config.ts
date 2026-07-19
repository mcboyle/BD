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
  },
});
