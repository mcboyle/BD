import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  useAiClassify,
  useAiNormalizeResolution,
  type AiClassifyResult,
  type AiNormalizeResult,
} from "@/hooks/useAiHelpers";
import { useAiStatus } from "@/hooks/useIntegrations";

// v3.66.752 — GUI for the two PURE ai helpers (dark cluster, split per the
// scope: the site-scoped ai_reanalyze lives on NeedsReview, not here).
//
// Derived from aiassist.py:
//  * Both helpers answer HTTP 200 {ok:false, "AI assist is disabled"} when
//    AI is off. The panel gates on the ALREADY-WIRED /api/ai/status and
//    disables with the reason — never fires into a disabled backend.
//  * normalize_resolution: `via` IS THE ANSWER'S PROVENANCE. ok:true with
//    resolution:null means "no-match" (or "empty") — a real result that
//    must be said out loud, never rendered as blank success. regex hits
//    (confidence 95) never touched the model; via:"ai" means the LLM
//    fallback ran.
//  * classify: role is clamped server-side to the teach vocabulary;
//    unknown collapses to "ignore". Rendering the reasoning matters —
//    a bare label invites over-trust in a 15s LLM call.
//  * Empty inputs are not fired: classify on "" is a wasted model call,
//    normalize on "" is the meaningless via:"empty" shape.

export function AiHelpersPanel() {
  const status = useAiStatus();
  const aiEnabled = status.data?.enabled === true;

  const classify = useAiClassify();
  const normalize = useAiNormalizeResolution();

  const [elementDesc, setElementDesc] = useState("");
  const [classifyResult, setClassifyResult] = useState<AiClassifyResult | null>(null);
  const [filename, setFilename] = useState("");
  const [normResult, setNormResult] = useState<AiNormalizeResult | null>(null);

  const disabledReason = !aiEnabled
    ? "AI assist is disabled — enable it in Integrations"
    : null;

  const doClassify = () => {
    if (!aiEnabled || !elementDesc.trim() || classify.isPending) return;
    setClassifyResult(null);
    classify.mutate(elementDesc.trim(), {
      onSuccess: setClassifyResult,
      onError: (e) => setClassifyResult({ ok: false, error: e.message }),
    });
  };

  const doNormalize = () => {
    if (!aiEnabled || !filename.trim() || normalize.isPending) return;
    setNormResult(null);
    normalize.mutate(filename.trim(), {
      onSuccess: setNormResult,
      onError: (e) => setNormResult({ ok: false, error: e.message }),
    });
  };

  return (
    <Card className="p-4 space-y-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">AI helpers</h3>
        <span className="text-xs text-muted-foreground" role="status">
          {status.isLoading ? "…" : aiEnabled ? "AI on" : "AI off"}
        </span>
      </div>

      {disabledReason && (
        <p className="text-xs text-amber-600">{disabledReason}</p>
      )}

      {/* ---- classify_role -------------------------------------------- */}
      <div className="space-y-2">
        <label className="text-xs font-medium" htmlFor="ai-classify-input">
          Classify an element
        </label>
        <textarea
          id="ai-classify-input"
          className="w-full text-xs border rounded p-2 bg-background font-mono"
          rows={2}
          placeholder={'<input type="password" name="pw" placeholder="Password">'}
          value={elementDesc}
          onChange={(e) => setElementDesc(e.target.value)}
        />
        <Button
          size="sm"
          disabled={!aiEnabled || !elementDesc.trim() || classify.isPending}
          title={disabledReason ?? (elementDesc.trim() ? "Classify" : "Paste an element first")}
          onClick={doClassify}
        >
          {classify.isPending ? "Classifying…" : "Classify"}
        </Button>
        {classifyResult && (
          <div className="text-xs" data-testid="classify-result">
            {classifyResult.ok ? (
              <>
                <span className="font-mono">{classifyResult.role}</span>{" "}
                <span className="text-muted-foreground">
                  ({classifyResult.confidence}% · {classifyResult.provider})
                </span>
                {classifyResult.reasoning && (
                  <p className="text-muted-foreground mt-1">
                    {classifyResult.reasoning}
                  </p>
                )}
              </>
            ) : (
              <span className="text-red-600" role="alert">
                {classifyResult.error ?? "classify failed"}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ---- normalize_resolution -------------------------------------- */}
      <div className="space-y-2 border-t pt-3">
        <label className="text-xs font-medium" htmlFor="ai-norm-input">
          Read a resolution from a filename
        </label>
        <input
          id="ai-norm-input"
          className="w-full text-xs border rounded p-2 bg-background font-mono"
          placeholder="clip_4k_final.mp4"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
        />
        <Button
          size="sm"
          disabled={!aiEnabled || !filename.trim() || normalize.isPending}
          title={disabledReason ?? (filename.trim() ? "Normalize" : "Enter a filename first")}
          onClick={doNormalize}
        >
          {normalize.isPending ? "Reading…" : "Normalize"}
        </Button>
        {normResult && (
          <div className="text-xs" data-testid="normalize-result">
            {!normResult.ok ? (
              <span className="text-red-600" role="alert">
                {normResult.error ?? "normalize failed"}
              </span>
            ) : normResult.resolution ? (
              <>
                <span className="font-mono">
                  {normResult.resolution}
                  {normResult.label ? ` (${normResult.label})` : ""}
                  {normResult.width && normResult.height
                    ? ` ${normResult.width}x${normResult.height}`
                    : ""}
                </span>{" "}
                <span className="text-muted-foreground">
                  {normResult.confidence}% · via {normResult.via}
                </span>
              </>
            ) : (
              // ok:true + resolution:null is a REAL answer ("no-match") —
              // say it; a blank here would launder no-answer into success.
              <span className="text-muted-foreground">
                No resolution recognized in that filename (via{" "}
                {normResult.via ?? "?"}).
              </span>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
