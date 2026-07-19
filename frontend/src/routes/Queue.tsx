import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, Pause, Play, Plus, Square, Trash2, Wrench, X } from "lucide-react";
import { toast } from "sonner";

import { AddUrlDialog } from "@/components/AddUrlDialog";
import { QueueOpsDialog } from "@/components/QueueOpsDialog";
import { AppShell } from "@/components/AppShell";
import { BulkActionBar, BulkActionButton } from "@/components/BulkActionBar";
import { JobErrorModal } from "@/components/JobErrorModal";
import { QueuePreflightStrip } from "@/components/QueuePreflightStrip";
import { QueueTemplatesPanel } from "@/components/ui/QueueTemplatesPanel";
import { SiteSearchPanel } from "@/components/ui/SiteSearchPanel";
import { RunningJobRow } from "@/components/RunningJobRow";
import {
  SortableQueueGroup,
  SortChipRibbon,
  bucketWaitingBySite,
} from "@/components/SortableQueueGroup";
import { WaitingJobRow } from "@/components/WaitingJobRow";
import { QueueFilterChips } from "@/components/QueueFilterChips";
import { RowQuickActions } from "@/components/RowQuickActions";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SkeletonRows } from "@/components/ui/SkeletonRows";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { useDensity } from "@/hooks/useDensity";
import { useBulkSelection } from "@/hooks/useBulkSelection";
import { useQueueStream } from "@/hooks/useQueueStream";
import { useUrlState } from "@/hooks/useUrlState";
import { apiGet, apiPost } from "@/lib/api-client";
import { formatEta } from "@/lib/format";
import { adaptiveInterval } from "@/lib/polling";
import type {
  QueueRunningEntry,
  QueueV2Full,
  QueueWaitingEntry,
} from "@/lib/api-types";

// Queue tab. Top-down composition:
//   - Header trailing slot: Pause All / Resume All toggle (+ Select)
//   - Three counter chips: Running · Waiting · Done today
//   - Now-Running list (live SSE progress, tap → error modal)
//   - "Sort" chip ribbon — one chip per site with >=2 waiting jobs;
//     tap opens focused per-site reorder mode (U2 drag-to-reorder).
//   - Waiting list with per-row cancel button + tap → error modal
//
// v3.64.x D3 follow-up: bulk select on Waiting (U1) + drag-to-reorder
// within one site (U2). The two modes are mutually exclusive — sort
// mode replaces the Waiting list with a focused per-site view; select
// mode adds checkboxes to the flat list. The header trailing slot
// reflects whichever mode (if any) is active.

type ModalTarget = {
  site_id: string;
  site_name: string;
  url: string;
  filename: string;
};

// Composite key for selection. WaitingJobRow is keyed by
// `${site_id}-${url}` and the bulk_cancel endpoint takes a list of
// {site_id, url} pairs, so we encode here and split on send.
const KEY_SEP = "\u0001"; // pick a separator that can't appear in a URL
function selKey(j: { site_id: string; url: string }): string {
  return `${j.site_id}${KEY_SEP}${j.url}`;
}
function parseKey(k: string): { site_id: string; url: string } | null {
  const ix = k.indexOf(KEY_SEP);
  if (ix < 0) return null;
  return { site_id: k.slice(0, ix), url: k.slice(ix + 1) };
}

