import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiGet, apiPost } from "@/lib/api-client";
import { MacrosOpsSection } from "@/components/sections/MacrosOpsSection";

// GUI parity (T19b) — two pre-existing, bodyless operator writes that needed a
// list/selector surface before they could be wired:
//   * POST /api/sites/<sid>/account_pool/reset/<idx> — reset one pooled account
//     back to 'available' (clears dead/cooldown/fail_count). Recoverable, so a
//     light single-confirm (single tap).
//   * POST /api/macros/delete/<sid>/<name> — delete a stored macro. Destructive,
//     so destructive yes/no confirm (No default) DELETE MACRO.
// Read sources render the rows: GET /api/account_pool/status_all (global pool
// view; rows carry site_id + idx) and GET /api/macros/list (rows carry site_id
// + name). Surface-only: both writes are pre-existing audited routes; nothing
// fires on a single click. Needs operator click-through validation.

const DELETE_MACRO_TOKEN = "DELETE MACRO";

interface PoolAccount {
  idx: number;
  username?: string;
  state?: string;
  fail_count?: number;
  lease_count?: number;
  in_use_by?: string | null;
  last_error?: string | null;
  cooldown_seconds_remaining?: number;
  seconds_since_last_use?: number;
}
interface PoolStatus {
  site_id: string;
  account_count?: number;
  cooldown_seconds?: number;
  accounts?: PoolAccount[];
}
interface PoolsResult {
  pools?: PoolStatus[];
  error?: string;
}
interface MacroSummary {
  site_id?: string;
  name?: string;
  action_count?: number;
  [k: string]: unknown;
}
interface MacrosResult {
  macros?: MacroSummary[];
  error?: string;
}

interface ResetTarget {
  siteId: string;
  idx: number;
  username: string;
}
interface DeleteTarget {
  siteId: string;
  name: string;
}

