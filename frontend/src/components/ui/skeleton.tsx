import { cn } from "@/lib/utils";

// Tier 1 #6 — loading skeletons. Animated pulse on a surface-2 fill.
// Used in U3+ to fill widget space while TanStack Query fetches the
// first response.
//
// U8 a11y: aria-hidden by default so screen readers don't announce
// the empty placeholder divs. The surrounding container should carry
// `aria-busy="true"` to signal the loading state — that's the
// caller's responsibility, not this primitive's.
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "animate-pulse rounded-md bg-surface-2",
        className,
      )}
      {...props}
    />
  );
}

export { Skeleton };
