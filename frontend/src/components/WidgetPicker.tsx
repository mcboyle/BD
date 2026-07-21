import { useMemo, useState } from "react";
import { Check, Plus } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { KPICard } from "@/components/KPICard";
import { useWidgetSelection } from "@/hooks/useWidgetSelection";
import { useWidgetData } from "@/hooks/useWidgetData";
import {
  CATEGORIES,
  WIDGETS,
  WIDGETS_BY_ID,
} from "@/lib/widgetCatalog";
import { cn } from "@/lib/utils";

// v3.64.x — widget picker overlay. Opens from the Home tab's "Add
// widget" button. The legacy `/` UI called this the "widget library."
//
// UX:
//   • Category tabs across the top (Activity / Performance / Capacity
//     / Health / System / Collection / Diagnostics + an "All" tab).
//   • Grid of preview cards — each shows a small render of the
//     widget with live data, plus a title, description, and a button
//     to add or remove it from the active dashboard.
//   • Selecting from the picker doesn't close it — the operator can
//     pick several widgets in one go. Close via Esc or the X.
//
// Click-outside-to-close is INTENTIONALLY disabled at the dialog
// primitive level (see Unit A's fix in dialog.tsx) — the operator
// shouldn't lose their picking session to a stray scrim click.

export interface WidgetPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // v3.65.2: when set, picker operates on the per-site widget scope
  // instead of the global one. Both hooks already accept an optional
  // siteId — without threading it here, opening the picker from
  // SiteDetail would still read/write the GLOBAL widget set, so
  // toggling a widget for one site would change everyone's dashboard.
  // The prop is optional so the Home tab's global picker keeps
  // working with no callsite changes.
  siteId?: string;
}

export function WidgetPicker({ open, onOpenChange, siteId }: WidgetPickerProps) {
  const { ids, add, remove } = useWidgetSelection(siteId);
  const { data } = useWidgetData(siteId);
  const [activeCat, setActiveCat] = useState<string>("all");

  const selectedSet = useMemo(() => new Set(ids), [ids]);

  const visibleWidgets = useMemo(
    () => (activeCat === "all"
      ? WIDGETS
      : WIDGETS.filter(w => w.cat === activeCat)),
    [activeCat],
  );

  const countsByCat = useMemo(() => {
    const out: Record<string, number> = { all: WIDGETS.length };
    for (const c of CATEGORIES) {
      out[c.id] = WIDGETS.filter(w => w.cat === c.id).length;
    }
    return out;
  }, []);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "max-w-3xl",
          // Override the default p-6 with a tighter layout; the
          // picker grid wants more horizontal real estate.
          "p-0",
        )}
      >
        <DialogHeader className="border-b hairline px-4 py-3">
          <DialogTitle className="text-sm font-semibold">
            Widget library
          </DialogTitle>
          <DialogDescription className="text-xs text-ink-3">
            Choose which widgets appear on this dashboard.
          </DialogDescription>
          <div className="text-xs text-ink-3">
            {ids.length} of {WIDGETS.length} widgets active
          </div>
        </DialogHeader>

        {/* Category tabs */}
        <div className="flex flex-wrap items-center gap-1 border-b hairline px-3 py-2">
          <CatTab
            id="all"
            label="All"
            count={countsByCat.all}
            active={activeCat === "all"}
            onClick={() => setActiveCat("all")}
          />
          {CATEGORIES.map(c => (
            <CatTab
              key={c.id}
              id={c.id}
              label={c.label}
              icon={<c.icon className="h-3 w-3" aria-hidden />}
              count={countsByCat[c.id] ?? 0}
              active={activeCat === c.id}
              onClick={() => setActiveCat(c.id)}
            />
          ))}
        </div>

        {/* Grid of preview cards */}
        <div className="max-h-[60vh] overflow-y-auto p-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {visibleWidgets.map(w => {
              const isSelected = selectedSet.has(w.id);
              const spec = w.spec(data);
              return (
                <div
                  key={w.id}
                  className={cn(
                    "hairline group relative overflow-hidden rounded-md border bg-surface-2 transition-colors",
                    isSelected && "border-primary bg-primary-soft",
                  )}
                >
                  <div className="h-[88px]">
                    <KPICard spec={spec} compact />
                  </div>
                  <div className="border-t hairline px-3 py-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-ink">
                          {w.title}
                        </div>
                        <div className="line-clamp-2 text-[11px] text-ink-3">
                          {w.desc}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          isSelected ? remove(w.id) : add(w.id)
                        }
                        aria-pressed={isSelected}
                        aria-label={isSelected
                          ? `Remove ${w.title} from dashboard`
                          : `Add ${w.title} to dashboard`}
                        className={cn(
                          "hairline grid h-6 w-6 shrink-0 place-items-center rounded-sm border transition-colors",
                          isSelected
                            ? "border-primary bg-primary text-surface"
                            : "border-ink-3/30 bg-surface text-ink-3 hover:text-ink",
                        )}
                      >
                        {isSelected ? (
                          <Check className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <Plus className="h-3.5 w-3.5" aria-hidden />
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CatTab({
  id,
  label,
  icon,
  count,
  active,
  onClick,
}: {
  id: string;
  label: string;
  icon?: React.ReactNode;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-cat={id}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-surface-2 text-ink"
          : "text-ink-3 hover:bg-surface-2 hover:text-ink",
      )}
    >
      {icon}
      <span>{label}</span>
      <span className="tabular text-[10px] text-ink-3">{count}</span>
    </button>
  );
}

// Re-export for callers that want the catalog without picker.
export { WIDGETS_BY_ID };
