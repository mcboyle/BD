// AiTeach — differential selector-repair operator surface (Phase 29).
//
// Standalone propose → review → commit panel for the LAST operator-facing
// write that had no SPA surface. Two endpoints, both full /api/ literals so
// the parity scanner credits them spa_wired:
//
//   * POST /api/ai/diff_repair                     — PROPOSE. Stateless; asks
//       the model for replacement selectors. Returns {ok, repairs:[{old_selector,
//       new_selector, role, reasoning, confidence}], removed:[...]}. Commits
//       NOTHING. {ok:false,error:"AI assist is disabled"} when AI is off;
//       {ok:true,repairs:[],note} on empty input.
//   * POST /api/sites/<sid>/learned/apply_repairs  — COMMIT. Applies the
//       operator-ACCEPTED subset into learned.download[role] (replace in place;
//       old must already exist). Supports dry_run preview. Persists + propagates
//       to the live runner server-side.
//
// The in-page Playwright teach overlay (learn.py) verifies new selectors against
// the live target DOM; a SPA route has no target DOM, so here the operator pastes
// the broken/working selectors + a DOM excerpt, reviews each proposal, and ticks
// the ones to commit. That review IS the manual handoff at the write boundary —
// nothing goes from model straight into a site's config.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { DriftRepairPanel } from "@/components/DriftRepairPanel";
import { Button } from "@/components/ui/button";
import { DangerZone } from "@/components/ui/DangerZone";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { apiGet, apiPost } from "@/lib/api-client";

interface SiteRow {
  site_id: string;
  name: string;
}
interface SitesV2 {
  ok: boolean;
  sites: SiteRow[];
}

type Role = "row_selectors" | "trigger_selectors";

interface Repair {
  old_selector: string;
  new_selector: string;
  role: Role;
  reasoning: string;
  confidence: number;
}
interface DiffRepairResult {
  ok: boolean;
  error?: string;
  note?: string;
  repairs?: Repair[];
  removed?: string[];
  latency_ms?: number;
  model?: string;
}
interface ApplyResult {
  ok: boolean;
  applied: { role: Role; old_selector: string; new_selector: string }[];
  removed: { role: Role; selector: string }[];
  rejected: { reason: string; old_selector?: string; role?: string }[];
  dry_run: boolean;
  count: number;
}

