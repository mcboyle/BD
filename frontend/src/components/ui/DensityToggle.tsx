import { AlignJustify, Rows3 } from "lucide-react";

import { useDensity, type Density } from "@/hooks/useDensity";
import { cn } from "@/lib/utils";

// P6-1 (data display) — a compact segmented control to flip the app-wide list
// density. Two buttons (Comfortable / Compact) with aria-pressed; placed in a
// list page's header trailing slot. Reads/writes the shared useDensity store,
// so every row on the page re-renders with it.

const OPTIONS: ReadonlyArray<{ value: Density; label: string; Icon: typeof Rows3 }> = [
  { value: "comfortable", label: "Comfortable", Icon: Rows3 },
  { value: "compact", label: "Compact", Icon: AlignJustify },
];

export function DensityToggle({ className }: { className?: string }) {
  const { density, setDensity } = useDensity();
  return (
    <div
      data-density-toggle
      role="group"
      aria-label="Row density"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-2 p-0.5",
        className,
      )}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = density === value;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            aria-label={label}
            title={`${label} rows`}
            onClick={() => setDensity(value)}
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors",
              active
                ? "bg-surface text-ink shadow-sm"
                : "text-ink-3 hover:text-ink",
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
