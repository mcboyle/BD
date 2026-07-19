import { useSyncExternalStore } from "react";

// P6-1 (data display) — a shared, app-wide density preference (comfortable |
// compact) for the list-heavy pages (Queue / History / Library / Activity).
//
// Implemented as a tiny module store read through useSyncExternalStore so that
// the DensityToggle control and every list row on a page re-render together
// from one source of truth — two independent useState hooks would NOT share
// state. Backed by localStorage["bd-density"] and synced across tabs via the
// native "storage" event; same-tab updates fan out via a custom event.

export type Density = "comfortable" | "compact";

const KEY = "bd-density";
const EVENT = "bd-density-change";

function read(): Density {
  if (typeof window === "undefined") return "comfortable";
  try {
    return window.localStorage.getItem(KEY) === "compact"
      ? "compact"
      : "comfortable";
  } catch {
    return "comfortable";
  }
}

function subscribe(cb: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

/** Set the app-wide density. Persists + notifies every useDensity() consumer. */
export function setDensity(d: Density): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, d);
  } catch {
    /* localStorage blocked — still fire the event so in-memory consumers sync */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function useDensity(): {
  density: Density;
  isCompact: boolean;
  setDensity: (d: Density) => void;
} {
  // getServerSnapshot returns the SSR/initial-default so the store is stable
  // before hydration (jsdom + the real client both start comfortable).
  const density = useSyncExternalStore(
    subscribe,
    read,
    () => "comfortable" as Density,
  );
  return { density, isCompact: density === "compact", setDensity };
}
