// Cluster — T8 (v3.66.211) federation · edge-deploy · device-pairing.
//
// One lazy, default-exported route consolidating the fed / edge_deploy /
// pair families. Wire-first + confirm-tier model (v3.66.209/210):
//   * reads ungated (peers table, status);
//   * sync_pull = B single-tap (explicit "Pull now" mutation — never
//     auto-fires);
//   * manual_register = A-tier (federation TRUST boundary: No-default
//     yes/no + amber label);
//   * edge_deploy/compose = B (pure compute), edge_deploy/all = A-tier
//     (fleet-wide fan-out);
//   * pair generate = B (shows the QR by design), pair/redeem token is a
//     write-only (R) secret (SecretField, never seeded, cleared on submit).
// Nothing here is one-click.

import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { SecretField } from "@/components/SecretField";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useEdgeAll,
  useEdgeCompose,
  useFedManualRegister,
  useFedPeers,
  useFedPendingReview,
  useFedPendingTemplates,
  useFedStatus,
  useFedSetTrust,
  useFedSyncPull,
  useGeneratePairing,
  useRedeemPairing,
} from "@/hooks/useCluster";
import type { PairInfo } from "@/lib/api-types";

type Tier = "A" | "B";
interface Pending {
  tier: Tier;
  title: string;
  body: string;
  amberLabel?: string;
  run: () => void;
}

function errText(e: unknown): string {
  if (e && typeof e === "object" && "message" in e) return String((e as Error).message);
  return String(e);
}

