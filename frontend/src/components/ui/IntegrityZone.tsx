import { ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";

// Cut 1 substrate — IntegrityZone: an AMBER (not red) grouping container for
// capture/redaction integrity controls, so they read as "protect this" rather
// than "destroy this". The amber/red split mirrors the meaning: DangerZone is
// destructive (red); IntegrityZone is protective (amber).
//
// Presentational + GROUPING ONLY. It never lowers a guard, weakens redaction,
// or changes any capture behavior — it only frames the controls.

export interface IntegrityZoneProps {
  /** Heading. Defaults to "Integrity". */
  title?: React.ReactNode;
  /** One short note shown under the title. */
  note?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function IntegrityZone({
  title = "Integrity",
  note,
  children,
  className,
}: IntegrityZoneProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-amber/40 bg-amber-soft/40",
        className,
      )}
      aria-label={typeof title === "string" ? title : "Integrity"}
    >
      <div className="flex items-center gap-2 border-b border-amber/20 px-3 py-2">
        <ShieldCheck className="h-4 w-4 shrink-0 text-amber-dim" aria-hidden />
        <span className="text-sm font-semibold text-amber-dim">{title}</span>
      </div>
      <div className="space-y-3 p-3">
        {note ? <p className="text-xs text-ink-3">{note}</p> : null}
        {children}
      </div>
    </section>
  );
}
