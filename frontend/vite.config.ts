import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Phase 1 root flip (v3.66.203): Flask serves the built SPA at `/`
// (catch-all SPA fallback in app.py serve_spa_root). The Vite `base`
// option must match so that asset URLs in index.html are emitted as
// /assets/... — served by the same catch-all. This is the *only*
// coupling between the build output and the Flask mount point —
// change one, change the other. (Pre-flip this was "/m2/".)
export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // During `npm run dev`, proxy /api/* to the running Flask app
      // so the dev server can talk to real backend endpoints without
      // CORS. The Flask dev port is 5555 in start_linux.sh.
      "/api": "http://127.0.0.1:5555",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    // Bundle-size revisit (v3.66.208, due at T6): the single index chunk had
    // grown to ~1.19 MB minified. Split the stable vendor mass from the app
    // code so a SPA-code-only release re-downloads only the app chunk.
    // ONE vendor chunk on purpose: a three-way react/charts/vendor split was
    // tried first and CRASHED at runtime ("Cannot access 'R' before
    // initialization" — cross-chunk TDZ from interdependent vendor modules
    // landing in different chunks; tsc/vite/band all green, only the render
    // gate caught it). A single vendor chunk cannot have cross-vendor-chunk
    // init-order problems. Route-level lazy() deferred (hash-rename noise).
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          return id.includes("node_modules") ? "vendor" : undefined;
        },
      },
    },
  },
});
