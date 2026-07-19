import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { formatBytes } from "@/lib/format";
import { Card } from "@/components/ui/card";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DangerZone } from "@/components/ui/DangerZone";
import { Input } from "@/components/ui/input";
import { apiPost } from "@/lib/api-client";
import { useStorageInventory } from "@/hooks/useLibraryOps";
import type { OkResult } from "@/lib/api-types";

// GUI parity (179) — Storage Rebalance keystone + reuse.
//   * P1: /api/rebalance plan→execute. P2: /api/storage_rebalance plan→execute.
// Both families share one structured-payload pattern (this RebalancePanel):
// plan is non-destructive (no confirm); execute defaults to a dry run; the
// live (dry_run:false) run requires a destructive yes/no confirm (No default). Surface-only — never
// reimplements storage_rebalance; never one-click.

type RebalanceMove = {
  from?: string;
  to?: string;
  filename?: string;
  size_bytes?: number;
  reason?: string;
};
type RebalancePlan = {
  strategy?: string;
  moves?: RebalanceMove[];
  summary?: unknown;
  warning?: string;
  error?: string;
};
type ExecResult = OkResult & {
  moved?: number;
  dry_run?: boolean;
  results?: unknown;
};

function RebalancePanel({
  title,
  description,
  planEndpoint,
  executeEndpoint,
}: {
  title: string;
  description: string;
  planEndpoint: string;
  executeEndpoint: string;
}) {
  const [paths, setPaths] = useState("");
  const [strategy, setStrategy] = useState("even_fill");
  const [maxMoves, setMaxMoves] = useState("100");
  const [plan, setPlan] = useState<RebalancePlan | null>(null);
  const [result, setResult] = useState<ExecResult | null>(null);
  const [confirming, setConfirming] = useState(false);

  const pathList = () =>
    paths
      .split("\n")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

  const planMut = useMutation<RebalancePlan, Error, void>({
    mutationFn: () => {
      const mx = parseInt(maxMoves, 10);
      return apiPost<RebalancePlan>(planEndpoint, {
        paths: pathList(),
        strategy,
        max_moves: mx > 0 ? mx : 100,
      });
    },
    onSuccess: (p) => {
      setPlan(p);
      setResult(null);
      if (p.error) toast.error(p.error);
      else if (p.warning) toast.warning(p.warning);
      else toast.success(`Plan ready (${(p.moves ?? []).length} move(s))`);
    },
    onError: (e) => toast.error(e.message),
  });

  const execMut = useMutation<ExecResult, Error, boolean>({
    mutationFn: (dry) => apiPost<ExecResult>(executeEndpoint, { plan, dry_run: dry }),
    onSuccess: (res, dry) => {
      setResult(res);
      if (res.ok === false) toast.error(res.error || "execute failed");
      else toast.success(dry ? "Dry run complete — no files moved" : "Rebalance executed");
    },
    onError: (e) => toast.error(e.message),
  });

  const moveCount = (plan?.moves ?? []).length;
  const planReady = !!plan && moveCount > 0;
  const busy = planMut.isPending || execMut.isPending;

  const generate = () => {
    if (pathList().length === 0) {
      toast.error("Enter at least one storage path");
      return;
    }
    planMut.mutate();
  };
  const dryRun = () => {
    if (!planReady) {
      toast.error("Generate a non-empty plan first");
      return;
    }
    execMut.mutate(true);
  };
  const armLive = () => {
    if (!planReady) {
      toast.error("Generate a non-empty plan first");
      return;
    }
    setConfirming(true);
  };
  const confirmLive = () => {
    setConfirming(false);
    execMut.mutate(false);
  };

  return (
    <Card className="mt-4 p-4">
      <WorkflowPage
        purpose={
          <>
            <h2 className="section-head">{title}</h2>
            <p className="text-xs text-ink-3">{description}</p>
          </>
        }
        inputs={
          <>
            <textarea
              className="min-h-[88px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
              placeholder="one storage path per line"
              value={paths}
              onChange={(e) => setPaths(e.target.value)}
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <label className="text-sm text-ink-3">
                strategy{" "}
                <select
                  className="ml-1 rounded-md border border-border bg-black/40 px-2 py-1 text-sm text-foreground"
                  value={strategy}
                  onChange={(e) => setStrategy(e.target.value)}
                >
                  <option value="even_fill">even_fill</option>
                  <option value="tier_by_age">tier_by_age</option>
                  <option value="fill_first">fill_first</option>
                </select>
              </label>
              <label className="text-sm text-ink-3">
                max moves{" "}
                <Input
                  type="number"
                  min={1}
                  className="ml-1 inline-block max-w-[100px]"
                  value={maxMoves}
                  onChange={(e) => setMaxMoves(e.target.value)}
                />
              </label>
              <Button variant="outline" disabled={busy} onClick={generate}>
                Generate plan
              </Button>
              {/* Dry run stays OUTSIDE the danger zone — it moves nothing. */}
              <Button variant="outline" disabled={busy || !planReady} onClick={dryRun}>
                Dry run (safe)
              </Button>
            </div>
          </>
        }
        plan={
          <>
            <p className="text-sm text-ink-3">
              {plan
                ? `strategy=${plan.strategy ?? "?"} · ${moveCount} move(s)` +
                  (plan.warning ? ` · warning: ${plan.warning}` : "") +
                  (plan.error ? ` · error: ${plan.error}` : "")
                : "No plan yet."}
            </p>
            {/* F3.5 — readable suggested-moves list (surface-only; the raw
                plan JSON stays below as the verbatim detail). Non-destructive:
                this only renders what plan_rebalance already returned. */}
            {planReady && (
              <div className="space-y-1">
                <div className="text-xs font-semibold text-ink-3">
                  Suggested moves ({moveCount})
                </div>
                <ul className="max-h-48 divide-y divide-border overflow-auto rounded border border-border bg-black/20 text-xs">
                  {(plan?.moves ?? []).map((m, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-x-2 px-2 py-1">
                      <span className="font-mono text-foreground">{m.filename ?? "(file)"}</span>
                      <span className="text-ink-3">
                        {m.from ?? "?"} <span aria-hidden>→</span> {m.to ?? "?"}
                      </span>
                      {typeof m.size_bytes === "number" && (
                        <span className="text-ink-3">
                          {formatBytes(m.size_bytes)}
                        </span>
                      )}
                      {m.reason && <span className="text-ink-2/70">{m.reason}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plan && (
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
                {JSON.stringify(plan, null, 2)}
              </pre>
            )}
          </>
        }
        danger={
          <DangerZone
            title="Execute rebalance"
            warning="The live run MOVES files on disk and cannot be auto-undone. Generate a plan and dry-run it first."
          >
            <Button variant="destructive" disabled={busy || !planReady} onClick={armLive}>
              Execute live (moves files)
            </Button>
          </DangerZone>
        }
        result={
          result ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
              {JSON.stringify(result, null, 2)}
            </pre>
          ) : undefined
        }
      />

      <ConfirmDialog
        open={confirming}
        title="Confirm live rebalance"
        target={`${moveCount} file move(s)`}
        consequence="This MOVES the planned files on disk and cannot be auto-undone."
        confirmLabel="Yes, execute live"
        cancelLabel="No, cancel"
        onConfirm={confirmLive}
        onCancel={() => setConfirming(false)}
      />
    </Card>
  );
}

// ── T3 (v3.66.207): per-disk inventory — compute-only POST, no gate. ──
function InventoryPanel() {
  const inventory = useStorageInventory();
  const [paths, setPaths] = useState("");
  const parsed = paths
    .split(/[\n,]+/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <Card className="mt-4 p-4">
      <h2 className="section-head">Disk inventory</h2>
      <p className="mb-2 text-sm text-ink-3">
        Per-disk usage for a set of paths via /api/storage_rebalance/inventory — read-only; feeds the
        plan above.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Paths (comma separated)"
          value={paths}
          onChange={(e) => setPaths(e.target.value)}
          className="max-w-xl"
        />
        <Button
          variant="outline"
          disabled={parsed.length === 0 || inventory.isPending}
          onClick={() =>
            inventory.mutate({ paths: parsed }, { onError: (e) => toast.error(e.message) })
          }
        >
          Inventory
        </Button>
      </div>
      {inventory.data?.inventory && (
        <ul className="mt-2 divide-y divide-border">
          {inventory.data.inventory.map((d, i) => (
            <li key={i} className="py-1.5 text-sm">
              <code className="text-xs">{String(d.path ?? d.mount ?? `disk ${i}`)}</code>{" "}
              <span className="text-ink-3">
                {Object.entries(d)
                  .filter(([k, v]) => k !== "path" && k !== "mount" && typeof v !== "object")
                  .map(([k, v]) => `${k}: ${String(v)}`)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function RebalanceCenter() {
  return (
    <AppShell
      title="Storage Rebalance"
      subtitle="Plan · dry-run · execute file moves across storage paths"
    >
      <GatedWriteBanner>
        <b>Plan</b> is non-destructive; <b>Execute</b> defaults to a dry run; the
        live run moves files on disk and requires an explicit yes/no confirmation (No default) — nothing fires on a single
        click; every request is audited by the underlying endpoint. <b>Needs operator click-through
        validation.</b>
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mb-3">
        Even out where downloaded files live across your storage paths. Plan
        shows the moves it would make without touching anything; Execute defaults
        to a dry run, and only a confirmed live run actually moves files on disk.
      </Callout>

      <RebalancePanel
        title="Path rebalance"
        description="Rebalance across explicit paths via /api/rebalance."
        planEndpoint="/api/rebalance/plan"
        executeEndpoint="/api/rebalance/execute"
      />
      <RebalancePanel
        title="Storage rebalance"
        description="Per-disk storage rebalance via /api/storage_rebalance (same plan→execute contract)."
        planEndpoint="/api/storage_rebalance/plan"
        executeEndpoint="/api/storage_rebalance/execute"
      />
      <InventoryPanel />
    </AppShell>
  );
}
