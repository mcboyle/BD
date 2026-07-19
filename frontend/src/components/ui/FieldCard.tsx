import { cn } from "@/lib/utils";

// Shared labeled-field-card (Slice 4c.4). The canonical form-field wrapper:
// a small field label (+ optional required marker), the control, and below it
// either a hint or — when validation fails — an inline error.
//
// Seeded from AddSiteWizard's local `Field`, generalized with an `error` slot
// so form pages get consistent inline validation. Presentational only: the
// page computes `error`; FieldCard just renders it (role=alert, suppressing the
// hint so the two never stack).

export interface FieldCardProps {
  /** Field label shown above the control. */
  label: React.ReactNode;
  /** Helper text below the control; hidden while an error is shown. */
  hint?: React.ReactNode;
  /** Marks the field required (red asterisk). */
  required?: boolean;
  /** Inline validation message. When set, replaces the hint. */
  error?: string;
  /** Extra classes on the field wrapper. */
  className?: string;
  children: React.ReactNode;
}

export function FieldCard({
  label,
  hint,
  required,
  error,
  className,
  children,
}: FieldCardProps) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="text-xs font-medium text-ink-2">
        {label}
        {required && (
          <span className="ml-0.5 text-red" aria-hidden>
            *
          </span>
        )}
      </span>
      {children}
      {error ? (
        <span role="alert" className="block text-[11px] text-red">
          {error}
        </span>
      ) : hint ? (
        <span className="block text-[11px] text-ink-3">{hint}</span>
      ) : null}
    </label>
  );
}
