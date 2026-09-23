import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api-client";

// Row 1063: cross-replica provenance ledger reconciliation. The operator copies
// this node's chain digest to the other node, pastes that node's digest here, and
// reads the verdict (in_sync / behind / ahead / forked / unknown). Read-only on
// both sides; no ledger rows leave either node.

export type LedgerDigest = {
  count: number;
  head_id: number;
  head_chain_hash: string;
  checkpoints: [number, string][];
};
type DigestResp = { ok: boolean; digest: LedgerDigest };
type Verdict = {
  status: "in_sync" | "behind" | "ahead" | "forked" | "unknown";
  local_head_id: number;
  remote_head_id: number;
  common_id: number;
  fork_after_id: number | null;
  lag: number;
  ask_ids: number[];
};
type ReconcileResp = { ok: boolean; verdict: Verdict; digest: LedgerDigest };

const STATUS_TEXT: Record<Verdict["status"], string> = {
  in_sync: "In sync — both ledgers end at the same entry.",
  behind: "This node is behind the peer; every entry it has matches.",
  ahead: "This node is ahead of the peer; every entry the peer has matches.",
  forked: "Forked — the ledgers disagree after a shared entry.",
  unknown: "Not decidable yet — ask the peer for a digest that includes the ids below.",
};

function verdictText(v: Verdict): string {
  if (v.status === "forked") return `${STATUS_TEXT.forked} Last agreeing id: ${v.fork_after_id}.`;
  if (v.status === "behind" || v.status === "ahead") return `${STATUS_TEXT[v.status]} Lag: ${v.lag}.`;
  if (v.status === "unknown") return `${STATUS_TEXT.unknown} ${v.ask_ids.join(", ")}`;
  return STATUS_TEXT[v.status];
}

export function ProvenanceLedgerPanel() {
  const digest = useQuery<DigestResp, Error>({
    queryKey: ["provenance", "digest"],
    queryFn: () => apiGet<DigestResp>("/api/provenance/digest"),
  });
  const [peerText, setPeerText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const reconcile = useMutation<ReconcileResp, Error, unknown>({
    mutationFn: (peer) => apiPost<ReconcileResp>("/api/provenance/reconcile", { digest: peer }),
  });

  const compare = () => {
    let peer: unknown;
    try {
      peer = JSON.parse(peerText);
    } catch {
      setParseError("The pasted text is not JSON. Paste the peer's digest exactly as copied.");
      return;
    }
    setParseError(null);
    // Accept either the bare digest or the whole /api/provenance/digest response.
    const inner = (peer as { digest?: unknown })?.digest;
    reconcile.mutate(inner ?? peer);
  };

  const d = digest.data?.digest;
  return (
    <div className="space-y-3" data-testid="provenance-ledger">
      {digest.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : digest.error ? (
        <p className="text-sm text-danger">Could not read this node's ledger: {digest.error.message}</p>
      ) : d ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>
            {d.count} entries · head #{d.head_id}{" "}
            <span className="font-mono text-xs text-ink-3">{d.head_chain_hash.slice(0, 16) || "—"}</span>
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void navigator.clipboard?.writeText(JSON.stringify(d))}
          >
            Copy this node's digest
          </Button>
        </div>
      ) : null}

      <textarea
        aria-label="Peer ledger digest"
        className="w-full min-h-20 rounded-md border bg-background p-2 text-xs font-mono"
        placeholder="Paste the other node's digest here"
        value={peerText}
        onChange={(e) => {
          setPeerText(e.target.value);
          setParseError(null);
          reconcile.reset(); // a verdict for a different paste must not linger
        }}
      />
      <Button size="sm" disabled={!peerText.trim() || reconcile.isPending} onClick={compare}>
        Compare ledgers
      </Button>

      {parseError ? <p className="text-sm text-danger">{parseError}</p> : null}
      {reconcile.error ? (
        <p className="text-sm text-danger">Compare failed: {reconcile.error.message}</p>
      ) : null}
      {reconcile.data?.verdict ? (
        <p className="text-sm" data-status={reconcile.data.verdict.status}>
          {verdictText(reconcile.data.verdict)}
        </p>
      ) : null}
    </div>
  );
}
