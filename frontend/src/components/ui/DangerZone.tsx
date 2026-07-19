import { ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";

// Polish pass item 3 — a shared container for grouping high-risk / destructive
// controls so a red button never stands alone on a page. A red-accented frame +
// one short warning line + the destructive controls grouped inside.
//
// Presentational + GROUPING ONLY. It does NOT add, ease, or bypass any action:
// confirmation and gating stay wherever the underlying control already enforces
// them (this never lowers a guard or makes a destructive action easier to fire).

export interface DangerZoneProps {
  /** Heading. Defaults to "Danger zone". */
  title?: React.ReactNode;
  /** One short warning sentence shown under the title. */
  warning?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function DangerZone({
  title = "Danger zone",
  warning,
  children,
  className,
}: DangerZoneProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-red/40 bg-red-soft/40",
        className,
      )}
      aria-label={typeof title === "string" ? title : "Danger zone"}
    >
      <div className="flex items-center gap-2 border-b border-red/20 px-3 py-2">
        <ShieldAlert className="h-4 w-4 shrink-0 text-red" aria-hidden />
        <span className="text-sm font-semibold text-red">{title}</span>
      </div>
      <div className="space-y-3 p-3">
        {warning ? <p className="text-xs text-ink-3">{warning}</p> : null}
        {children}
      </div>
    </section>
  );
}

export default DangerZone;
