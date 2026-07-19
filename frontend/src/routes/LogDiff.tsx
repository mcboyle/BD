import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { FileText } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet } from "@/lib/api-client";
import type { JobLogDiffResponse, JobLogDiffSide, JobLogEvent } from "@/lib/api-types";
import { cn } from "@/lib/utils";
import { formatTimestamp } from "@/lib/format";

// /m2/logs/diff route — side-by-side event log comparison between
// two jobs.
//
// Query: ?a=<site_id>:<url>&b=<site_id>:<url>
// Backend: /api/queue/v2/job_log_diff (U5 endpoint).
//
// Layout: on mobile (default), two stacked cards with the unified
// diff between them (collapsed by default). On wide screens, the
// two job logs render side-by-side with scroll-syncing between
// them so the operator can compare an event timeline at a glance.
// The unified diff sits below both, monospace, with red/green
// shading per the standard +/− line prefix.
//
// Why not implement diff client-side: the SPA would need a diff
// library (~10KB) and recomputing on every render would chew
// frames on long logs. The backend already had difflib.unified_diff
// available; cheaper to do it there and ship the lines.

export function LogDiff() {
  const [params] = useSearchParams();
  const a = params.get("a") || "";
  const b = params.get("b") || "";
  const hasBoth = a !== "" && b !== "";

  const { data, isLoading, isError } = useQuery<JobLogDiffResponse>({
    queryKey: ["job-log-diff", a, b],
    queryFn: ({ signal }) =>
      apiGet<JobLogDiffResponse>(
        `/api/queue/v2/job_log_diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&limit=200`,
        signal,
      ),
    enabled: hasBoth,
    refetchOnWindowFocus: false,
  });

  return (
    <AppShell
      title="Compare logs"
      backTo={{ to: "/queue", label: "Back to queue" }}
      breadcrumb="Logs › Diff"
    >
      <Callout tone="info" title="What appears here" className="mb-3">
        Compare two log snapshots to see changed lines, warnings, and operator-visible differences.
      </Callout>
      {!hasBoth && (
        <Card className="border-amber bg-amber-soft p-3 text-sm text-amber-dim" role="alert">
          Missing 'a' or 'b' query parameter. Open this view from the Queue
          tab's Compare flow.
        </Card>
      )}
      {hasBoth && isError && (
        <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
          Couldn't load the diff.
        </Card>
      )}
      {hasBoth && isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}
      {hasBoth && data && (
        <DiffBody data={data} />
      )}
    </AppShell>
  );
}

function DiffBody({ data }: { data: JobLogDiffResponse }) {
  // Scroll-sync between the two side panes on wide screens. We
  // sync raw scrollTop because both panes render the same number
  // of events the same way (one row each). On mobile the panes
  // stack, so the syncing is a no-op (the listeners still attach
  // but never fire on the hidden ref pair).
  const aRef = useRef<HTMLUListElement>(null);
  const bRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const a = aRef.current;
    const b = bRef.current;
    if (!a || !b) return;
    let lock = false;
    const sync = (from: HTMLUListElement, to: HTMLUListElement) => () => {
      if (lock) return;
      lock = true;
      to.scrollTop = from.scrollTop;
      // Release on next frame so the inverse handler doesn't bounce.
      requestAnimationFrame(() => {
        lock = false;
      });
    };
    const onA = sync(a, b);
    const onB = sync(b, a);
    a.addEventListener("scroll", onA);
    b.addEventListener("scroll", onB);
    return () => {
      a.removeEventListener("scroll", onA);
      b.removeEventListener("scroll", onB);
    };
  }, []);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <SidePanel side="a" data={data.a} listRef={aRef} />
        <SidePanel side="b" data={data.b} listRef={bRef} />
      </div>
      <UnifiedDiff lines={data.diff} />
    </div>
  );
}

function SidePanel({
  side,
  data,
  listRef,
}: {
  side: "a" | "b";
  data: JobLogDiffSide;
  listRef: React.RefObject<HTMLUListElement>;
}) {
  const labelColor = side === "a" ? "text-primary" : "text-amber-dim";
  return (
    <Card className="flex min-w-0 flex-col p-3">
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide",
            side === "a" ? "bg-primary-soft" : "bg-amber-soft",
            labelColor,
          )}
        >
          {side.toUpperCase()}
        </span>
        <FileText className="h-3.5 w-3.5 shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 truncate text-sm font-medium">
          {data.current?.filename || data.url || "Unknown job"}
        </span>
      </div>
      {!data.ok && (
        <p className="text-xs text-red" role="alert">
          {data.error || "Couldn't load this side."}
        </p>
      )}
      {data.ok && data.current?.message && (
        <div
          className={cn(
            "hairline mb-2 rounded-md p-2 text-[11px]",
            data.current.status === "failed"
              ? "bg-red-soft text-red"
              : data.current.status === "needs_review"
                ? "bg-amber-soft text-amber-dim"
                : "bg-surface-2 text-ink-2",
          )}
        >
          {data.current.message}
        </div>
      )}
      <ul
        ref={listRef}
        role="log"
        aria-label={`Side ${side.toUpperCase()} events`}
        className="hairline max-h-72 overflow-y-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]"
      >
        {data.events.length === 0 && (
          <li className="text-ink-3">No events.</li>
        )}
        {data.events.map((ev, i) => (
          <EventLine key={`${side}-${i}`} ev={ev} />
        ))}
      </ul>
    </Card>
  );
}

function EventLine({ ev }: { ev: JobLogEvent }) {
  return (
    <li className="flex gap-2 border-b border-hairline py-1 last:border-b-0">
      <span className="tabular shrink-0 text-ink-3">{formatTimestamp(ev.ts, { style: "time" })}</span>
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
      <span className="min-w-0 break-words text-ink-2">{ev.message}</span>
    </li>
  );
}

function UnifiedDiff({ lines }: { lines: string[] }) {
  if (lines.length === 0) {
    return (
      <Card className="p-3 text-center text-sm text-ink-3">
        No differences.
      </Card>
    );
  }
  return (
    <Card className="p-3">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-ink-3">
        Unified diff
      </div>
      <pre className="hairline max-h-96 overflow-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]">
        {lines.map((line, i) => {
          const cls = diffLineClass(line);
          return (
            <div key={i} className={cls}>
              {line || "\u00a0"}
            </div>
          );
        })}
      </pre>
    </Card>
  );
}

function diffLineClass(line: string): string {
  // Match the standard unified_diff prefixes: ---, +++, @@, +, -.
  // The leading +/- are the only ones we colorize for at-a-glance reading.
  if (line.startsWith("+++") || line.startsWith("---")) return "text-ink-3";
  if (line.startsWith("@@")) return "text-primary";
  if (line.startsWith("+")) return "text-green";
  if (line.startsWith("-")) return "text-red";
  return "text-ink-2";
}

