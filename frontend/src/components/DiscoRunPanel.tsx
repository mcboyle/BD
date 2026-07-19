// A-DISCO cut 4b -- operator run-now control + run history.
//
// The daily disco.scheduled_run task is the autonomous path (gated by the
// A-DISCO auto-discovery toggle in this same Automation section). This panel is
// the attended path: a Run-now button for the OPV live-verify, so the operator
// does not have to wait for the daily task. Run-now FORCES a pass past the daily
// toggle (an explicit operator action) -- but the master off-switch still
// dominates, per-site discovery config still gates which sites run, and the
// bounded budget still applies. It never flips the daily toggle.
//
//   POST /api/discovery/disco/run   -- run one pass now (force)
//   GET  /api/discovery/disco/runs  -- persisted run history
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";

interface DiscoRun {
  id?: number;
  ts?: number;
  site_id?: string;
  root?: string;
  host?: string;
  enumerated?: number;
  enqueued?: number;
  review?: number;
  reject?: number;
  halted?: number;
  halt_reason?: string;
}
interface DiscoRunsResp {
  runs: DiscoRun[];
}
interface RunResult {
  ran: boolean;
  reason: string;
  sites: number;
  runs: DiscoRun[];
}

function fmtWhen(ts?: number): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "unknown";
  }
}

export function DiscoRunPanel() {
  const qc = useQueryClient();

  const history = useQuery({
    queryKey: ["disco-runs"],
    queryFn: () => apiGet<DiscoRunsResp>("/api/discovery/disco/runs?limit=5"),
  });

  const run = useMutation({
    mutationFn: () => apiPost<RunResult>("/api/discovery/disco/run", {}),
    onSuccess: (r) => {
      if (r.ran) {
        const enq = (r.runs || []).reduce((n, x) => n + (x.enqueued || 0), 0);
        toast.success(
          `Discovery ran on ${r.sites} site${r.sites === 1 ? "" : "s"}, ${enq} queued`,
        );
      } else {
        toast.message(`Discovery did not run (${r.reason || "unknown"})`);
      }
      qc.invalidateQueries({ queryKey: ["disco-runs"] });
    },
    onError: (e) => toast.error(`Run failed: ${String(e)}`),
  });

  const last = history.data?.runs?.[0] || null;

  return (
    <Card className="space-y-3 p-4">
      <div>
        <div className="text-[13px] font-medium text-ink">A-DISCO run now</div>
        <p className="text-[12px] text-ink-3">
          Runs one discovery pass on demand for every site whose per-site discovery
          is enabled, for the live-verify, without waiting for the daily task. It
          does not flip the daily toggle, and the master off-switch above still
          overrides it.
        </p>
      </div>

      <div className="rounded-md hairline p-2 text-[12px] text-ink-3">
        {last && last.ts ? (
          <span>
            last run: {last.site_id || "site"} &middot; enumerated {last.enumerated ?? 0} /
            queued {last.enqueued ?? 0} / review {last.review ?? 0} &middot;{" "}
            {fmtWhen(last.ts)}
          </span>
        ) : (
          <span>last run: never run yet</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Running..." : "Run now"}
        </Button>
        <span className="text-[12px] text-ink-3">
          Runs a discovery pass now (even if the daily toggle is off).
        </span>
      </div>
    </Card>
  );
}
