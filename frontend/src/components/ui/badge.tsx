import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

// Badge variants map 1:1 to the mockup status pills:
//   - default     : primary-soft bg, primary text (informational)
//   - secondary   : surface-2 bg, ink-2 text     (neutral count chips)
//   - success     : green-soft bg, green text    (active, OK)
//   - warning     : amber-soft bg, amber text    (NEEDS ATTENTION)
//   - destructive : red-soft bg, red text        (errors, FAILED)
//   - outline     : hairline border only         (tab chips, filter chips)
const badgeVariants = cva(
  "inline-flex items-center rounded-sm px-2 py-0.5 text-xs font-semibold tabular-nums uppercase tracking-wide",
  {
    variants: {
      variant: {
        default: "bg-primary-soft text-primary",
        secondary: "bg-surface-2 text-ink-2",
        success: "bg-green-soft text-green",
        warning: "bg-amber-soft text-amber-dim",
        destructive: "bg-red-soft text-red",
        outline: "hairline bg-transparent text-ink-2",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {
  /** Optional decorative glyph (✓/!/⏸/✕/•) so color isn't the only status
   *  signal. Rendered aria-hidden before the label; semantics stay in the
   *  label text + variant. Absent -> nothing extra renders. */
  glyph?: React.ReactNode;
}

function Badge({ className, variant, glyph, children, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props}>
      {glyph != null && glyph !== false ? (
        <span aria-hidden className="mr-1 leading-none">
          {glyph}
        </span>
      ) : null}
      {children}
    </div>
  );
}

export { Badge, badgeVariants };
