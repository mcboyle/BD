import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Crown, Search, FlaskConical } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { apiPost } from "@/lib/api-client";
import type {
  InspectCandidateRow,
  InspectResult,
  TemplateDryRunResult,
} from "@/lib/api-types";

// v3.66.149 (#1/#5) — per-site dry-run inspector. Paste page HTML and either:
//   - "Inspect candidates" → classify every detection candidate (verdict +
//     rejection reason) and show the winner that WOULD be selected, or
//   - "Test template" → whether a reviewed template matches, its selector
//     groups / resolutions / redacted patterns / lint, static selector
//     hit-counts, and whether a final safe candidate would be selected.
// Both are offline: no fetch, no download, no cookie/token/storage reads.

function kindClass(kind: string, accepted: boolean): string {
  if (!accepted) return "bg-ink-1/40 text-ink-3";
  if (kind === "download") return "bg-green-soft text-green";
  return "bg-amber-soft text-amber"; // trigger
}

function CandidateRow({ r }: { r: InspectCandidateRow }) {
  return (
    <div className="border-t border-hairline py-1.5 first:border-t-0">
      <div className="flex items-center gap-2">
        <span
          className={
            "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase " +
            kindClass(r.kind, r.accepted)
          }
        >
          {r.accepted ? r.kind : "rejected"}
        </span>
        <span className="truncate font-mono text-[11px] text-ink-2">
          {r.selector || "—"}
        </span>
        {typeof r.score === "number" && (
          <span className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-3">
            {r.score.toFixed(0)}
          </span>
        )}
      </div>
      {!r.accepted && r.reason && (
        <div className="mt-0.5 text-[11px] text-ink-3">↳ {r.reason}</div>
      )}
      {r.accepted && r.url && (
        <div className="mt-0.5 truncate text-[11px] text-ink-3">{r.url}</div>
      )}
    </div>
  );
}

