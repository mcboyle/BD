// Adaptive polling. The design narrative locks: when there's active
// work (running > 0, queued > 0), poll fast; when the system is idle,
// back off. This avoids the naive 1s × 6 tabs × 3 endpoints = 18 RPS
// hammering. Each widget reads its own "is anything happening" signal
// from its own query data; this helper centralises the *intervals*
// so they're consistent across widgets.
//
// Returns false to STOP polling (TanStack convention) — used when the
// tab is in the background, since visibilitychange handling is built
// into TanStack Query already.
//
// Usage with TanStack Query:
//   refetchInterval: (query) => adaptiveInterval({
//     query,
//     isBusy: (d: DashboardV2) => d.today.running > 0 || d.attention.length > 0,
//     fast: 2000, slow: 10000,
//   })

// We only need to read `.state.data` off the query argument; we don't
// care about TError or any other type parameters TanStack threads
// through `Query`. Using a structural minimum here decouples the
// helper from TanStack's full Query type — which has TError in a
// contravariant position that makes `Query<T, Error, T>` not assignable
// to `Query<T, unknown, T>`. The old typing (Query<T, unknown, T>)
// caused TS2322 at every call site when stash built the SPA fresh
// against a strict TS config that inferred TError = Error for
// useQuery<T>(...). See v3.64.4 CHANGELOG.
export interface AdaptiveOpts<T> {
  query: { state: { data: T | undefined } };
  isBusy: (data: T) => boolean;
  fast: number;   // ms when busy
  slow: number;   // ms when idle
}

export function adaptiveInterval<T>(opts: AdaptiveOpts<T>): number {
  const data = opts.query.state.data;
  if (!data) return opts.fast; // first load: be eager
  return opts.isBusy(data) ? opts.fast : opts.slow;
}