export function PoolsMacros() {
  const qc = useQueryClient();

  const poolsQ = useQuery<PoolsResult>({
    queryKey: ["account-pools-all"],
    queryFn: ({ signal }) => apiGet<PoolsResult>("/api/account_pool/status_all", signal),
  });
  const macrosQ = useQuery<MacrosResult>({
    queryKey: ["macros-list"],
    queryFn: ({ signal }) => apiGet<MacrosResult>("/api/macros/list", signal),
  });

  // ── reset one account (recoverable → light single-confirm) ─────────
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const resetMut = useMutation<{ ok?: boolean; error?: string }, Error, ResetTarget>({
    mutationFn: (t) =>
      apiPost<{ ok?: boolean; error?: string }>(
        `/api/sites/${encodeURIComponent(t.siteId)}/account_pool/reset/${t.idx}`,
        {},
      ),
    onSuccess: (r, t) => {
      if (r.ok === false) toast.error(r.error || "reset failed");
      else toast.success(`Reset ${t.username || `#${t.idx}`} → available`);
      qc.invalidateQueries({ queryKey: ["account-pools-all"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── delete one macro (destructive → yes/no confirm, No default) ────
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const deleteMut = useMutation<{ ok?: boolean; error?: string }, Error, DeleteTarget>({
    mutationFn: (t) =>
      apiPost<{ ok?: boolean; error?: string }>(
        `/api/macros/delete/${encodeURIComponent(t.siteId)}/${encodeURIComponent(t.name)}`,
        {},
      ),
    onSuccess: (r, t) => {
      if (r.ok === false) toast.error(r.error || "delete failed");
      else if (r.ok) toast.success(`Deleted macro ${t.name}`);
      else toast.message(`Macro ${t.name} did not exist`);
      qc.invalidateQueries({ queryKey: ["macros-list"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const busy = resetMut.isPending || deleteMut.isPending;
  const pools = poolsQ.data?.pools ?? [];
  const macros = macrosQ.data?.macros ?? [];

  return (
    <AppShell title="Pools & macros" subtitle="Account-pool reset · macro delete · gated">
      <GatedWriteBanner title="Operator management surface" className="mb-3">
        Account reset is recoverable (single confirm); macro delete is
        destructive and requires an explicit yes/no confirmation (No default). Needs operator click-through validation.
      </GatedWriteBanner>

      {/* Account pools */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Account pools</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Reset a pooled account to <code>available</code> after you have fixed its cause (password,
          captcha, etc.) — clears dead state, cooldown and fail count. POSTs to{" "}
          <code>/api/sites/&lt;sid&gt;/account_pool/reset/&lt;idx&gt;</code>.
        </p>
        {poolsQ.isLoading && <p className="text-xs text-muted-foreground">Loading pools…</p>}
        {poolsQ.isError && <p className="text-xs text-destructive">Failed to load pools.</p>}
        {!poolsQ.isLoading && pools.length === 0 && (
          <p className="text-xs text-muted-foreground">No account pools.</p>
        )}
        {pools.map((p) => (
          <div key={p.site_id} className="mb-3">
            <div className="mb-1 text-xs font-medium text-muted-foreground">
              {p.site_id} · {p.account_count ?? p.accounts?.length ?? 0} account(s)
            </div>
            <div className="max-h-72 overflow-auto rounded border border-border">
              <table className="bd-table w-full text-xs">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="px-2 py-1">idx</th>
                    <th className="px-2 py-1">username</th>
                    <th className="px-2 py-1">state</th>
                    <th className="px-2 py-1">fails</th>
                    <th className="px-2 py-1">cooldown</th>
                    <th className="px-2 py-1"></th>
                  </tr>
                </thead>
                <tbody>
                  {(p.accounts ?? []).map((a) => (
                    <tr key={a.idx} className="border-t border-border">
                      <td className="px-2 py-1">{a.idx}</td>
                      <td className="break-all px-2 py-1">{a.username || `#${a.idx}`}</td>
                      <td className="px-2 py-1">{a.state || "?"}</td>
                      <td className="px-2 py-1">{a.fail_count ?? 0}</td>
                      <td className="px-2 py-1">
                        {a.cooldown_seconds_remaining ? `${a.cooldown_seconds_remaining}s` : "—"}
                      </td>
                      <td className="px-2 py-1 text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            setResetTarget({
                              siteId: p.site_id,
                              idx: a.idx,
                              username: a.username || `#${a.idx}`,
                            })
                          }
                        >
                          Reset
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Card>

      {/* Macros */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Macros</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Delete a stored macro. This is permanent. POSTs to{" "}
          <code>/api/macros/delete/&lt;sid&gt;/&lt;name&gt;</code>.
        </p>
        {macrosQ.isLoading && <p className="text-xs text-muted-foreground">Loading macros…</p>}
        {macrosQ.isError && <p className="text-xs text-destructive">Failed to load macros.</p>}
        {!macrosQ.isLoading && macros.length === 0 && (
          <p className="text-xs text-muted-foreground">No stored macros.</p>
        )}
        {macros.length > 0 && (
          <div className="max-h-72 overflow-auto rounded border border-border">
            <table className="bd-table w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="px-2 py-1">site_id</th>
                  <th className="px-2 py-1">name</th>
                  <th className="px-2 py-1">actions</th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {macros.map((m, i) => (
                  <tr key={`${m.site_id}:${m.name}:${i}`} className="border-t border-border">
                    <td className="break-all px-2 py-1">{m.site_id || "—"}</td>
                    <td className="break-all px-2 py-1">{m.name || "—"}</td>
                    <td className="px-2 py-1">{m.action_count ?? 0}</td>
                    <td className="px-2 py-1 text-right">
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy || !m.site_id || !m.name}
                        onClick={() => {
                          setDeleteTarget({ siteId: String(m.site_id), name: String(m.name) });
                        }}
                      >
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Reset confirm (light — recoverable, single tap) */}
      <Dialog open={resetTarget !== null} onOpenChange={(o) => !o && setResetTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset account</DialogTitle>
            <DialogDescription>
              Reset{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                {resetTarget?.username}
              </code>{" "}
              on <code className="rounded bg-muted px-1 py-0.5 text-foreground">{resetTarget?.siteId}</code>{" "}
              back to available. This clears its dead state, cooldown and fail count.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setResetTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                const t = resetTarget;
                setResetTarget(null);
                if (t) resetMut.mutate(t);
              }}
            >
              Confirm reset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete macro confirm (destructive — yes/no, No default) */}
      <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete macro</DialogTitle>
            <DialogDescription>
              Permanently delete macro{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">{deleteTarget?.name}</code>{" "}
              on <code className="rounded bg-muted px-1 py-0.5 text-foreground">{deleteTarget?.siteId}</code>.
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <p className="font-mono text-xs text-amber-300">{DELETE_MACRO_TOKEN}</p>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setDeleteTarget(null)}>
              No, cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                const t = deleteTarget;
                setDeleteTarget(null);
                if (t) deleteMut.mutate(t);
              }}
            >
              Yes, delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <MacrosOpsSection />
    </AppShell>
  );
}
