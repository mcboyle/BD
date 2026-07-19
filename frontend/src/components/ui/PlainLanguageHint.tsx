import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { apiPost } from "@/lib/api-client";

// v3.66.753 — the operator half of the a11y cluster adjudication.
//
// POST /api/a11y/plain_language {message} -> {plain, original}. Rule-based
// (accessibility.py pattern table, no model call): rewrites the error
// strings that survive friendly_error and still read like
// "ConnectionError: HTTPSConnectionPool..." to a non-technical operator.
//
// THE HONESTY RULE: plain_language RETURNS THE ORIGINAL when no pattern
// matches. plain === original means "no simpler phrasing exists" — say
// that, never render the same text twice as if it were an explanation.
//
// The other two a11y endpoints (audit {html}, contrast ?fg=&bg=) are
// CLOSED as dev-surface — no operator workflow holds their inputs. See
// DARK_CLUSTER_ADJUDICATION_v3_66_753.md.

interface PlainResult {
  plain?: string;
  original?: string;
  error?: string;
}

export function PlainLanguageHint({ message }: { message: string }) {
  const [result, setResult] = useState<PlainResult | null>(null);
  const m = useMutation<PlainResult, Error, string>({
    mutationFn: (msg) =>
      apiPost<PlainResult>("/api/a11y/plain_language", { message: msg }),
  });

  if (!message) return null;

  const doExplain = () => {
    if (m.isPending) return;
    setResult(null);
    m.mutate(message, {
      onSuccess: setResult,
      onError: (e) => setResult({ error: e.message }),
    });
  };

  const noSimpler =
    result && !result.error && (result.plain ?? "") === (result.original ?? "");

  return (
    <div className="pt-1" data-testid="plain-language">
      <Button variant="ghost" size="sm" disabled={m.isPending} onClick={doExplain}>
        {m.isPending ? "Explaining…" : "Explain plainly"}
      </Button>
      {result?.error && (
        <p className="text-xs text-red-600" role="alert">
          {result.error}
        </p>
      )}
      {result && !result.error && (
        noSimpler ? (
          <p className="text-xs text-muted">
            No simpler phrasing is available for this message.
          </p>
        ) : (
          <p className="text-xs text-ink bg-surface-2 rounded p-2 mt-1">
            {result.plain}
          </p>
        )
      )}
    </div>
  );
}
