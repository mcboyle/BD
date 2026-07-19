import { useState } from "react";
import { AlertTriangle, ChevronDown, X } from "lucide-react";

import { cn } from "@/lib/utils";

// P6-3 -> polish pass item 2: the gated-write surface note is now COMPACT.
// A single amber line ("<title> - Review required before changes apply - Details")
// with the full per-route description disclosed behind Details, and per-session
// dismissible (sessionStorage; NO backend persistence). Presentational only:
// the safety meaning and the full text are preserved, just far less dominant
// than the old full-width amber paragraph that repeated on every admin page.
//
// Call-sites are unchanged -- `title` (optional) is the lead label and
// `children` (the per-route detail) moves into the Details disclosure.
//
// Convergence #4 (banner tiering): `level` tiers the visual weight WITHOUT
// touching the safety meaning. Both levels keep the full per-route text behind
// Details and stay per-session dismissible; only the collapsed header differs.
//   full  (default)  the one-line amber bar — write-heavy / dangerous pages
//   chip             a smaller inline amber pill — lower-risk / read-mostly
// `level` defaults to "full" so every existing call-site is unchanged.

export type GatedWriteBannerLevel = "full" | "chip";

export interface GatedWriteBannerProps {
  /** Lead label. Defaults to "Gated writes enabled". */
  title?: React.ReactNode;
  /** Full safety detail — disclosed under Details (not shown until expanded). */
  children: React.ReactNode;
  className?: string;
  /** Stable key for the per-session dismiss; defaults to a slug of the title. */
  dismissKey?: string;
  /** Visual weight tier. Defaults to "full". Never changes the safety text. */
  level?: GatedWriteBannerLevel;
  /** routeRisk-aligned alias for `level`. When set, wins over `level`.
   *  Call-sites pass routeRisk(path).bannerShape so tiering reads from one
   *  source of truth. Same values/meaning as `level`. */
  shape?: GatedWriteBannerLevel;
}

function keyFor(title: React.ReactNode, explicit?: string): string {
  if (explicit) return explicit;
  const t = typeof title === "string" ? title : "gated-write";
  return "bd-gwb-dismiss:" + t.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

export function GatedWriteBanner({
  title = "Gated writes enabled",
  children,
  className,
  dismissKey,
  level = "full",
  shape,
}: GatedWriteBannerProps) {
  // `shape` is the routeRisk-aligned alias and wins when provided.
  const tier: GatedWriteBannerLevel = shape ?? level;
  const k = keyFor(title, dismissKey);
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(k) === "1";
    } catch {
      return false;
    }
  });
  const [open, setOpen] = useState(false);
  if (dismissed) return null;

  const dismiss = () => {
    try {
      sessionStorage.setItem(k, "1");
    } catch {
      /* session storage unavailable — dismiss for this mount only */
    }
    setDismissed(true);
  };

  if (tier === "chip") {
    // Compact pill: a smaller amber chip for lower-risk pages. Same safety
    // text behind Details, same per-session dismiss — only the collapsed
    // header is lighter (no full-width bar, no "Review required" subtitle).
    return (
      <div className={cn("text-[11px]", className)}>
        <div
          role="note"
          className="inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-full border border-amber/30 bg-amber-soft px-2.5 py-1"
        >
          <AlertTriangle
            className="h-3 w-3 shrink-0 text-amber-dim"
            aria-hidden
          />
          <span className="font-medium text-amber-dim">{title}</span>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="inline-flex items-center gap-0.5 rounded font-medium text-amber-dim hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            Details
            <ChevronDown
              className={cn("h-3 w-3 transition-transform", open && "rotate-180")}
              aria-hidden
            />
          </button>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss for this session"
            className="rounded p-0.5 text-amber-dim/70 hover:text-amber-dim focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </div>
        {open && (
          <div className="mt-1.5 rounded-lg border border-amber/20 bg-amber-soft/60 px-3 py-2 text-ink-3">
            {children}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      role="note"
      className={cn(
        "rounded-lg border border-amber/30 bg-amber-soft text-xs",
        className,
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <AlertTriangle
          className="h-3.5 w-3.5 shrink-0 text-amber-dim"
          aria-hidden
        />
        <span className="font-semibold text-amber-dim">{title}</span>
        <span className="hidden text-ink-3 sm:inline">
          · Review required before changes apply
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="ml-auto inline-flex items-center gap-1 rounded font-medium text-amber-dim hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          Details
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </button>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss for this session"
          className="rounded p-0.5 text-amber-dim/70 hover:text-amber-dim focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {open && (
        <div className="border-t border-amber/20 px-3 py-2 text-ink-3">
          {children}
        </div>
      )}
    </div>
  );
}
