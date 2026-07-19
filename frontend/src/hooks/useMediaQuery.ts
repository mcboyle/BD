import { useState, useEffect } from "react";

// Reactive media query hook. Returns true if the query matches.
// SSR-safe: returns `false` until first effect.
//
// v3.64.x — added so AppShell can branch on viewport width to render
// the desktop shell at lg breakpoint without making mobile users pay
// for the desktop chrome.

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    // Sync once in case the initial state diverged.
    setMatches(mq.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);

  return matches;
}