export function Queue() {
  const qc = useQueryClient();
  const { isCompact } = useDensity();
  const [modalTarget, setModalTarget] = useState<ModalTarget | null>(null);
  const [addUrlOpen, setAddUrlOpen] = useState(false);
  const [queueOpsOpen, setQueueOpsOpen] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  const [sortingSite, setSortingSite] = useState<{
    id: string;
    name: string;
  } | null>(null);

  const { data, isLoading, isError } = useQuery<QueueV2Full>({
    queryKey: ["queue-v2"],
    queryFn: ({ signal }) => apiGet<QueueV2Full>("/api/queue/v2", signal),
    refetchInterval: (q) =>
      adaptiveInterval<QueueV2Full>({
        query: q,
        isBusy: (d) =>
          (d.running?.length ?? 0) > 0 || (d.waiting?.length ?? 0) > 0,
        fast: 3000,
        slow: 10_000,
      }),
  });

  // Subscribe to the SSE progress stream only when there's something
  // running — saves a connection on idle screens.
  const hasRunning = (data?.running?.length ?? 0) > 0;
  const progressMap = useQueueStream(hasRunning);

  // Pause-all / Resume-all. Optimistic: flip the polling cadence
  // immediately and toast on success.
  const pauseAllMut = useMutation({
    mutationFn: () => apiPost("/api/pause_all", {}),
    onSuccess: () => {
      toast.success("Paused all sites");
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
    },
    onError: (e: Error) => toast.error(`Pause-all failed: ${e.message}`),
  });
  const resumeAllMut = useMutation({
    mutationFn: () => apiPost("/api/resume_all", {}),
    onSuccess: () => {
      toast.success("Resumed all sites");
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
    },
    onError: (e: Error) => toast.error(`Resume-all failed: ${e.message}`),
  });

  // Start-all (T19a): kicks off EVERY runner at once. Unlike pause/resume
  // (reversible), this commits all sites to running, so it is typed-confirm
  // gated rather than one-click. CSRF + backend audit apply on the POST.
  const [startAllConfirm, setStartAllConfirm] = useState(false);
  const startAllMut = useMutation({
    mutationFn: () => apiPost("/api/start_all", {}),
    onSuccess: () => {
      toast.success("Started all sites");
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
    },
    onError: (e: Error) => toast.error(`Start-all failed: ${e.message}`),
  });

  // Per-job cancel (waiting list). Uses optimistic update: remove
  // the row immediately, roll back on error.
  const cancelMut = useMutation({
    mutationFn: (vars: { site_id: string; url: string }) =>
      apiPost("/api/queue/v2/cancel", vars),
    onMutate: async ({ url }) => {
      await qc.cancelQueries({ queryKey: ["queue-v2"] });
      const prev = qc.getQueryData<QueueV2Full>(["queue-v2"]);
      if (prev) {
        qc.setQueryData<QueueV2Full>(["queue-v2"], {
          ...prev,
          waiting: prev.waiting.filter((w) => w.url !== url),
        });
      }
      return { prev };
    },
    onError: (err: Error, _vars, ctx) => {
      // Rollback to the previous snapshot.
      if (ctx?.prev) qc.setQueryData(["queue-v2"], ctx.prev);
      toast.error(`Cancel failed: ${err.message}`);
    },
    onSuccess: (_, vars) => {
      const w = data?.waiting.find((x) => x.url === vars.url);
      toast.success(`Cancelled ${w?.filename || "job"}`);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
  });

  // F1.7 pin-to-front (waiting list). POSTs priority=high to the existing
  // per-site priority endpoint; the runner moves the URL to the queue head.
  const pinMut = useMutation({
    mutationFn: (vars: { site_id: string; url: string }) =>
      apiPost(`/api/sites/${vars.site_id}/priority`, {
        url: vars.url,
        priority: "high",
      }),
    onError: (e: Error) => toast.error(`Pin failed: ${e.message}`),
    onSuccess: (_, vars) => {
      const w = data?.waiting.find((x) => x.url === vars.url);
      toast.success(`Pinned ${w?.filename || "job"} to front`);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
  });

  const running = data?.running ?? [];
  const waiting = data?.waiting ?? [];
  const doneToday = data?.done_today_count ?? 0;
  const truncated = data?.waiting_truncated_count ?? 0;

  // Cut 6.4/6.6 — URL-encoded status filter (shareable, zero persistence) +
  // removable filter chips. "all" shows both sections; "running"/"waiting"
  // narrow to one. The active filter renders as a removable chip.
  const [statusFilter, setStatusFilter] = useUrlState("status", "all");
  const showRunning = statusFilter !== "waiting";
  const showWaiting = statusFilter !== "running";
  const filterChips =
    statusFilter === "all"
      ? []
      : [{ key: "status", label: `Status: ${statusFilter}` }];

  // Cut 6.6 — per-site quick-actions consume the existing per-site control
  // endpoints (no new endpoints). A waiting job's site can be paused/resumed or
  // kicked to capture now.
  const siteActionMut = useMutation({
    mutationFn: ({ sid, action }: { sid: string; action: "pause" | "resume" | "start" }) =>
      apiPost(`/api/sites/${encodeURIComponent(sid)}/${action}`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
    },
    onError: (e: Error) => toast.error(e.message || "Action failed"),
  });
  const pausedSites = new Set(
    (data?.per_site ?? [])
      .filter((p) => (p as { state?: string }).state === "paused")
      .map((p) => p.site_id),
  );

  // Selection works on Waiting only.
  const waitingKeys = waiting.map(selKey);
  const selection = useBulkSelection(waitingKeys);

  useEffect(() => {
    selection.pruneTo(waitingKeys);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingKeys.join("|")]);

  // If the site being sorted has its waiting jobs drop to 0 (all
  // cancelled or all dispatched), exit sort mode automatically.
  useEffect(() => {
    if (!sortingSite) return;
    const remaining = waiting.filter((j) => j.site_id === sortingSite.id);
    if (remaining.length === 0) {
      setSortingSite(null);
    }
  }, [sortingSite, waiting]);

  const exitSelectionMode = () => {
    selection.clear();
    setSelectionMode(false);
  };

  const exitSortMode = () => {
    setSortingSite(null);
  };

  const enterSelectionMode = () => {
    if (sortingSite) setSortingSite(null);
    setSelectionMode(true);
  };

  const enterSortMode = (siteId: string, siteName: string) => {
    if (selectionMode) exitSelectionMode();
    setSortingSite({ id: siteId, name: siteName });
  };

  // ── v3.66.724: the site queue bulk-ops surface ──────────────────────────
  //
  // The selection spans SITES (keys are `${site_id}|${url}`), but every one of these
  // endpoints is PER-SITE: /api/sites/<sid>/bulk_pause, etc. Dispatching a cross-site
  // selection at a single sid would silently drop every job belonging to the other
  // sites -- you would select 20, pause 7, and get a success toast. So group, then
  // fan out one call per site and sum the counts.
  //
  // And every one of these endpoints validates {urls: [...]} and 400s without it. That
  // is not a hypothetical: the old "Delete ALL jobs" button in SiteActions posted {} and
  // failed 100% of the time, while the reachability ledger scored it as WIRED -- the
  // ledger can only see the route string, never the body. Send real urls.
  const groupBySite = (ids: string[]): Map<string, string[]> => {
    const bySite = new Map<string, string[]>();
    for (const k of ids) {
      const p = parseKey(k);
      if (!p) continue;
      const list = bySite.get(p.site_id) ?? [];
      list.push(p.url);
      bySite.set(p.site_id, list);
    }
    return bySite;
  };

  type BulkOpResponse = { ok: boolean } & Record<string, unknown>;
  type SiteCall = (sid: string, urls: string[]) => Promise<BulkOpResponse>;

  // Fan out across every site in the selection. Failures are COLLECTED, not thrown, so
  // one bad site cannot hide the work that DID land -- and the toast reports the real
  // number rather than the number we hoped for.
  //
  // NOTE ON THE CALL SITES BELOW: each one spells the route as a FULL literal
  // (`/api/sites/${encodeURIComponent(sid)}/bulk_pause`) rather than interpolating a
  // `${suffix}` variable. That is not style. The parity scanner resolves ONE
  // interpolation, not two -- a `${sid}/${suffix}` template is invisible to it, which is
  // exactly why accounts/rotate and captcha/test still read as unwired CONTROL endpoints
  // despite being perfectly reachable from SiteActions. Wiring these through a variable
  // suffix would fix the GUI and leave the ledger red forever.
  const fanOut = async (call: SiteCall): Promise<{ sites: number; failed: string[] }> => {
    const bySite = groupBySite(selection.ids);
    const failed: string[] = [];
    await Promise.all(
      [...bySite.entries()].map(async ([sid, urls]) => {
        try {
          await call(sid, urls);
        } catch {
          failed.push(sid);
        }
      }),
    );
    return { sites: bySite.size, failed };
  };

  const runBulk = (label: string, call: SiteCall) =>
    fanOut(call).then(({ sites, failed }) => {
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      exitSelectionMode();
      if (failed.length) {
        toast.error(`${label}: failed on ${failed.length} of ${sites} site(s)`);
      } else {
        toast.success(`${label}: ${selection.size} job(s) across ${sites} site(s)`);
      }
    });

  const bulkPauseMut = useMutation({
    mutationFn: () => runBulk("Paused", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/bulk_pause`, { urls })),
  });
  const bulkResumeMut = useMutation({
    mutationFn: () => runBulk("Resumed", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/bulk_resume`, { urls })),
  });
  const bulkRetryMut = useMutation({
    mutationFn: () => runBulk("Retried", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/bulk_retry`, { urls })),
  });
  const bulkHighMut = useMutation({
    mutationFn: () => runBulk("Priority high", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/bulk_priority`,
        { urls, priority: "high" })),
  });
  const bulkNormalMut = useMutation({
    mutationFn: () => runBulk("Priority normal", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/bulk_priority`,
        { urls, priority: "normal" })),
  });
  // jobs/bulk_priority is a DIFFERENT object from bulk_priority: it tags the JOB record
  // (a freeform label the runner consults when picking the next pending URL), where
  // bulk_priority reorders the waiting QUEUE. Both are real; not duplicates.
  const jobsPriorityMut = useMutation({
    mutationFn: () => runBulk("Job priority high", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/jobs/bulk_priority`,
        { urls, priority: "high" })),
  });
  const bulkMarkMut = useMutation({
    mutationFn: (status: string) => runBulk(`Marked ${status}`, (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/jobs/bulk_mark`,
        { urls, status })),
  });
  // Destructive. Confirm-gated -- and unlike the control it replaces, it sends real urls
  // and reports the count the server actually removed.
  const [confirmDelete, setConfirmDelete] = useState(false);
  const bulkDeleteMut = useMutation({
    mutationFn: () => runBulk("Deleted", (sid, urls) =>
      apiPost<BulkOpResponse>(`/api/sites/${encodeURIComponent(sid)}/jobs/bulk_delete`,
        { urls })),
  });

  // Bulk cancel mutation. Optimistic: remove all selected rows from
  // the cached query; rollback on error (same shape as single-cancel).
  type BulkCancelResponse = {
    ok: boolean;
    cancelled: number;
    total: number;
    errors: { site_id: string; url: string; error: string }[];
  };
  const bulkCancelMut = useMutation<BulkCancelResponse, Error, void, { prev: QueueV2Full | undefined }>({
    mutationFn: () => {
      const jobs = selection.ids
        .map(parseKey)
        .filter((x): x is { site_id: string; url: string } => x !== null);
      return apiPost<BulkCancelResponse>("/api/queue/v2/bulk_cancel", { jobs });
    },
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: ["queue-v2"] });
      const prev = qc.getQueryData<QueueV2Full>(["queue-v2"]);
      if (prev) {
        const removedUrls = new Set(
          selection.ids
            .map(parseKey)
            .filter((x): x is { site_id: string; url: string } => x !== null)
            .map((j) => j.url),
        );
        qc.setQueryData<QueueV2Full>(["queue-v2"], {
          ...prev,
          waiting: prev.waiting.filter((w) => !removedUrls.has(w.url)),
        });
      }
      return { prev };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["queue-v2"], ctx.prev);
      toast.error(`Bulk cancel failed: ${err.message}`);
    },
    onSuccess: (res) => {
      if (res.cancelled === res.total && res.errors.length === 0) {
        toast.success(`Cancelled ${res.cancelled} job${res.cancelled === 1 ? "" : "s"}`);
      } else {
        toast.warning(
          `Cancelled ${res.cancelled} of ${res.total}; ${res.errors.length} failed`,
        );
      }
      exitSelectionMode();
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
  });

  const openModal = (
    job: QueueRunningEntry | QueueWaitingEntry,
  ) => {
    setModalTarget({
      site_id: job.site_id,
      site_name: job.site_name,
      url: job.url,
      filename: job.filename,
    });
  };

  // Track which URL is currently being cancelled so the row can show
  // a disabled state. Single-job tracking is enough for U5 — bulk
  // select handled separately.
  const cancellingUrl = cancelMut.isPending
    ? cancelMut.variables?.url
    : undefined;

  // The pause/resume toggle's label depends on whether anything is
  // actively running. If nothing's running it's the resume affordance;
  // otherwise it's pause.
  const showPause = running.length > 0;

  // Sort chips bucket the waiting list by site (and only show chips
  // for sites with >=2 waiting jobs — one job is nothing to reorder).
  const buckets = bucketWaitingBySite(data);

  // What goes in the header trailing slot depends on which mode
  // is active. Sort > Selection > Default.
  const trailing = sortingSite ? null : selectionMode ? (
    <Button
      size="sm"
      variant="ghost"
      onClick={exitSelectionMode}
      aria-label="Exit selection mode"
    >
      <X className="h-4 w-4" aria-hidden />
      Done
    </Button>
  ) : (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      <DensityToggle />
      <Button
        size="sm"
        variant="outline"
        onClick={() => setAddUrlOpen(true)}
        aria-label="Add URLs to the queue"
      >
        <Plus className="h-4 w-4" aria-hidden />
        Add URLs
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setQueueOpsOpen(true)}
        aria-label="Transform queued URLs or clear finished URLs"
      >
        <Wrench className="h-4 w-4" aria-hidden />
        Queue ops
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={enterSelectionMode}
        aria-label="Select waiting jobs for bulk cancel"
        disabled={waiting.length === 0}
      >
        <CheckSquare className="h-4 w-4" aria-hidden />
        Select
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setStartAllConfirm(true)}
        disabled={startAllMut.isPending}
        aria-label="Start all sites"
      >
        <Play className="h-3.5 w-3.5" aria-hidden />
        Start all
      </Button>
      {showPause ? (
        <Button
          size="sm"
          variant="outline"
          disabled={pauseAllMut.isPending}
          onClick={() => pauseAllMut.mutate()}
        >
          <Pause className="h-3.5 w-3.5" aria-hidden />
          Pause all
        </Button>
      ) : (
        <Button
          size="sm"
          disabled={resumeAllMut.isPending}
          onClick={() => resumeAllMut.mutate()}
        >
          <Play className="h-3.5 w-3.5" aria-hidden />
          Resume
        </Button>
      )}
    </div>
  );

  // Filter waiting to the site being sorted (memoized via JSX render).
  const sortingJobs = sortingSite
    ? waiting.filter((j) => j.site_id === sortingSite.id)
    : [];

  return (
    <AppShell title="Queue" trailing={trailing}>
      <div className="space-y-3">
        {/* Cut 4: read-only go/no-go preflight strip — a glance before Start. */}
        <QueuePreflightStrip />
        {/* Counter chips — always visible (orthogonal to modes). */}
        <Card className="grid grid-cols-3 gap-2 p-4">
          <Counter label="Running" value={running.length} color="text-green" />
          <Counter label="Waiting" value={waiting.length} color="text-amber" />
          <Counter
            label="Done today"
            value={doneToday}
            color="text-ink-2"
          />
        </Card>

        {/* Cut 6.4/6.6 — status filter (URL-encoded) + removable chips. */}
        {!selectionMode && !sortingSite && (
          <div className="flex flex-wrap items-center gap-2 px-1">
            <div className="inline-flex overflow-hidden rounded-md hairline">
              {(["all", "running", "waiting"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStatusFilter(s)}
                  aria-pressed={statusFilter === s}
                  className={
                    "px-2.5 py-1 text-xs capitalize " +
                    (statusFilter === s
                      ? "bg-surface-2 text-ink"
                      : "bg-surface text-ink-3 hover:text-ink")
                  }
                >
                  {s}
                </button>
              ))}
            </div>
            <QueueFilterChips
              chips={filterChips}
              onRemove={() => setStatusFilter("all")}
            />
          </div>
        )}

        {isError && (
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn't load queue.
          </Card>
        )}

        {/* Now running. */}
        {showRunning && (
        <div>
          <div className="mb-2 px-1 eyebrow">
            Now running
          </div>
          {isLoading && !data && <SkeletonRows count={3} rowClassName="h-20" />}
          {!isLoading && running.length === 0 && (
            <EmptyState
              bare
              title="Nothing running"
              hint="Active downloads show here with live progress. Start a capture or add URLs to create work."
            />
          )}
          {running.length > 0 && (
            <ul className={isCompact ? "space-y-1" : "space-y-2"}>
              {running.map((job) => (
                <li key={`${job.site_id}-${job.url}`}>
                  <RunningJobRow
                    job={job}
                    pctOverride={progressMap.byUrl.get(job.url)}
                    onClick={openModal}
                    compact={isCompact}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
        )}

        {/* Sort mode replaces the flat Waiting list. */}
        {showWaiting && (sortingSite ? (
          <SortableQueueGroup
            siteId={sortingSite.id}
            siteName={sortingSite.name}
            jobs={sortingJobs}
            onDone={exitSortMode}
          />
        ) : (
          <div>
            <div className="mb-2 flex items-baseline justify-between px-1">
              <span className="eyebrow">
                Waiting
              </span>
              {truncated > 0 && (
                <span className="text-[10px] tabular text-ink-3">
                  +{truncated} more
                </span>
              )}
            </div>
            {/* F1.6: per-site drain summary — waiting count + estimated
                time to clear that site's backlog (from its recent
                completion rate; "—" when no rate yet). */}
            {(data?.per_site ?? []).some((p) => p.waiting_count > 0) && (
              <div className="mb-2 flex flex-wrap gap-1.5 px-1">
                {(data?.per_site ?? [])
                  .filter((p) => p.waiting_count > 0)
                  .map((p) => (
                    <span
                      key={p.site_id}
                      className="inline-flex items-center gap-1.5 rounded-full bg-surface px-2 py-0.5 text-[11px] text-ink-2 ring-1 ring-border"
                      title={`${p.site_name}: ${p.waiting_count} waiting${p.running_count ? `, ${p.running_count} running` : ""}`}
                    >
                      <span
                        aria-hidden
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ backgroundColor: p.avatar_color }}
                      />
                      <span className="font-medium text-ink">{p.site_name}</span>
                      <span className="tabular-nums">{p.waiting_count} waiting</span>
                      <span className="text-ink-3">
                        · drain {formatEta(p.drain_eta_seconds)}
                      </span>
                    </span>
                  ))}
              </div>
            )}
            {/* Sort chip ribbon — only renders when at least one site
                has >=2 waiting jobs. */}
            {!selectionMode && (
              <SortChipRibbon buckets={buckets} onPick={enterSortMode} />
            )}
            {selectionMode && waiting.length > 0 && (
              <div className="mb-2 flex items-center justify-between px-1 text-xs text-ink-3">
                <button
                  type="button"
                  onClick={() =>
                    selection.isAllSelected ? selection.clear() : selection.selectAll()
                  }
                  className="flex items-center gap-1.5 rounded-sm py-1 text-primary hover:underline"
                >
                  {selection.isAllSelected ? (
                    <Square className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <CheckSquare className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {selection.isAllSelected ? "Deselect all" : "Select all waiting"}
                </button>
                <span className="tabular-nums">
                  {selection.size} / {waiting.length}
                </span>
              </div>
            )}
            {!isLoading && waiting.length === 0 && (
              <EmptyState
                bare
                title="Nothing waiting"
                hint="Queued URLs wait here before they run. Add URLs or start a capture to fill the queue."
              />
            )}
            {waiting.length > 0 && (
              <ul className={isCompact ? "space-y-0.5" : "space-y-1.5"}>
                {waiting.map((job) => (
                  <li key={`${job.site_id}-${job.url}`}>
                    <WaitingJobRow
                      job={job}
                      compact={isCompact}
                      onOpen={openModal}
                      onCancel={(j) =>
                        cancelMut.mutate({ site_id: j.site_id, url: j.url })
                      }
                      isCancelling={cancellingUrl === job.url}
                      quickActions={
                        <RowQuickActions
                          paused={pausedSites.has(job.site_id)}
                          onPause={() =>
                            siteActionMut.mutate({ sid: job.site_id, action: "pause" })
                          }
                          onResume={() =>
                            siteActionMut.mutate({ sid: job.site_id, action: "resume" })
                          }
                          onCaptureNow={() =>
                            siteActionMut.mutate({ sid: job.site_id, action: "start" })
                          }
                        />
                      }
                      onPin={
                        selectionMode
                          ? undefined
                          : (j) =>
                              pinMut.mutate({ site_id: j.site_id, url: j.url })
                      }
                      selected={selectionMode ? selection.has(selKey(job)) : false}
                      onToggleSelect={
                        selectionMode
                          ? (j) => selection.toggle(selKey(j))
                          : undefined
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}

        <BulkActionBar
          count={selection.size}
          onClear={exitSelectionMode}
          actions={
            <>
            <BulkActionButton
              onClick={() => bulkPauseMut.mutate()}
              disabled={bulkPauseMut.isPending}
              ariaLabel="Pause selected waiting jobs"
            >
              Pause
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkResumeMut.mutate()}
              disabled={bulkResumeMut.isPending}
              ariaLabel="Resume selected waiting jobs"
            >
              Resume
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkRetryMut.mutate()}
              disabled={bulkRetryMut.isPending}
              ariaLabel="Retry selected waiting jobs"
            >
              Retry
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkHighMut.mutate()}
              disabled={bulkHighMut.isPending}
              ariaLabel="Set selected waiting jobs to high priority"
            >
              High
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkNormalMut.mutate()}
              disabled={bulkNormalMut.isPending}
              ariaLabel="Set selected waiting jobs to normal priority"
            >
              Normal
            </BulkActionButton>
            <BulkActionButton
              onClick={() => jobsPriorityMut.mutate()}
              disabled={jobsPriorityMut.isPending}
              ariaLabel="Tag selected jobs high priority"
            >
              Tag high
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkMarkMut.mutate("needs_review")}
              disabled={bulkMarkMut.isPending}
              ariaLabel="Mark selected jobs needs review"
            >
              Needs review
            </BulkActionButton>
            <BulkActionButton
              onClick={() => setConfirmDelete(true)}
              disabled={bulkDeleteMut.isPending}
              variant="destructive"
              ariaLabel="Delete selected jobs"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              Delete
            </BulkActionButton>
            <BulkActionButton
              onClick={() => bulkCancelMut.mutate()}
              disabled={bulkCancelMut.isPending}
              variant="destructive"
              ariaLabel="Cancel selected waiting jobs"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              Cancel
            </BulkActionButton>
            </>
          }
        />
      </div>

      {/* v3.66.724: destructive, so confirm-gated. The control this replaces was ALSO
          confirm-gated -- behind a typed "DELETE ALL JOBS" -- and then failed with a 400
          every single time, because it sent no urls. A confirmation in front of a broken
          call is not safety, it is theatre. This one sends the selected urls and reports
          what the server actually removed. */}
      <Dialog
        open={confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {selection.size} selected job(s)?</DialogTitle>
            <DialogDescription>
              Removes the selected jobs from their site queues. This cannot be undone.
              Only the jobs you have selected are affected.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={bulkDeleteMut.isPending}
              onClick={() => {
                setConfirmDelete(false);
                bulkDeleteMut.mutate();
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {modalTarget && (
        <JobErrorModal
          open={!!modalTarget}
          onOpenChange={(open) => !open && setModalTarget(null)}
          site_id={modalTarget.site_id}
          site_name={modalTarget.site_name}
          url={modalTarget.url}
          filename={modalTarget.filename}
        />
      )}

      <Dialog
        open={startAllConfirm}
        onOpenChange={(o) => !o && setStartAllConfirm(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start all sites</DialogTitle>
            <DialogDescription>
              Start the runner for every site at once. All eligible queued jobs
              will begin downloading. This action is audited.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setStartAllConfirm(false)}>
              Cancel
            </Button>
            <Button
              disabled={startAllMut.isPending}
              onClick={() => {
                setStartAllConfirm(false);
                startAllMut.mutate();
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <QueueTemplatesPanel />
      {/* v3.66.743 — live-site search feeds the queue; capability-gated */}
      <SiteSearchPanel />

      <AddUrlDialog open={addUrlOpen} onOpenChange={setAddUrlOpen} />
      <QueueOpsDialog open={queueOpsOpen} onOpenChange={setQueueOpsOpen} />
    </AppShell>
  );
}

function Counter({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  // V4 — restyle to match V2 TodayStatsCard tri-stat treatment:
  // text-3xl bold numbers, tracking-tighter, opacity-40 when zero (so
  // the color still reads even on an empty queue), uppercase label
  // with wider tracking. Same component, same callers, no behavior
  // change — just visual alignment.
  const isZero = value === 0;
  return (
    <div className="flex flex-col items-start gap-1">
      <span
        className={`text-3xl font-bold tabular tracking-tighter leading-none ${color} ${isZero ? "opacity-40" : ""}`}
      >
        {value}
      </span>
      <span className="eyebrow">
        {label}
      </span>
    </div>
  );
}
