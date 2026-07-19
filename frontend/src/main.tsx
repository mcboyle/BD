import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";

import App from "./App";
import "./index.css";
import { applyStoredThemeOnBoot } from "@/hooks/useTheme";
import { shouldGlobalErrorToast, mutationErrorMessage } from "@/lib/mutationErrors";

// Apply persisted theme BEFORE first render — otherwise a user who
// chose dark mode last session sees a flash of light content while
// React boots. The boot helper reads localStorage["bd-theme"] and
// toggles the `dark` class on <html> synchronously.
applyStoredThemeOnBoot();

// T9b (v3.66.213) — register the EXISTING root-scope /sw.js so the SPA push
// surface (hooks/usePush.ts) has navigator.serviceWorker.ready + pushManager
// available. We deliberately reuse the SAME root /sw.js the legacy UI
// registered (served at scope "/" with Service-Worker-Allowed: /): the browser
// treats this as an UPDATE of the existing scope-"/" registration, NOT a new
// SW, so any existing PushSubscription — and its server push_subscriptions row
// keyed by endpoint — is preserved across the legacy → SPA cutover. Failure is
// non-fatal: the push surface degrades gracefully (hides the enable control).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* push surface degrades gracefully if registration fails */
    });
  });
}

// TanStack Query — the standout reason to pick React per the design
// narrative. Defaults: adaptive polling is configured per-query via
// refetchInterval in each route hook, NOT here. Setting a global
// refetchInterval would clobber the per-widget adaptive shape.
//
// staleTime defaults to 0 (queries are immediately considered stale,
// re-fetch on every mount). That's the right default for a live ops
// tool where /api/sites changes mean a user took an action and wants
// the new state visible.
const queryClient = new QueryClient({
  // P6-2 — global write-failure safety net. Fires a toast for any mutation
  // error UNLESS the mutation registered its own onError (so the route
  // mutations that already toast.error keep their bespoke message; the silent
  // invalidate-only hook mutations no longer fail quietly).
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (!shouldGlobalErrorToast(Boolean(mutation.options.onError))) return;
      toast.error(mutationErrorMessage(error));
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
      staleTime: 0,
    },
    mutations: {
      retry: 0,
    },
  },
});

// Phase 1 root flip (v3.66.203): React Router basename "/" — the SPA
// owns the site root, so <Link to="/queue"> emits /queue. Matches
// Vite's base option in vite.config.ts. Change one, change the other.
// (Pre-flip the SPA was mounted at /m2; /m2 now 302-redirects here.)
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/">
        <App />
        <Toaster
          position="top-center"
          richColors
          closeButton
          // Toasts persist across route changes by default in sonner.
          // Tier 1 #3 (toast notifications) — wired at the root so
          // any descendant can call toast.success(...) etc.
        />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
