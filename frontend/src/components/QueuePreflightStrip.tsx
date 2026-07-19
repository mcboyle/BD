import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, AlertTriangle, XCircle, Loader2 } from "lucide-react";

import { apiGet } from "@/lib/api-client";
import type {
  QueuePreflightResponse,
  OiCheck,
  OiCheckStatus,
} from "@/lib/api-types";
import { StatusPill, type PillTone } from "@/components/StatusPill";
import { cn } from "@/lib/utils";

// Cut 4 — queue preflight strip.
//
// A read-only go/no-go banner above the queue. It calls
// GET /api/queue/preflight (which aggregates auth health, daily budget,
// selector drift, runner status, the review backlog, plus a download-dir
// writability check and a duplicate estimate) and renders one pill per check
// with an overall Ready / Not ready headline. It never mutates anything — it's
// a glance before you hit Start.

const TONE: Record<OiCheckStatus, PillTone> = {
  ok: "green",
  warn: "amber",
  fail: "red",
};

function checkIcon(status: OiCheckStatus) {
  if (status === "fail") return <XCircle className="h-3.5 w-3.5" aria-hidden />;
  if (status === "warn")
    return <AlertTriangle className="h-3.5 w-3.5" aria-hidden />;
  return <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />;
}

export interface QueuePreflightStripProps {
  className?: string;
  /** Poll interval ms; 0 disables polling (default 30s). */
  refetchMs?: number;
}

export function QueuePreflightStrip({
  className,
  refetchMs = 30_000,
}: QueuePreflightStripProps) {
  const { data, isLoading, isError } = useQuery<QueuePreflightResponse>({
    queryKey: ["queue-preflight"],
    queryFn: ({ signal }) =>
      apiGet<QueuePreflightResponse>("/api/queue/preflight", signal),
    refetchInterval: refetchMs || false,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  if (isLoading) {
    return (
      <div
        className={cn(
          "hairline flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs text-ink-3",
          className,
        )}
        aria-busy
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        Running preflight…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div
        role="status"
        className={cn(
          "hairline rounded-lg bg-surface-2 px-3 py-2 text-xs text-ink-3",
          className,
        )}
      >
        Preflight unavailable.
      </div>
    );
  }

  const ready = data.ready;
  const failing = data.checks.filter((c) => c.status === "fail").length;
  const warning = data.checks.filter((c) => c.status === "warn").length;

  return (
    <section
      aria-label="Queue preflight"
      className={cn(
        "hairline rounded-lg p-3",
        ready ? "bg-green-soft/40" : "bg-red-soft/40",
        className,
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        {ready ? (
          <CheckCircle2 className="h-4 w-4 text-green" aria-hidden />
        ) : (
          <XCircle className="h-4 w-4 text-red" aria-hidden />
        )}
        <span
          className={cn(
            "text-sm font-semibold",
            ready ? "text-green" : "text-red",
          )}
        >
          {ready ? "Ready to run" : "Not ready"}
        </span>
        {(failing > 0 || warning > 0) && (
          <span className="text-xs text-ink-3">
            {failing > 0 && `${failing} blocking`}
            {failing > 0 && warning > 0 && " · "}
            {warning > 0 && `${warning} warning${warning === 1 ? "" : "s"}`}
          </span>
        )}
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {data.checks.map((c: OiCheck) => (
          <li key={c.key}>
            <StatusPill
              tone={TONE[c.status]}
              size="sm"
              leadingIcon={checkIcon(c.status)}
              ariaLabel={`${c.label}: ${c.status}${c.detail ? ` — ${c.detail}` : ""}`}
            >
              <span title={c.detail || undefined}>{c.label}</span>
            </StatusPill>
          </li>
        ))}
      </ul>
    </section>
  );
}