function InspectPanel({ res }: { res: InspectResult }) {
  if (!res.ok) {
    return (
      <Card className="border bg-surface p-3 text-sm text-ink-3">
        {res.error ?? "Could not inspect."}
      </Card>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-xs text-ink-3">
        {res.n_accepted} accepted · {res.n_rejected} rejected ·{" "}
        {res.n_candidates} total
      </div>
      {res.winner && (
        <Card className="border bg-green-soft/40 p-3 hairline">
          <div className="mb-1 flex items-center gap-2">
            <Crown className="h-4 w-4 text-green" aria-hidden />
            <span className="text-[11px] font-semibold uppercase tracking-wider text-green">
              Would select
            </span>
          </div>
          <div className="truncate font-mono text-[11px] text-ink-2">
            {res.winner.selector}
          </div>
          {res.winner.url && (
            <div className="mt-0.5 truncate text-[11px] text-ink-3">
              {res.winner.url}
            </div>
          )}
          {res.winner.signals.length > 0 && (
            <div className="mt-1 text-[11px] text-ink-3">
              signals: {res.winner.signals.join(", ")}
            </div>
          )}
        </Card>
      )}
      <Card className="border bg-surface p-3">
        {res.candidates.length === 0 ? (
          <div className="text-sm text-ink-3">No candidates found.</div>
        ) : (
          res.candidates.map((r, i) => <CandidateRow key={i} r={r} />)
        )}
      </Card>
    </div>
  );
}

function TemplatePanel({ res }: { res: TemplateDryRunResult }) {
  if (!res.ok) {
    return (
      <Card className="border bg-surface p-3 text-sm text-ink-3">
        {res.error ?? "Could not run template dry-run."}
      </Card>
    );
  }
  return (
    <div className="space-y-2">
      <Card className="border bg-surface p-3">
        <div className="flex items-center gap-2">
          <span
            className={
              "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase " +
              (res.template_matched
                ? "bg-green-soft text-green"
                : "bg-ink-1/40 text-ink-3")
            }
          >
            {res.template_matched ? "template matched" : "no template"}
          </span>
          <span className="truncate text-sm text-ink">{res.host}</span>
        </div>
        {res.template_matched && res.template && (
          <div className="mt-1 text-xs text-ink-3">
            {res.template.resolutions.length > 0 && (
              <>Res {res.template.resolutions.slice(0, 5).join("/")} · </>
            )}
            {res.template.selectors.length} selector groups ·{" "}
            {res.network_patterns.length} patterns
          </div>
        )}
        <div className="mt-1.5 text-xs">
          <span
            className={
              res.safe_candidate_selected ? "text-green" : "text-ink-3"
            }
          >
            {res.safe_candidate_selected
              ? "✓ a safe candidate would be selected"
              : "no safe candidate from supplied HTML"}
          </span>
        </div>
      </Card>

      {res.has_blocking_lint && (
        <Card className="border-red bg-red-soft p-3 text-xs text-red" role="alert">
          {res.lint_warnings.find((w) => w.level === "error")?.message ??
            "template has an unsafe selector"}
        </Card>
      )}

      {res.selector_hit_counts.length > 0 && (
        <Card className="border bg-surface p-3">
          <div className="mb-1 eyebrow">
            Selector hits
          </div>
          {res.selector_hit_counts.map((h, i) => (
            <div
              key={i}
              className="flex items-center gap-2 border-t border-hairline py-1 first:border-t-0"
            >
              <span className="truncate font-mono text-[11px] text-ink-2">
                {h.selector}
              </span>
              <span className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-3">
                {h.hits ?? "n/a"}
              </span>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

export function DryRunInspector() {
  const { siteId = "" } = useParams<{ siteId: string }>();
  const [html, setHtml] = useState("");
  const [last, setLast] = useState<"inspect" | "template" | null>(null);

  const inspectMut = useMutation<InspectResult, Error, void>({
    mutationFn: () =>
      apiPost<InspectResult>(
        `/api/sites/${encodeURIComponent(siteId)}/candidates/inspect`,
        { html },
      ),
    onSuccess: () => setLast("inspect"),
    onError: (err) => toast.error(`Inspect failed: ${err.message}`),
  });

  const templateMut = useMutation<TemplateDryRunResult, Error, void>({
    mutationFn: () =>
      apiPost<TemplateDryRunResult>(
        `/api/sites/${encodeURIComponent(siteId)}/template/dry_run`,
        { html },
      ),
    onSuccess: () => setLast("template"),
    onError: (err) => toast.error(`Dry-run failed: ${err.message}`),
  });

  const busy = inspectMut.isPending || templateMut.isPending;

  const trailing = (
    <Link
      to={`/sites/${encodeURIComponent(siteId)}`}
      className="grid h-8 w-8 place-items-center rounded-sm text-ink-3 hover:bg-surface-2 hover:text-ink"
      aria-label="Back to site"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden />
    </Link>
  );

  return (
    <AppShell
      title="Candidate Inspector"
      subtitle="Dry-run · no fetch, no download"
      trailing={trailing}
    >
      <div className="space-y-3">
        {/* Slice helper card (UI convergence #4) — what this checks +
            the safe boundary. Dry-run only; no fetch, no download. */}
        <Callout tone="info" title="What this checks">
          Paste a page's HTML to preview which candidates a template would
          match and how it would parse them.{" "}
          <span className="text-ink">Inspect candidates</span> lists what was
          found; <span className="text-ink">Test template</span> runs the
          site's template against the markup. This is a dry run — nothing is
          fetched, downloaded, or saved.
        </Callout>
        <textarea
          value={html}
          onChange={(e) => setHtml(e.target.value)}
          placeholder="Paste page HTML source here…"
          spellCheck={false}
          className="h-40 w-full resize-y rounded-md border bg-surface p-2 font-mono text-[11px] text-ink hairline placeholder:text-ink-3 focus:outline-none focus:ring-1 focus:ring-green"
        />
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => inspectMut.mutate()}
            disabled={busy || !html.trim()}
          >
            <Search className="h-3.5 w-3.5" aria-hidden />
            {inspectMut.isPending ? "Inspecting…" : "Inspect candidates"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => templateMut.mutate()}
            disabled={busy || !html.trim()}
          >
            <FlaskConical className="h-3.5 w-3.5" aria-hidden />
            {templateMut.isPending ? "Testing…" : "Test template"}
          </Button>
        </div>

        {last === "inspect" && inspectMut.data && (
          <InspectPanel res={inspectMut.data} />
        )}
        {last === "template" && templateMut.data && (
          <TemplatePanel res={templateMut.data} />
        )}
      </div>
    </AppShell>
  );
}
