// Item 4 — F3.2 drift-repair sweep: status + control surface.
//
// The daily drift->AI-repair sweep lands REVIEW-ONLY drafts (never enables). This
// panel makes it observable + operable: a labelled toggle, a "last sweep"
// summary, a Run-now button (force = run without flipping the daily automation),
// and the count of review-only drafts it has produced.
//
//   GET  /api/automation/drift_repair        — status (enabled, last_run, pending)
//   POST /api/automation/drift_repair/run     — run now (force)
//   POST /api/automation/drift_repair/toggle  — set the daily toggle
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";

interface LastRun {
  ts: number;
  ran: boolean;
  considered: number;
  repaired: number;
  skipped: number;
  site_ids: string[];
}
interface DriftStatus {
  ok: boolean;
  enabled: boolean;
  last_run: LastRun | null;
  drafts_pending: number;
}
interface RunResult {
  ok: boolean;
  summary: { ran: boolean; considered?: number; repaired?: number; skipped?: number; reason?: string };
}

function fmtWhen(ts: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

export function DriftRepairPanel() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["drift-repair-status"],
    queryFn: () => apiGet<DriftStatus>("/api/automation/drift_repair"),
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      apiPost<{ ok: boolean; enabled: boolean }>(
        "/api/automation/drift_repair/toggle",
        { enabled },
      ),
    onSuccess: (r) => {
      toast.success(`Daily drift repair ${r.enabled ? "enabled" : "disabled"}`);
      qc.invalidateQueries({ queryKey: ["drift-repair-status"] });
    },
    onError: (e) => toast.error(`Toggle failed: ${String(e)}`),
  });

  const run = useMutation({
    mutationFn: () =>
      apiPost<RunResult>("/api/automation/drift_repair/run", { force: true }),
    onSuccess: (r) => {
      const s = r.summary || {};
      if (s.ran) {
        toast.success(
          `Sweep ran — considered ${s.considered ?? 0}, repaired ${s.repaired ?? 0}, skipped ${s.skipped ?? 0}`,
        );
      } else {
        toast.message(`Sweep did not run (${s.reason || "unknown"})`);
      }
      qc.invalidateQueries({ queryKey: ["drift-repair-status"] });
    },
    onError: (e) => toast.error(`Run failed: ${String(e)}`),
  });

  const d = status.data;
  const last = d?.last_run || null;

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[13px] font-medium text-ink">Drift repair (daily sweep)</div>
          <p className="text-[12px] text-ink-3">
            When a site&apos;s selectors go stale, the sweep asks the AI to propose
            fixes and lands them as <strong>review-only drafts</strong> — it never
            enables a template. Safe to leave on.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-[12px] text-ink-2">
          <input
            type="checkbox"
            checked={!!d?.enabled}
            disabled={toggle.isPending || status.isLoading}
            onChange={(e) => toggle.mutate(e.target.checked)}
          />
          Daily
        </label>
      </div>

      <div className="rounded-md hairline p-2 text-[12px] text-ink-3">
        {last && last.ts ? (
          <span>
            last sweep: considered {last.considered} / repaired {last.repaired} / skipped{" "}
            {last.skipped} · {fmtWhen(last.ts)}
          </span>
        ) : (
          <span>last sweep: never run yet</span>
        )}
        {typeof d?.drafts_pending === "number" && (
          <span> · {d.drafts_pending} review draft{d.drafts_pending === 1 ? "" : "s"} pending</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Running…" : "Run now"}
        </Button>
        <span className="text-[12px] text-ink-3">
          Runs the sweep on demand (even if the daily toggle is off).
        </span>
      </div>
    </Card>
  );
}
