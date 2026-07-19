import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// P6-1 (data display) — row-shaped loading placeholder for the list pages.
// Renders `count` row-height Skeletons so the loading state previews the list
// shape instead of one undifferentiated block. The container carries
// aria-busy; the individual Skeletons are aria-hidden (per the primitive).

export interface SkeletonRowsProps {
  count?: number;
  /** Tailwind height class per row; defaults to a comfortable row height. */
  rowClassName?: string;
  className?: string;
}

export function SkeletonRows({
  count = 5,
  rowClassName = "h-12",
  className,
}: SkeletonRowsProps) {
  return (
    <div
      aria-busy="true"
      aria-label="Loading"
      className={cn("space-y-1.5", className)}
    >
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className={cn("w-full", rowClassName)} />
      ))}
    </div>
  );
}
