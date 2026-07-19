import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "./badge";

// Cut 5 — OriginChip: per-field provenance from GET /api/global_config/origins.
// Shows the source (default / global / env) and apply-timing. env-locked adds a
// lock glyph + a "locked" hint. Secret-safe BY CONSTRUCTION: there is no `value`
// prop — this chip only ever renders provenance, never a field value.

export type SettingOrigin = "default" | "global" | "env";
export type ApplyTiming = "immediate" | "restart";

export interface OriginChipProps {
  origin: SettingOrigin;
  applyTiming: ApplyTiming;
  envLocked: boolean;
  /** Provenance-only marker; present for API symmetry, never echoes a value. */
  isSecret: boolean;
  className?: string;
}

const ORIGIN_META: Record<
  SettingOrigin,
  { label: string; variant: "secondary" | "default" | "outline" }
> = {
  default: { label: "default", variant: "secondary" },
  global: { label: "global", variant: "default" },
  env: { label: "env", variant: "outline" },
};

export function OriginChip({
  origin,
  applyTiming,
  envLocked,
  className,
}: OriginChipProps) {
  const meta = ORIGIN_META[origin];
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <Badge
        variant={meta.variant}
        glyph={envLocked ? <Lock className="h-3 w-3" aria-hidden /> : undefined}
        title={envLocked ? "Locked by an environment variable" : undefined}
      >
        {meta.label}
      </Badge>
      <span className="text-xs text-ink-3">
        {applyTiming === "restart" ? "restart" : "immediate"}
      </span>
      {envLocked ? (
        <span className="text-xs text-ink-3" aria-label="env locked">
          locked
        </span>
      ) : null}
    </span>
  );
}
