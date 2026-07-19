import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { GitCompare, RefreshCw, Square, FileText, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/format";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api-client";
import type { JobLogResponse, RunsResponse } from "@/lib/api-types";
import { JobTimeline } from "@/components/JobTimeline";
import { cn } from "@/lib/utils";

// Job error modal. Opens when the operator taps a queue row. Shows:
//   - the current status + message (one-line summary)
//   - the last ≤50 event-log entries (timestamped, kind tagged)
//   - Retry button (visible iff the status is terminal: failed,
//     stopped, needs_review)
//   - Cancel button (visible iff the status is pending/running)
//
// v3.64.x D3 follow-up (U5): a Compare flow that lets the operator
// pick a SECOND job, then navigate to /logs/diff?a=…&b=… for the
// side-by-side view. The pick is stashed in sessionStorage so it
// survives the modal close/reopen cycle. The pick is per-tab (not
// per-device) — sessionStorage is the right scope: a comparison
// pick shouldn't bleed across browser tabs.
//
// Why a modal and not a slide-over: matches the mockup; modal is
// also simpler with shadcn Dialog and works well on mobile.

const COMPARE_PICK_KEY = "bd-log-diff-pick";

interface ComparePick {
  site_id: string;
  url: string;
  filename: string;
}

function readPick(): ComparePick | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(COMPARE_PICK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed.site_id === "string" &&
      typeof parsed.url === "string" &&
      typeof parsed.filename === "string"
    ) {
      return parsed;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function writePick(p: ComparePick | null): void {
  if (typeof window === "undefined") return;
  try {
    if (p === null) window.sessionStorage.removeItem(COMPARE_PICK_KEY);
    else window.sessionStorage.setItem(COMPARE_PICK_KEY, JSON.stringify(p));
  } catch {
    /* ignore */
  }
}

export interface JobErrorModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  site_id: string;
  site_name: string;
  url: string;
  filename: string;
}

export function JobErrorModal({
  open,
  onOpenChange,
  site_id,
  site_name,
  url,
  filename,
}: JobErrorModalProps) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [pick, setPick] = useState<ComparePick | null>(() => readPick());

  // Re-read the pick every time the modal opens — the operator may
  // have stashed it from another row's modal in this same session.
  useEffect(() => {
    if (open) setPick(readPick());
  }, [open]);

  const { data, isLoading, isError } = useQuery<JobLogResponse>({
    queryKey: ["job-log", site_id, url],
    queryFn: ({ signal }) =>
      apiGet<JobLogResponse>(
        `/api/queue/v2/job_log?site_id=${encodeURIComponent(site_id)}&url=${encodeURIComponent(url)}&limit=50`,
        signal,
      ),
    enabled: open,
    // Stay fresh while the modal is open — if a retry kicks new events,
    // the operator sees them without manually re-opening.
    refetchInterval: open ? 3000 : false,
    refetchOnWindowFocus: false,
  });

  // Cut 4: match this job's most-recent failed run (list is DESC) to surface
  // the server-classified reason + run timeline. Failed runs only; cached.
  const { data: failedRuns } = useQuery<RunsResponse>({
    queryKey: ["failed-runs"],
    queryFn: ({ signal }) =>
      apiGet<RunsResponse>("/api/runs?status=failed", signal),
    enabled: open,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  const retryMut = useMutation({
    mutationFn: () =>
      apiPost(`/api/sites/${encodeURIComponent(site_id)}/retry_one`, { url }),
    onSuccess: () => {
      toast.success(`Retry queued for ${filename || "job"}`);
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["queue-v2-running"] });
      qc.invalidateQueries({ queryKey: ["job-log", site_id, url] });
    },
    onError: (e: Error) => toast.error(`Retry failed: ${e.message}`),
  });

  const cancelMut = useMutation({
    mutationFn: () =>
      apiPost("/api/queue/v2/cancel", { site_id, url }),
    onSuccess: () => {
      toast.success(`Cancelled ${filename || "job"}`);
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["queue-v2-running"] });
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(`Cancel failed: ${e.message}`),
  });

  const status = data?.current?.status ?? "";
  const isTerminal = ["failed", "stopped", "needs_review"].includes(status);
  const isLive = ["pending", "running"].includes(status);

  // Cut 4: the matched failed run carries the persisted reason_code + run id.
  const matchedRun =
    status === "failed"
      ? failedRuns?.runs?.find((r) => r.url === url && r.site_id === site_id)
      : undefined;

  // U5: Compare flow. Two states:
  //   - No pick stashed → "Pick to compare" button stashes THIS job.
  //   - Pick stashed for ANOTHER job → "Compare with X" CTA navigates
  //     to /logs/diff?a=<pick>&b=<this>.
  //   - Pick stashed for THIS job → "Cancel pick" so the operator can
  //     bail without leaving the modal.
  const pickIsThis = pick !== null && pick.site_id === site_id && pick.url === url;
  const pickIsOther = pick !== null && !pickIsThis;

  const handlePickForCompare = () => {
    const p: ComparePick = { site_id, url, filename };
    writePick(p);
    setPick(p);
    toast(`Picked: ${filename || "job"}`, {
      description: "Open another job's log and tap Compare to diff.",
      duration: 4000,
    });
  };

  const handleClearPick = () => {
    writePick(null);
    setPick(null);
  };

  const handleCompareNow = () => {
    if (!pick) return;
    const aSpec = `${encodeURIComponent(pick.site_id)}:${encodeURIComponent(pick.url)}`;
    const bSpec = `${encodeURIComponent(site_id)}:${encodeURIComponent(url)}`;
    onOpenChange(false);
    // Clear the pick — the diff route's URL captures the state.
    writePick(null);
    setPick(null);
    navigate(`/logs/diff?a=${aSpec}&b=${bSpec}`);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 truncate">
            <FileText className="h-4 w-4 shrink-0" aria-hidden />
            <span className="truncate">{filename || "Job"}</span>
          </DialogTitle>
          <DialogDescription className="truncate">
            {site_name} · <span className="tabular">{status || "unknown"}</span>
          </DialogDescription>
        </DialogHeader>

        {/* U5: Compare-pick banner. Shown when a DIFFERENT job is
         * already stashed as the A-side. Highlighted so it's obvious
         * the operator is in the middle of a compare flow. */}
        {pickIsOther && pick && (
          <div
            role="status"
            aria-live="polite"
            className="hairline flex items-center gap-2 rounded-md bg-primary-soft p-2 text-xs text-primary"
          >
            <GitCompare className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate">
              A: {pick.filename || pick.url}
            </span>
            <button
              type="button"
              onClick={handleClearPick}
              className="rounded-sm p-0.5 text-primary hover:bg-primary/10"
              aria-label="Cancel compare pick"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        )}

        {/* Top-line message (the runner's last status message). The
         * event log below it has more detail; this is the headline. */}
        {data?.current?.message && (
          <div
            className={cn(
              "hairline rounded-md p-3 text-sm",
              status === "failed"
                ? "bg-red-soft text-red"
                : status === "needs_review"
                  ? "bg-amber-soft text-amber-dim"
                  : "bg-surface-2 text-ink-2",
            )}
          >
            {data.current.message}
          </div>
        )}

        {/* Cut 4: server-classified failure reason + run-lifecycle timeline,
         * shown when this job maps to a recorded failed run. */}
        {matchedRun && <JobTimeline runId={matchedRun.id} className="mt-1" />}

        {/* Event log */}
        <div>
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-3">
            Recent events
          </div>
          {isLoading && (
            <div className="space-y-1">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-full" />
            </div>
          )}
          {isError && (
            <p className="text-sm text-red">Couldn't load events.</p>
          )}
          {data && data.events.length === 0 && !isLoading && (
            <p className="text-sm text-ink-3">No events yet.</p>
          )}
          {data && data.events.length > 0 && (
            <ul
              role="log"
              aria-live="polite"
              aria-label="Recent events"
              className="hairline max-h-64 overflow-y-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]"
            >
              {data.events.map((ev, i) => (
                <li
                  key={i}
                  className="flex gap-2 border-b border-hairline py-1 last:border-b-0"
                >
                  <span className="tabular text-ink-3 shrink-0">
                    {formatTimestamp(ev.ts, { style: "time" })}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 uppercase tracking-wide",
                      ev.kind === "error"
                        ? "text-red"
                        : ev.kind === "warning"
                          ? "text-amber"
                          : "text-ink-3",
                    )}
                  >
                    {ev.kind || "info"}
                  </span>
                  <span className="min-w-0 break-words text-ink-2">
                    {ev.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          {/* U5: Compare CTA. When a pick is stashed for a different
           * job, the primary action becomes "Compare now". When no
           * pick is set, expose "Compare with..." which stashes THIS
           * job as the A-side. When the pick IS this job, show a
           * "Picked" indicator so the operator knows they're in
           * pick-pending mode. */}
          {pickIsOther && (
            <Button onClick={handleCompareNow}>
              <GitCompare className="h-3.5 w-3.5" aria-hidden />
              Compare now
            </Button>
          )}
          {!pick && (
            <Button variant="outline" onClick={handlePickForCompare}>
              <GitCompare className="h-3.5 w-3.5" aria-hidden />
              Compare with…
            </Button>
          )}
          {pickIsThis && (
            <Button variant="outline" onClick={handleClearPick}>
              <X className="h-3.5 w-3.5" aria-hidden />
              Cancel pick
            </Button>
          )}
          {isLive && (
            <Button
              variant="destructive"
              disabled={cancelMut.isPending}
              onClick={() => cancelMut.mutate()}
            >
              <Square className="h-3.5 w-3.5" aria-hidden />
              {cancelMut.isPending ? "Cancelling…" : "Cancel"}
            </Button>
          )}
          {isTerminal && (
            <Button
              disabled={retryMut.isPending}
              onClick={() => retryMut.mutate()}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {retryMut.isPending ? "Retrying…" : "Retry"}
            </Button>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