// One selector per line → trimmed non-empty list.
function lines(s: string): string[] {
  return s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function AiTeach() {
  const [siteId, setSiteId] = useState("");
  const [broken, setBroken] = useState("");
  const [working, setWorking] = useState("");
  const [domExcerpt, setDomExcerpt] = useState("");
  const [pageUrl, setPageUrl] = useState("");

  const [repairs, setRepairs] = useState<Repair[]>([]);
  const [removed, setRemoved] = useState<string[]>([]);
  const [accepted, setAccepted] = useState<Record<number, boolean>>({});

  const sitesQ = useQuery<SitesV2>({
    queryKey: ["sites-v2-aiteach"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
  });

  // ── PROPOSE — POST /api/ai/diff_repair ───────────────────────────────────
  const proposeM = useMutation({
    mutationFn: () =>
      apiPost<DiffRepairResult>("/api/ai/diff_repair", {
        broken_selectors: lines(broken),
        working_selectors: lines(working),
        dom_excerpt: domExcerpt.slice(0, 16000),
        page_url: pageUrl.trim(),
      }),
    onSuccess: (res) => {
      if (!res.ok) {
        toast.error(res.error || "AI returned an error");
        setRepairs([]);
        setRemoved([]);
        return;
      }
      const reps = res.repairs || [];
      setRepairs(reps);
      setRemoved(res.removed || []);
      // Default-accept anything at high confidence; operator can untick.
      const init: Record<number, boolean> = {};
      reps.forEach((r, i) => (init[i] = r.confidence >= 70));
      setAccepted(init);
      if (res.note) toast.info(res.note);
      else
        toast.success(
          `${reps.length} proposal${reps.length === 1 ? "" : "s"}` +
            (res.latency_ms ? ` · ${res.latency_ms}ms` : ""),
        );
    },
    onError: (e: unknown) => toast.error(`Propose failed: ${String(e)}`),
  });

  // ── COMMIT — POST /api/sites/<sid>/learned/apply_repairs ──────────────────
  function acceptedRepairs(): Repair[] {
    return repairs.filter((_, i) => accepted[i]);
  }

  const commitM = useMutation({
    mutationFn: (dryRun: boolean) =>
      apiPost<ApplyResult>(`/api/sites/${siteId}/learned/apply_repairs`, {
        repairs: acceptedRepairs().map((r) => ({
          old_selector: r.old_selector,
          new_selector: r.new_selector,
          role: r.role,
        })),
        removed,
        dry_run: dryRun,
      }),
    onSuccess: (res, dryRun) => {
      const verb = dryRun ? "Would apply" : "Applied";
      toast.success(
        `${verb} ${res.count} repair${res.count === 1 ? "" : "s"}` +
          (res.removed.length ? ` · ${res.removed.length} removed` : "") +
          (res.rejected.length ? ` · ${res.rejected.length} rejected` : ""),
      );
      if (!dryRun) {
        setRepairs([]);
        setRemoved([]);
        setAccepted({});
      }
    },
    onError: (e: unknown) => toast.error(`Commit failed: ${String(e)}`),
  });

  const canPropose = lines(broken).length > 0 && !proposeM.isPending;
  const acceptedCount = acceptedRepairs().length;
  const canCommit =
    !!siteId && (acceptedCount > 0 || removed.length > 0) && !commitM.isPending;

  return (
    <AppShell
      title="AI selector repair"
      subtitle="Propose · review · commit replacement selectors after a redesign"
    >
      <div className="mx-auto max-w-3xl p-4">
        <div className="mb-4">
          <DriftRepairPanel />
        </div>
        <WorkflowPage
          purpose={
            <Callout tone="info" title="What this page does">
          Ask the model for replacement selectors when a site redesign breaks
          the learned ones, review each proposal, then commit the accepted set
          to the site. Nothing is applied without your review.
            </Callout>
          }
          inputs={
        <Card className="space-y-3 p-4">
          <label className="block">
            <span className="text-[12px] font-medium text-ink">Site</span>
            <select
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface-2 p-2 text-sm text-ink"
            >
              <option value="">Select a site…</option>
              {(sitesQ.data?.sites || []).map((s) => (
                <option key={s.site_id} value={s.site_id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[12px] font-medium text-ink">
              Broken selectors{" "}
              <span className="text-ink-3">(one per line, required)</span>
            </span>
            <textarea
              value={broken}
              onChange={(e) => setBroken(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded border border-line bg-surface-2 p-2 font-mono text-[12px] text-ink"
              placeholder=".old-row a.title"
            />
          </label>

          <label className="block">
            <span className="text-[12px] font-medium text-ink">
              Working selectors{" "}
              <span className="text-ink-3">(optional context)</span>
            </span>
            <textarea
              value={working}
              onChange={(e) => setWorking(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-line bg-surface-2 p-2 font-mono text-[12px] text-ink"
            />
          </label>

          <label className="block">
            <span className="text-[12px] font-medium text-ink">
              DOM excerpt{" "}
              <span className="text-ink-3">(≤16k chars of the new page)</span>
            </span>
            <textarea
              value={domExcerpt}
              onChange={(e) => setDomExcerpt(e.target.value)}
              rows={5}
              className="mt-1 w-full rounded border border-line bg-surface-2 p-2 font-mono text-[11px] text-ink"
            />
          </label>

          <label className="block">
            <span className="text-[12px] font-medium text-ink">
              Page URL <span className="text-ink-3">(optional)</span>
            </span>
            <input
              value={pageUrl}
              onChange={(e) => setPageUrl(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface-2 p-2 text-sm text-ink"
            />
          </label>

          <Button onClick={() => proposeM.mutate()} disabled={!canPropose}>
            {proposeM.isPending ? "Asking AI…" : "Propose repairs"}
          </Button>
        </Card>
          }
          plan={repairs.length > 0 ? (
          <Card className="space-y-3 p-4">
            <div className="text-[12px] font-medium text-ink">
              Proposed repairs — tick the ones to commit
            </div>
            <div className="space-y-2">
              {repairs.map((r, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded border border-line p-2"
                >
                  <input
                    type="checkbox"
                    checked={!!accepted[i]}
                    onChange={(e) =>
                      setAccepted((a) => ({ ...a, [i]: e.target.checked }))
                    }
                    className="mt-1"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-[11px] text-ink-3 line-through">
                      {r.old_selector}
                    </div>
                    <div className="font-mono text-[12px] text-ink">
                      {r.new_selector}
                    </div>
                    <div className="mt-0.5 text-[11px] text-ink-3">
                      {r.role} · confidence {r.confidence}
                      {r.reasoning ? ` · ${r.reasoning}` : ""}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {removed.length > 0 && (
              <div className="text-[11px] text-ink-3">
                Marked removed (no equivalent): {removed.join(", ")}
              </div>
            )}

            <DangerZone
              title="Apply to live site"
              warning="Commit writes the accepted selector repairs to the live site template. Dry-run applies nothing."
            >
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => commitM.mutate(true)}
                  disabled={!canCommit}
                >
                  Dry-run preview
                </Button>
                <Button onClick={() => commitM.mutate(false)} disabled={!canCommit}>
                  {commitM.isPending
                    ? "Committing…"
                    : `Commit ${acceptedCount} to site`}
                </Button>
              </div>
            </DangerZone>
          </Card>
        ) : null}
        />
      </div>
    </AppShell>
  );
}
