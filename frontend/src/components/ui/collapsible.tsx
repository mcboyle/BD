import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

// v3.66.326 — a tiny, dependency-free disclosure. Radix isn't installed and
// the sandbox has no network, so this is a plain useState + conditional
// render. Used for the desktop nav groups and the collapsible settings
// categories (global Settings + per-site SiteSettings). Callers style the
// header via headerClassName so the same primitive serves a card-style
// settings section and an uppercase nav-group label.
//
// Slice 4a — three additions for the desktop nav groups:
//   • persistKey  — remember open/closed across navigations in localStorage
//                   ("bd-collapsible:<key>" = "1"/"0"), seeded by defaultOpen.
//   • forceOpen   — render open regardless of stored/toggled state (the group
//                   containing the active route is always surfaced).
//   • active      — caller-supplied flag; stamps data-active + a tint class on
//                   the header so you can see which group your page is in.
//
// Streaming note (artifacts) does not apply — this is the real SPA, so a
// closed section simply isn't mounted until opened.

const PERSIST_PREFIX = "bd-collapsible:";

function readPersisted(key: string | undefined, def: boolean): boolean {
  if (!key || typeof window === "undefined") return def;
  try {
    const v = window.localStorage.getItem(PERSIST_PREFIX + key);
    if (v == null) return def;
    return v === "1";
  } catch {
    return def;
  }
}

function writePersisted(key: string | undefined, open: boolean): void {
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PERSIST_PREFIX + key, open ? "1" : "0");
  } catch {
    /* localStorage blocked */
  }
}

export interface CollapsibleProps {
  title: ReactNode;
  /** Open on first render. Default closed. */
  defaultOpen?: boolean;
  /** Persist open/closed under localStorage["bd-collapsible:<persistKey>"]. */
  persistKey?: string;
  /** Render open regardless of stored/toggled state (active-route group). */
  forceOpen?: boolean;
  /** Mark the header active (data-active + tint) — e.g. the group holding
   *  the current route. Presentational only. */
  active?: boolean;
  /** Leading icon node (rendered between the chevron and the title). */
  icon?: ReactNode;
  /** Right-aligned slot on the header (e.g. a summary chip). */
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  headerClassName?: string;
  bodyClassName?: string;
}

export function Collapsible({
  title,
  defaultOpen = false,
  persistKey,
  forceOpen = false,
  active = false,
  icon,
  right,
  children,
  className,
  headerClassName,
  bodyClassName,
}: CollapsibleProps) {
  const [open, setOpen] = useState(() =>
    readPersisted(persistKey, defaultOpen),
  );

  // When the active route lands inside this group, forceOpen flips true and
  // we surface it — without clobbering the user's stored manual choice (so it
  // collapses back to that choice once the route moves elsewhere).
  useEffect(() => {
    if (forceOpen && !open) setOpen(true);
  }, [forceOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = open || forceOpen;

  const toggle = () => {
    const next = !shown;
    setOpen(next);
    writePersisted(persistKey, next);
  };

  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={shown}
        data-active={active ? "true" : undefined}
        onClick={toggle}
        className={cn(
          "flex w-full items-center gap-2 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          active && "text-ink",
          headerClassName,
        )}
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 text-ink-3 transition-transform",
            shown && "rotate-90",
            active && "text-primary",
          )}
          aria-hidden
        />
        {icon}
        <span className="min-w-0 flex-1">{title}</span>
        {right}
      </button>
      {shown && <div className={bodyClassName}>{children}</div>}
    </div>
  );
}
