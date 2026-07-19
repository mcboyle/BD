import { useEffect, useState } from "react";
import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { toast } from "sonner";

import { SortableWaitingJobRow } from "@/components/SortableWaitingJobRow";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiPost } from "@/lib/api-client";
import type { QueueV2Full, QueueWaitingEntry } from "@/lib/api-types";

// Focused per-site reorder view shown when the operator taps a site
// chip in the "Sort" mode of the Queue tab.
//
// Lifts the per-site waiting jobs into local state so drag-to-reorder
// is fully optimistic during the gesture. POST to
// /api/sites/<sid>/jobs/reorder on each drag end, with `ord` values
// 10/20/30… (gaps for future inserts). On error, revert + toast.
//
// Accessibility: dnd-kit's keyboard sensor gives full arrow-key
// reordering for screen-reader/keyboard users. Each row's drag
// handle has an aria-label naming the file.

export interface SortableQueueGroupProps {
  siteId: string;
  siteName: string;
  jobs: QueueWaitingEntry[];
  onDone: () => void;
}

export function SortableQueueGroup({
  siteId,
  siteName,
  jobs,
  onDone,
}: SortableQueueGroupProps) {
  const qc = useQueryClient();

  // Local order. Initialized from `jobs`; updated optimistically on
  // each drag end. If the upstream query refetches with a new order,
  // the parent should remount this component to pick up — we do NOT
  // sync on every prop update mid-drag because that would fight the
  // user's gesture.
  const [orderedJobs, setOrderedJobs] = useState(jobs);
  const [activeId, setActiveId] = useState<string | null>(null);

  // Pick up new jobs (e.g. one finished and dropped out, or a new one
  // arrived). De-dupes against the current order: known URLs keep
  // their position, unknown ones get appended. This keeps the
  // operator's drag work intact across polls.
  useEffect(() => {
    setOrderedJobs((prev) => {
      const prevUrls = new Set(prev.map((j) => j.url));
      const incomingUrls = new Set(jobs.map((j) => j.url));
      // Drop jobs that vanished
      const kept = prev.filter((j) => incomingUrls.has(j.url));
      // Append truly new ones at the end
      const newOnes = jobs.filter((j) => !prevUrls.has(j.url));
      // Refresh job payloads for kept entries (filename might have
      // changed, etc.)
      const byUrl = new Map(jobs.map((j) => [j.url, j]));
      const refreshed = kept.map((j) => byUrl.get(j.url) ?? j);
      return [...refreshed, ...newOnes];
    });
  }, [jobs]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 }, // tap tolerance
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  type ReorderResponse = { ok: boolean; affected: number };
  const reorderMut = useMutation<
    ReorderResponse,
    Error,
    { urls: string[]; prevOrder: QueueWaitingEntry[] }
  >({
    mutationFn: ({ urls }) => {
      // Compose {url: ord} with 10/20/30 gaps so future inserts can
      // slot in without renumbering. Server clamps to 5000.
      const ordering: Record<string, number> = {};
      urls.forEach((u, i) => {
        ordering[u] = (i + 1) * 10;
      });
      return apiPost<ReorderResponse>(
        `/api/sites/${encodeURIComponent(siteId)}/jobs/reorder`,
        { ordering },
      );
    },
    onError: (err, vars) => {
      // Roll back the local order.
      setOrderedJobs(vars.prevOrder);
      toast.error(`Reorder failed: ${err.message}`);
    },
    onSuccess: () => {
      // Invalidate so the parent queue snapshot picks up server
      // truth on the next poll (and so other tabs see it).
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
  });

  const onDragStart = (e: DragStartEvent) => {
    setActiveId(String(e.active.id));
  };

  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null);
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIndex = orderedJobs.findIndex((j) => j.url === active.id);
    const newIndex = orderedJobs.findIndex((j) => j.url === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const prevOrder = orderedJobs;
    const next = arrayMove(orderedJobs, oldIndex, newIndex);
    setOrderedJobs(next);
    reorderMut.mutate({ urls: next.map((j) => j.url), prevOrder });
  };

  const activeJob =
    activeId !== null ? orderedJobs.find((j) => j.url === activeId) : null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            Sort: {siteName}
          </div>
          <div className="text-[11px] text-ink-3">
            {orderedJobs.length} waiting · drag to reorder
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={onDone}>
          <Check className="h-3.5 w-3.5" aria-hidden />
          Done
        </Button>
      </div>
      {orderedJobs.length === 0 ? (
        <Card className="p-4 text-center text-sm text-ink-3">
          No waiting jobs to reorder.
        </Card>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onDragCancel={() => setActiveId(null)}
        >
          <SortableContext
            items={orderedJobs.map((j) => j.url)}
            strategy={verticalListSortingStrategy}
          >
            <ul className="space-y-1.5">
              {orderedJobs.map((job) => (
                <li key={job.url}>
                  <SortableWaitingJobRow job={job} />
                </li>
              ))}
            </ul>
          </SortableContext>
          <DragOverlay>
            {activeJob ? (
              <SortableWaitingJobRow job={activeJob} isDragOverlay />
            ) : null}
          </DragOverlay>
        </DndContext>
      )}
      {reorderMut.isPending && (
        <div
          className="text-[11px] text-ink-3"
          role="status"
          aria-live="polite"
        >
          Saving order…
        </div>
      )}
    </div>
  );
}

// Helper that the Queue route uses to bucket the global waiting list
// by site_id and surface the chip ribbon. Returns [{site_id, name,
// count}] sorted by count desc so the busiest sites are first.
export function bucketWaitingBySite(
  data: QueueV2Full | undefined,
): { siteId: string; siteName: string; count: number }[] {
  if (!data) return [];
  const m = new Map<string, { siteId: string; siteName: string; count: number }>();
  for (const j of data.waiting) {
    const cur = m.get(j.site_id);
    if (cur) cur.count++;
    else
      m.set(j.site_id, {
        siteId: j.site_id,
        siteName: j.site_name,
        count: 1,
      });
  }
  return Array.from(m.values()).sort((a, b) => b.count - a.count);
}

// Small chip that displays a site name + waiting count and opens
// reorder mode for that site when tapped.
export function SiteSortChip({
  siteName,
  count,
  onClick,
  disabled,
}: {
  siteName: string;
  count: number;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || count < 2}
      className="hairline shrink-0 rounded-full bg-surface px-3 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
      aria-label={`Reorder ${count} waiting jobs for ${siteName}`}
    >
      <span className="font-medium">{siteName}</span>
      <span className="ml-1 tabular-nums text-ink-3">{count}</span>
    </button>
  );
}

// Convenience wrapper that closes over the chip click.
export function SortChipRibbon({
  buckets,
  onPick,
}: {
  buckets: { siteId: string; siteName: string; count: number }[];
  onPick: (siteId: string, siteName: string) => void;
}) {
  // No buckets with >=2 jobs = no chips worth showing.
  const reorderable = buckets.filter((b) => b.count >= 2);
  if (reorderable.length === 0) return null;
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto px-1 py-1">
      <span className="shrink-0 text-[11px] uppercase tracking-wide text-ink-3">
        Sort
      </span>
      {reorderable.map((b) => (
        <SiteSortChip
          key={b.siteId}
          siteName={b.siteName}
          count={b.count}
          onClick={() => onPick(b.siteId, b.siteName)}
        />
      ))}
    </div>
  );
}
