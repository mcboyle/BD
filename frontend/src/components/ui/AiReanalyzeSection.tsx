import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useAiReanalyze, type AiReanalyzeResult } from "@/hooks/useAiHelpers";
import { useAiStatus } from "@/hooks/useIntegrations";

// v3.66.752 — GUI for the site-scoped third of the dark ai cluster.
//
// POST /api/sites/<sid>/ai_reanalyze {url} requires the url to be a job the
// runner still holds (404 "url not in queue") — its meaning is "re-analyze
// THIS job", and the endpoint's own context string says the moment is a
// needs_review row ("This URL was DOWNLOADED previously but moved to
// needs_review"). So it mounts HERE, where sid+url+failure are already on
// screen — not on a generic site page behind a URL picker.
//
// Honesty notes, derived from app_sites_id_core.py:
//  * had_screenshot=false means the analysis was TEXT-ONLY (the worker
//    browser is gone; vision mode needs the stored screenshot). That is a
//    weaker answer and the panel says so.
//  * suggestions are PROPOSALS for the teach panel to verify — advisory,
//    same posture as diff_repair. Rendering reasoning + confidence resists
//    over-trust in a one-shot LLM call.
//  * AI off => HTTP 200 {ok:false}; gate on the wired /api/ai/status and
//    disable with the reason instead of firing into a disabled backend.

export function AiReanalyzeSection({ sid, url }: { sid: string; url: string }) {
  const status = useAiStatus();
  const aiEnabled = status.data?.enabled === true;
  const reanalyze = useAiReanalyze();
  const [result, setResult] = useState<AiReanalyzeResult | null>(null);

  const doAsk = () => {
    if (!aiEnabled || !sid || !url || reanalyze.isPending) return;
    setResult(null);
    reanalyze.mutate(
      { sid, url },
      {
        onSuccess: setResult,
        onError: (e) => setResult({ ok: false, error: e.message }),
      },
    );
  };

  return (
    <div className="space-y-2 border-t pt-3" data-testid="ai-reanalyze">
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!aiEnabled || reanalyze.isPending}
          title={
            !aiEnabled
              ? "AI assist is disabled — enable it in Integrations"
              : "Ask the model why this failed and what selectors to try"
          }
          onClick={doAsk}
        >
          {reanalyze.isPending ? "Asking…" : "Ask AI about this failure"}
        </Button>
        {!aiEnabled && !status.isLoading && (
          <span className="text-xs text-muted">AI assist is disabled</span>
        )}
      </div>

      {result && !result.ok && (
        <p className="text-xs text-red-600" role="alert">
          {result.error ?? "reanalyze failed"}
        </p>
      )}

      {result?.ok && (
        <div className="space-y-2 text-xs">
          <p className="text-muted">
            {result.had_screenshot
              ? "Analyzed with the stored screenshot (vision mode)."
              : "Text-only analysis — no screenshot was available, so the model never saw the page."}{" "}
            {result.tried_count ?? 0} selector(s) already tried ·{" "}
            {result.event_count ?? 0} recent event(s) included.
          </p>
          {(result.suggestions ?? []).length === 0 ? (
            <p className="text-muted">
              The model returned no selector proposals for this failure.
            </p>
          ) : (
            <ul className="space-y-1">
              {(result.suggestions ?? []).map((s, i) => (
                <li key={i} className="rounded border p-2">
                  <div className="font-mono break-all">{s.selector}</div>
                  <div className="text-muted">
                    {s.role} · {s.confidence}%
                    {s.reasoning ? ` — ${s.reasoning}` : ""}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="text-muted">
            Proposals only — verify in the teach panel before committing.
          </p>
        </div>
      )}
    </div>
  );
}
