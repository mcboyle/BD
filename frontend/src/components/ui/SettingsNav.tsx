import { cn } from "@/lib/utils";

// Cut 5 — SettingsNav: the Settings mini-ToC. Lists ALL page sections (locked
// decision: full nav) and jumps to each section anchor. A changed-marker dot
// fires only on sections passed in `changedSections` (config-backed sections
// with an unsaved field). Presentational + an onNavigate callback; the page
// owns scroll/anchor behaviour and the changed-set derivation.

export interface SettingsNavSection {
  id: string;
  label: string;
}

export interface SettingsNavProps {
  sections: SettingsNavSection[];
  /** Section ids holding at least one unsaved (config-backed) field. */
  changedSections: Set<string>;
  /** Currently in-view section id (scrollspy), for active styling. */
  activeId?: string;
  onNavigate: (id: string) => void;
  /** "rail" = vertical sidebar (default); "chips" = horizontal wrap pills. */
  variant?: "rail" | "chips";
  className?: string;
}

export function SettingsNav({
  sections,
  changedSections,
  activeId,
  onNavigate,
  variant = "rail",
  className,
}: SettingsNavProps) {
  if (variant === "chips") {
    return (
      <nav
        aria-label="Settings sections"
        className={cn("flex flex-wrap gap-1.5", className)}
      >
        {sections.map((s) => {
          const changed = changedSections.has(s.id);
          const active = activeId === s.id;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onNavigate(s.id)}
              aria-current={active ? "true" : undefined}
              className={cn(
                "hairline inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                active
                  ? "bg-primary-soft text-primary"
                  : "bg-surface-2 text-ink-2 hover:text-ink",
              )}
            >
              <span>{s.label}</span>
              {changed ? (
                <span
                  data-testid="settingsnav-changed-marker"
                  aria-label="unsaved changes in this section"
                  title="Unsaved changes"
                  className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-dim"
                />
              ) : null}
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <nav aria-label="Settings sections" className={cn("flex flex-col gap-0.5", className)}>
      {sections.map((s) => {
        const changed = changedSections.has(s.id);
        const active = activeId === s.id;
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => onNavigate(s.id)}
            aria-current={active ? "true" : undefined}
            className={cn(
              "group flex items-center justify-between rounded px-2 py-1.5 text-left text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
              active
                ? "bg-surface-2 font-medium text-ink-1"
                : "text-ink-2 hover:bg-surface-2/60 hover:text-ink-1",
            )}
          >
            <span className="truncate">{s.label}</span>
            {changed ? (
              <span
                data-testid="settingsnav-changed-marker"
                aria-label="unsaved changes in this section"
                title="Unsaved changes"
                className="ml-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-dim"
              />
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
