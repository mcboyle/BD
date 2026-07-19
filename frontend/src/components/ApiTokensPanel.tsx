// ApiTokensPanel — scoped programmatic API tokens (F4.3, v3.66.227).
//
// Wires the three management endpoints as FULL /api/ literals so the parity
// scanner credits them as spa_wired:
//
//   * GET    /api/api_tokens             — list token METADATA (never the
//                                          secret; server only echoes it once
//                                          at creation).
//   * POST   /api/api_tokens             — {scope, label?, ttl_hours?} mint a
//                                          token; response carries the full
//                                          token value exactly once.
//   * DELETE /api/api_tokens/<token_id>  — revoke.
//
// Enforcement of what each scope may reach lives server-side in
// _API_TOKEN_ROUTE_POLICY (fail-closed). read < enqueue < admin.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiDelete, apiGet, apiPost } from "@/lib/api-client";
import { formatTimestamp } from "@/lib/format";

interface ApiTokenRow {
  token_id: string;
  label: string;
  scope: string;
  status: string;
  created_at?: number;
  expires_at?: number | null;
  use_count?: number;
}
interface ApiTokensList {
  ok: boolean;
  tokens: ApiTokenRow[];
}
interface ApiTokenCreateResult {
  ok: boolean;
  token?: string;
  token_id?: string;
  scope?: string;
  error?: string;
}

const SCOPES = ["read", "enqueue", "admin"] as const;
type Scope = (typeof SCOPES)[number];

function useApiTokens() {
  return useQuery<ApiTokensList, Error>({
    queryKey: ["api_tokens", "list"],
    queryFn: ({ signal }) => apiGet<ApiTokensList>("/api/api_tokens", signal),
  });
}

function useApiTokenCreate() {
  const qc = useQueryClient();
  return useMutation<
    ApiTokenCreateResult,
    Error,
    { scope: Scope; label: string; ttlHours?: number }
  >({
    mutationFn: ({ scope, label, ttlHours }) =>
      apiPost<ApiTokenCreateResult>("/api/api_tokens", {
        scope,
        label,
        ttl_hours: ttlHours,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api_tokens", "list"] }),
  });
}

function useApiTokenRevoke() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (tokenId) =>
      apiDelete<{ ok: boolean }>(`/api/api_tokens/${encodeURIComponent(tokenId)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api_tokens", "list"] }),
  });
}

export function ApiTokensPanel() {
  const list = useApiTokens();
  const create = useApiTokenCreate();
  const revoke = useApiTokenRevoke();

  const [scope, setScope] = useState<Scope>("read");
  const [label, setLabel] = useState("");
  const [ttl, setTtl] = useState("");
  const [minted, setMinted] = useState<string | null>(null);

  const busy = create.isPending || revoke.isPending;

  function onCreate() {
    const ttlHours = ttl.trim() === "" ? undefined : Number(ttl);
    if (ttlHours !== undefined && (!Number.isFinite(ttlHours) || ttlHours <= 0)) {
      toast.error("TTL must be a positive number of hours (or blank).");
      return;
    }
    create.mutate(
      { scope, label: label.trim(), ttlHours },
      {
        onSuccess: (r) => {
          if (!r.ok || !r.token) {
            toast.error(r.error || "create failed");
            return;
          }
          setMinted(r.token);
          setLabel("");
          setTtl("");
          toast.success(`Created ${r.scope} token — copy it now, shown once.`);
        },
        onError: (e) => toast.error(e.message),
      },
    );
  }

  return (
    <Card className="mb-3 p-4">
      <div className="mb-2 text-sm font-medium text-ink">API tokens</div>
      <p className="mb-3 text-[11px] text-ink-3">
        Mint scoped tokens for scripts / integrations. Scope is hierarchical:{" "}
        <b>read</b> &lt; <b>enqueue</b> &lt; <b>admin</b>. A token reaches only
        the routes allowed for its scope; everything else is denied. Send it as{" "}
        <code className="font-mono">Authorization: Bearer &lt;token&gt;</code> or{" "}
        <code className="font-mono">X-BD-API-Token</code>. The full value is
        shown once, here, at creation.
      </p>

      <div className="flex flex-wrap items-end gap-2">
        <label className="text-[11px] text-ink-3">
          Scope
          <select
            className="mt-1 block rounded border border-input bg-background px-2 py-1 text-sm"
            value={scope}
            onChange={(e) => setScope(e.target.value as Scope)}
          >
            {SCOPES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-ink-3">
          Label
          <input
            className="mt-1 block rounded border border-input bg-background px-2 py-1 text-sm"
            placeholder="e.g. ci-runner"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="text-[11px] text-ink-3">
          TTL hours (blank = no expiry)
          <input
            className="mt-1 block w-40 rounded border border-input bg-background px-2 py-1 text-sm"
            placeholder="optional"
            value={ttl}
            onChange={(e) => setTtl(e.target.value)}
          />
        </label>
        <Button size="sm" disabled={busy} onClick={onCreate}>
          {create.isPending ? "Creating…" : "Create token"}
        </Button>
      </div>

      {minted && (
        <div className="mt-3 rounded border border-amber-700/40 bg-amber-950/20 p-2">
          <div className="text-[11px] text-ink-3">
            New token (shown once — copy and store it now):
          </div>
          <code className="mt-1 block break-all rounded bg-muted px-1 py-0.5 font-mono text-xs">
            {minted}
          </code>
          <Button
            size="sm"
            variant="ghost"
            className="mt-1"
            onClick={() => {
              void navigator.clipboard?.writeText(minted);
              toast.success("Copied");
            }}
          >
            Copy
          </Button>
        </div>
      )}

      <div className="mt-3">
        <div className="mb-1 text-[11px] text-ink-3">
          {list.data?.tokens?.length
            ? `${list.data.tokens.length} token(s)`
            : "No tokens yet"}
        </div>
        {list.isLoading ? (
          <div className="text-[11px] text-ink-3">Loading…</div>
        ) : (
          (list.data?.tokens ?? []).map((t) => (
            <div
              key={t.token_id}
              className="mt-1 flex items-center justify-between rounded border border-input p-2"
            >
              <div className="min-w-0">
                <div className="truncate text-xs text-ink">
                  {t.label || "(no label)"}{" "}
                  <span className="font-mono text-ink-3">({t.token_id})</span>
                </div>
                <div className="text-[11px] text-ink-3">
                  scope <b>{t.scope}</b> · {t.status} · used {t.use_count ?? 0}×
                  {t.expires_at
                    ? ` · expires ${formatTimestamp(t.expires_at)}`
                    : ""}
                </div>
              </div>
              {t.status === "active" && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    revoke.mutate(t.token_id, {
                      onSuccess: (r) =>
                        r.ok
                          ? toast.success("Token revoked")
                          : toast.error("revoke failed"),
                      onError: (e) => toast.error(e.message),
                    })
                  }
                >
                  Revoke
                </Button>
              )}
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
