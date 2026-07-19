import { cn } from "@/lib/utils";

// Shared workflow step-indicator (Slice 4d). Extracted from AddSiteWizard's
// local Stepper so every multi-step flow renders the same progress affordance
// from one source. Decorative bars + small labels; the active step is marked
// aria-current="step" for assistive tech. Does not gate navigation — the page
// owns step transitions.

export interface WorkflowStepsProps {
  /** Ordered step labels. */
  steps: string[];
  /** Zero-based index of the current step. */
  current: number;
  /** Accessible name for the progress list. */
  ariaLabel?: string;
  className?: string;
}

export function WorkflowSteps({
  steps,
  current,
  ariaLabel = "Progress",
  className,
}: WorkflowStepsProps) {
  return (
    <ol
      className={cn("flex items-center gap-1.5", className)}
      aria-label={ariaLabel}
    >
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li
            key={label}
            className="flex flex-1 items-center gap-1.5"
            aria-current={state === "current" ? "step" : undefined}
          >
            <span
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                state === "done" && "bg-primary",
                state === "current" && "bg-primary/50",
                state === "todo" && "bg-surface-2",
              )}
              aria-hidden
            />
            <span
              className={cn(
                "text-[10px] font-semibold uppercase tracking-wider",
                state === "current" ? "text-ink-2" : "text-ink-3",
              )}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