export default function Cluster() {
  const peers = useFedPeers();
  const status = useFedStatus();
  const setTrust = useFedSetTrust();
  const TRUST_TIERS = ["trusted", "observed", "blocked"];
  const driftByPeer = new Map(
    (peers.data?.drift ?? []).map((d) => [d.instance_id, d.behind ?? 0]),
  );
  const syncPull = useFedSyncPull();
  const register = useFedManualRegister();
  const pendingTemplates = useFedPendingTemplates();
  const reviewTemplate = useFedPendingReview();
  const compose = useEdgeCompose();
  const edgeAll = useEdgeAll();
  const genPair = useGeneratePairing();
  const redeem = useRedeemPairing();

  const [pending, setPending] = useState<Pending | null>(null);

  // manual register form
  const [riid, setRiid] = useState("");
  const [rurl, setRurl] = useState("");

  // edge deploy form
  const [edImage, setEdImage] = useState("bulkdownloader:latest");

  // pairing
  const [pairInfo, setPairInfo] = useState<PairInfo | null>(null);
  const [redeemToken, setRedeemToken] = useState(""); // write-only (R)

  const confirmRun = () => {
    if (pending) pending.run();
    setPending(null);
  };

  const armSyncPull = () =>
    setPending({
      tier: "B",
      title: "Pull peer history",
      body: "Fetch new history rows from federation peers now?",
      run: () =>
        syncPull.mutate(
          {},
          {
            onSuccess: (r) => toast.success(`Pulled ${r.rows?.length ?? 0} row(s)`),
            onError: (e) => toast.error(errText(e)),
          },
        ),
    });

  const armRegister = () => {
    if (!riid.trim() || !rurl.trim()) {
      toast.error("instance_id and base_url required");
      return;
    }
    setPending({
      tier: "A",
      title: "Register federation peer",
      body: "Registering a peer establishes a TRUST relationship — this host will accept and sync history from it. Proceed?",
      amberLabel: `TRUST PEER  ${riid.trim()}  @  ${rurl.trim()}`,
      run: () =>
        register.mutate(
          { instance_id: riid.trim(), base_url: rurl.trim() },
          {
            onSuccess: (r) => {
              if (r.ok) {
                toast.success("Peer registered");
                setRiid("");
                setRurl("");
              } else {
                toast.error(r.error || "register failed");
              }
            },
            onError: (e) => toast.error(errText(e)),
          },
        ),
    });
  };

  const armCompose = () =>
    setPending({
      tier: "B",
      title: "Generate docker-compose.yml",
      body: "Generate a compose file for this install? (compute only — nothing is deployed)",
      run: () =>
        compose.mutate(
          { image: edImage.trim() || "bulkdownloader:latest" },
          { onError: (e) => toast.error(errText(e)) },
        ),
    });

  const armEdgeAll = () =>
    setPending({
      tier: "A",
      title: "Build all edge deploy artifacts",
      body: "Generate the full deploy artifact set (compose + systemd + k8s) for fleet rollout. Proceed?",
      amberLabel: "BUILD FLEET ARTIFACTS",
      run: () =>
        edgeAll.mutate(
          { image: edImage.trim() || "bulkdownloader:latest" },
          {
            onSuccess: () => toast.success("Artifacts generated"),
            onError: (e) => toast.error(errText(e)),
          },
        ),
    });

  const armGenPair = () =>
    setPending({
      tier: "B",
      title: "Generate pairing code",
      body: "Create a one-time pairing code (valid 5 minutes) to add a new device?",
      run: () =>
        genPair.mutate(undefined, {
          onSuccess: (info) => setPairInfo(info),
          onError: (e) => toast.error(errText(e)),
        }),
    });

  const armRedeem = () => {
    if (!redeemToken.trim()) {
      toast.error("pairing token required");
      return;
    }
    setPending({
      tier: "B",
      title: "Redeem pairing token",
      body: "Exchange this pairing token for a session on this device?",
      run: () =>
        redeem.mutate(
          { token: redeemToken.trim() },
          {
            onSuccess: (r) => {
              if (r.ok) {
                toast.success("Paired — session established");
                setRedeemToken(""); // clear the write-only secret
              } else {
                toast.error(r.error || "redeem failed");
              }
            },
            onError: (e) => toast.error(errText(e)),
          },
        ),
    });
  };

  return (
    <AppShell title="Cluster" subtitle="Federation · Edge deploy · Device pairing">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-4">
        <header>
          <h1 className="text-xl font-semibold">Cluster</h1>
          <p className="text-sm text-muted-foreground">
            Federation peers, edge deployment artifacts, and device pairing. Writes are gated —
            trust and fleet-wide actions require an explicit yes/no (No is the default).
          </p>
        </header>

        {/* ── Federation ─────────────────────────────────────────── */}
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Federation peers</h2>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>active: {status.data?.peers_active ?? "—"}</span>
              <span>claims: {status.data?.active_claims ?? "—"}</span>
              <span>behind: {status.data?.peers_behind ?? "—"}</span>
              <Button size="sm" variant="outline" onClick={armSyncPull} disabled={syncPull.isPending}>
                Pull now
              </Button>
            </div>
          </div>
          {peers.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : peers.data?.peers && peers.data.peers.length > 0 ? (
            <table className="bd-table w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1">instance</th>
                  <th>base url</th>
                  <th>version</th>
                  <th>trust</th>
                  <th>behind</th>
                </tr>
              </thead>
              <tbody>
                {peers.data.peers.map((p, i) => (
                  <tr key={p.instance_id || i} className="border-t border-border/40">
                    <td className="py-1 font-mono text-xs">{p.instance_id}</td>
                    <td className="font-mono text-xs">{p.base_url}</td>
                    <td className="text-xs">{p.version || "—"}</td>
                    <td className="text-xs">
                      <select
                        className="h-7 rounded-md border border-border bg-transparent px-1 text-xs"
                        value={p.trust_tier || "observed"}
                        disabled={setTrust.isPending || !p.instance_id}
                        onChange={(e) =>
                          setTrust.mutate(
                            { instance_id: p.instance_id as string, tier: e.target.value },
                            {
                              onSuccess: () => toast.success(`${p.instance_id} → ${e.target.value}`),
                              onError: (err) => toast.error(err.message || "Set trust failed"),
                            },
                          )
                        }
                      >
                        {TRUST_TIERS.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="text-xs tabular-nums">
                      {(() => {
                        const b = driftByPeer.get(p.instance_id) ?? 0;
                        return b > 0 ? (
                          <span className="text-amber">{b}</span>
                        ) : (
                          <span className="text-muted-foreground">0</span>
                        );
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted-foreground">No peers registered.</p>
          )}

          <div className="mt-4 border-t border-border/40 pt-3">
            <p className="mb-2 text-xs font-medium">Register a peer manually</p>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="max-w-[16rem]"
                placeholder="instance_id"
                value={riid}
                onChange={(e) => setRiid(e.target.value)}
                aria-label="peer instance id"
              />
              <Input
                className="max-w-[18rem]"
                placeholder="https://peer.host:5555"
                value={rurl}
                onChange={(e) => setRurl(e.target.value)}
                aria-label="peer base url"
              />
              <Button variant="destructive" onClick={armRegister} disabled={register.isPending}>
                Register peer…
              </Button>
            </div>
          </div>
        </Card>

        {/* ── Pending federated templates (C7-11.2) ──────────────── */}
        <Card className="p-4">
          <h2 className="mb-2 font-medium">Pending federated templates</h2>
          <p className="mb-3 text-xs text-muted">
            Templates received from peers. Approving writes a non-destructive
            copy (fed_&lt;peer&gt;_&lt;host&gt;) into your template store; it
            never overwrites your own templates.
          </p>
          {pendingTemplates.isLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : (pendingTemplates.data?.pending ?? []).length === 0 ? (
            <p className="text-sm text-muted">No pending templates.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {(pendingTemplates.data?.pending ?? []).map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between gap-2 rounded border border-border p-2"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{t.site_id}</div>
                    <div className="truncate text-xs text-muted">
                      from {t.from_instance}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button
                      size="sm"
                      disabled={reviewTemplate.isPending}
                      onClick={() => {
                        if (
                          window.confirm(
                            `Approve peer template "${t.site_id}" from ${t.from_instance}? It will be written into your template store.`,
                          )
                        ) {
                          reviewTemplate.mutate(
                            { id: t.id, action: "approve" },
                            {
                              onSuccess: (r) =>
                                r.ok
                                  ? toast.success("Template approved")
                                  : toast.error(r.error ?? "Approve failed"),
                              onError: (e) => toast.error(e.message),
                            },
                          );
                        }
                      }}
                    >
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={reviewTemplate.isPending}
                      onClick={() =>
                        reviewTemplate.mutate(
                          { id: t.id, action: "reject" },
                          {
                            onSuccess: () => toast.success("Template rejected"),
                            onError: (e) => toast.error(e.message),
                          },
                        )
                      }
                    >
                      Reject
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* ── Edge deploy ────────────────────────────────────────── */}
        <Card className="p-4">
          <h2 className="mb-2 font-medium">Edge deploy</h2>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="max-w-[18rem]"
              placeholder="image (bulkdownloader:latest)"
              value={edImage}
              onChange={(e) => setEdImage(e.target.value)}
              aria-label="container image"
            />
            <Button variant="outline" onClick={armCompose} disabled={compose.isPending}>
              Compose only
            </Button>
            <Button variant="destructive" onClick={armEdgeAll} disabled={edgeAll.isPending}>
              Build all artifacts…
            </Button>
          </div>
          {compose.data?.yaml && (
            <pre className="mt-3 max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
              {compose.data.yaml}
            </pre>
          )}
          {edgeAll.data?.artifacts && (
            <p className="mt-2 text-xs text-muted-foreground">
              Generated: {Object.keys(edgeAll.data.artifacts).join(", ")}
            </p>
          )}
        </Card>

        {/* ── Device pairing ─────────────────────────────────────── */}
        <Card className="p-4">
          <h2 className="mb-2 font-medium">Device pairing</h2>
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="flex-1">
              <Button variant="outline" onClick={armGenPair} disabled={genPair.isPending}>
                Generate pairing code
              </Button>
              {pairInfo && (
                <div className="mt-3">
                  {pairInfo.qr_svg ? (
                    <img
                      className="inline-block rounded bg-white p-2"
                      src={`data:image/svg+xml;utf8,${encodeURIComponent(pairInfo.qr_svg)}`}
                      alt="Device pairing QR code"
                    />
                  ) : (
                    <p className="text-xs text-amber-300">{pairInfo.qr_error || "QR unavailable"}</p>
                  )}
                  <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                    {pairInfo.url}
                  </p>
                  <p className="text-xs text-muted-foreground">Valid 5 minutes · one-time use.</p>
                </div>
              )}
            </div>
            <div className="flex-1 border-t border-border/40 pt-3 sm:border-l sm:border-t-0 sm:pl-4 sm:pt-0">
              <p className="mb-2 text-xs font-medium">Redeem a token on this device</p>
              <SecretField
                value={redeemToken}
                onChange={setRedeemToken}
                ariaLabel="pairing token"
                placeholder="pairing token (write-only)"
              />
              <Button
                className="mt-2"
                variant="outline"
                onClick={armRedeem}
                disabled={redeem.isPending}
              >
                Redeem
              </Button>
            </div>
          </div>
        </Card>
      </div>

      {/* ── Confirm-tier dialog ──────────────────────────────────── */}
      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pending?.title ?? "Confirm action"}</DialogTitle>
            <DialogDescription>{pending?.body}</DialogDescription>
          </DialogHeader>
          {pending?.tier === "A" && pending.amberLabel && (
            <p className="font-mono text-xs text-amber-300">{pending.amberLabel}</p>
          )}
          <DialogFooter>
            {pending?.tier === "A" ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
