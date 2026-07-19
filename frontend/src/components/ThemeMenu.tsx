import { Monitor, Moon, Sun, Palette, Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useTheme } from "@/hooks/useTheme";
import { THEME_IDS, type ThemeMode, type ThemeId } from "@/lib/themes";
import { cn } from "@/lib/utils";

// Cut 1 substrate — ThemeMenu: ONE theme control reused in the sidebar footer
// AND Settings (both just drive useTheme). Two variants:
//   inline   — the three quick switches (System / Light / Dark) shown inline,
//              plus the full catalog dropdown. For Settings + expanded sidebar.
//   compact  — a single icon trigger that opens a popover with the same
//              switches. For the collapsed sidebar / mobile.
// No second picker is copied; ThemeToggle becomes a thin re-export of inline.
//
// The quick-mode option list (system/light/dark) can be supplied by the caller
// (ThemeToggle owns the canonical THEME_QUICK_MODES and passes it in); when
// absent we fall back to the same default here. Kept as an optional prop rather
// than a cross-import so there's no ThemeMenu<->ThemeToggle module cycle.

export interface QuickMode {
  value: ThemeMode;
  label: string;
}

const MODE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

const DEFAULT_QUICK: ReadonlyArray<QuickMode> = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const CATALOG: ThemeId[] = THEME_IDS.filter(
  (id) => id !== "light" && id !== "dark",
);

interface QuickModeWithIcon extends QuickMode {
  icon: React.ComponentType<{ className?: string }>;
}

function QuickSwitches({
  mode,
  setMode,
  modes,
}: {
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
  modes: ReadonlyArray<QuickModeWithIcon>;
}) {
  return (
    <div className="flex items-center gap-1">
      {modes.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          onClick={() => setMode(value)}
          aria-pressed={mode === value}
          className={cn(
            "inline-flex items-center gap-1 rounded px-2 py-1 text-xs",
            mode === value
              ? "bg-primary/15 text-primary"
              : "text-ink-3 hover:text-ink-1",
          )}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

export interface ThemeMenuProps {
  variant?: "inline" | "compact";
  className?: string;
  /** Quick-mode option list (system/light/dark). Defaults to DEFAULT_QUICK.
   *  ThemeToggle passes its canonical THEME_QUICK_MODES here. */
  quickModes?: ReadonlyArray<QuickMode>;
}

export function ThemeMenu({
  variant = "inline",
  className,
  quickModes = DEFAULT_QUICK,
}: ThemeMenuProps) {
  const quick: ReadonlyArray<QuickModeWithIcon> = quickModes.map((m) => ({
    ...m,
    icon: MODE_ICONS[m.value as string] ?? Monitor,
  }));
  const { mode, setMode, resolved } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Click-outside closes the compact popover (transient affordance).
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (variant === "compact") {
    return (
      <div ref={ref} className={cn("relative", className)}>
        <button
          type="button"
          aria-label="Theme"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center justify-center rounded p-1.5 text-ink-3 hover:text-ink-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <Palette className="h-4 w-4" />
        </button>
        {open ? (
          <div className="absolute bottom-full left-0 z-50 mb-1 rounded-lg border border-line bg-surface-1 p-2 shadow-lg">
            <QuickSwitches mode={mode} setMode={setMode} modes={quick} />
          </div>
        ) : null}
      </div>
    );
  }

  // inline
  return (
    <div className={cn("space-y-1.5", className)}>
      <QuickSwitches mode={mode} setMode={setMode} modes={quick} />
      <select
        aria-label="Theme catalog"
        value={CATALOG.includes(resolved) ? resolved : ""}
        onChange={(e) => e.target.value && setMode(e.target.value as ThemeMode)}
        className="w-full rounded border border-line bg-surface-1 px-2 py-1 text-xs text-ink-1"
      >
        <option value="">More themes…</option>
        {CATALOG.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
    </div>
  );
}

// Surface the resolved-theme glyph for callers that want a checkmark indicator.
export function ThemeCheck({ active }: { active: boolean }) {
  return active ? <Check className="h-3 w-3" aria-hidden /> : null;
}
