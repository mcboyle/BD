import { createContext, useContext } from "react";

import { Card } from "@/components/ui/card";
import { Collapsible } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

// Slice 2 — settings search. Settings.tsx provides the query via this context;
// SettingRow self-hides on no match, and SettingSection expands (ignoring its
// collapsed default) while a query is active so matches are revealed. Empty
// sections are hidden via CSS :has() (see index.css .settings-searching).
export const SettingsSearchContext = createContext("");

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

// Visual grouping for Settings + Advanced. Mockup signature:
//   - Tiny uppercase section label OUTSIDE the card
//   - Card holds a vertical list of rows, divided by hairlines
//   - Each row is label-on-left, control-on-right
//
// v3.66.326: optional `collapsible` — folds the section under its label so
// the long Settings scroll becomes scannable. Everyday sections stay open;
// power-user ones (Capture, Diagnostics, keep-alive, Security…) collapse by
// default. Non-collapsible is unchanged (the default).

export interface SettingSectionProps {
  label: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
  /** Fold the section under a clickable label. */
  collapsible?: boolean;
  /** When collapsible, open on first render. Default closed. */
  defaultOpen?: boolean;
}

export function SettingSection({
  label,
  description,
  children,
  className,
  collapsible,
  defaultOpen,
}: SettingSectionProps) {
  const q = useContext(SettingsSearchContext);
  const searching = q.trim().length > 0;
  const id = slugify(label);
  const sectionCls = cn("space-y-2 scroll-mt-16", className);
  if (collapsible && !searching) {
    return (
      <section id={id} data-section className={sectionCls}>
        <Collapsible
          defaultOpen={defaultOpen}
          headerClassName="px-1 py-0.5"
          bodyClassName="mt-2 space-y-2"
          title={
            <span className="space-y-0.5">
              <span className="block eyebrow">
                {label}
              </span>
              {description && (
                <span className="block text-[11px] text-ink-3">{description}</span>
              )}
            </span>
          }
        >
          <Card className="divide-y divide-hairline">{children}</Card>
        </Collapsible>
      </section>
    );
  }
  return (
    <section id={id} data-section className={sectionCls}>
      <header className="space-y-0.5 px-1">
        <h2 className="eyebrow">
          {label}
        </h2>
        {description && (
          <p className="text-[11px] text-ink-3">{description}</p>
        )}
      </header>
      <Card className="divide-y divide-hairline">{children}</Card>
    </section>
  );
}

// One row inside a SettingSection. Layout:
//   - Label (and optional sub-line) on the left
//   - Control on the right
//   - On small screens or when the control needs more space, the
//     control wraps below.

export interface SettingRowProps {
  label: string;
  hint?: string;
  control: React.ReactNode;
  /** Wrap the control under the label instead of next to it. */
  stacked?: boolean;
  /** Render the hint as an irrecoverable-effects warning (disclaimer). */
  danger?: boolean;
  /** P6-8: badge the row when its saved value differs from the shipped default. */
  modified?: boolean;
  /** Cut 5: provenance/affordance slot under the label (OriginChip, CopyButton). */
  aside?: React.ReactNode;
}

export function SettingRow({ label, hint, control, stacked, danger, modified, aside }: SettingRowProps) {
  const q = useContext(SettingsSearchContext).trim().toLowerCase();
  const hide = q.length > 0 && !`${label} ${hint ?? ""}`.toLowerCase().includes(q);
  return (
    <div
      data-srow
      hidden={hide}
      className={cn(
        "p-3",
        hide
          ? "hidden"
          : stacked
            ? "space-y-2"
            : "flex items-center justify-between gap-3",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-ink">
          {label}
          {modified && (
            <span className="ml-2 rounded-sm bg-primary-soft px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wider text-primary">
              modified
            </span>
          )}
        </div>
        {hint && (
          <div className={cn("mt-0.5 text-[11px]", danger ? "text-red" : "text-ink-3")}>
            {danger && <span aria-hidden="true">⚠ </span>}{hint}
          </div>
        )}
        {aside && <div className="mt-1 flex flex-wrap items-center gap-2">{aside}</div>}
      </div>
      <div className={cn("shrink-0", stacked && "w-full")}>{control}</div>
    </div>
  );
}
