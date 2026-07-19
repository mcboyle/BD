import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

// Cut 5 — ValidationSummary: a top-of-page rollup of active field validation
// errors (e.g. malformed domain-overrides JSON, a non-absolute allowlist root).
// Inert when there are none. Each row jumps to the offending field's section so
// the operator can fix it without hunting. Presentational; the page owns the
// problem list + the jump target.

export interface ValidationProblem {
  /** Stable field key (used for onJump + React key). */
  field: string;
  /** Human label for the field/section. */
  label: string;
  /** The validation message to show. */
  message: string;
}

export interface ValidationSummaryProps {
  problems: ValidationProblem[];
  /** Jump to the field's section (e.g. scroll to its anchor). */
  onJump?: (field: string) => void;
  className?: string;
}

export function ValidationSummary({ problems, onJump, className }: ValidationSummaryProps) {
  if (problems.length === 0) return null;

  const heading =
    problems.length === 1
      ? "1 setting needs attention before saving"
      : `${problems.length} settings need attention before saving`;

  return (
    <div
      role="alert"
      className={cn(
        "rounded-lg border border-red/40 bg-red-soft/50 px-4 py-3",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-red" aria-hidden />
        <span className="text-sm font-semibold text-red">{heading}</span>
      </div>
      <ul className="mt-2 space-y-1">
        {problems.map((p) => (
          <li key={p.field} className="text-xs text-ink-2">
            <button
              type="button"
              onClick={() => onJump?.(p.field)}
              className="font-medium text-ink-1 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {p.label}
            </button>
            {": "}
            <span>{p.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
