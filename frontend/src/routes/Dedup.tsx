import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { formatBytes } from "@/lib/format";
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
import { apiGet, apiPost } from "@/lib/api-client";

// GUI parity (T19c) — the dedup workflow panel. Wires all six pre-existing
// /api/dedup/* endpoints into one surface:
//   status (poll) · scan · scan/cancel · find · groups · remove
// Notes from source (186):
//   * The whole feature is gated on videohash + ffmpeg; status reports
//     availability and the UI greys out actions when unavailable.
//   * scan is a non-destructive background job (refuses if one is running);
//     light single-confirm naming the root.
//   * remove de-lists a path from the registry ONLY — it does NOT delete the
//     file on disk (a re-scan re-adds it). Recoverable → light single-confirm.
//   * find is read-shaped (no mutation) → no confirm.
// Surface-only: every endpoint is a pre-existing audited route. Needs operator
// click-through validation.

interface DedupStats {
  total?: number;
  last_computed_at?: number;
}
interface DedupScanState {
  running?: boolean;
  started_at?: number;
  done?: number;
  total?: number;
  current_path?: string;
  summary?: unknown;
}
interface DedupStatus {
  ok?: boolean;
  available?: boolean;
  videohash_installed?: boolean;
  ffmpeg_installed?: boolean;
  stats?: DedupStats;
  scan?: DedupScanState;
}
interface DedupMember {
  path: string;
  hash_hex?: string;
  file_size_bytes?: number;
  computed_at?: number;
  distance?: number;
}
interface DedupGroup {
  members: DedupMember[];
  max_distance?: number;
  size_diff_bytes?: number;
}
interface GroupsResult {
  ok?: boolean;
  error?: string;
  distance?: number;
  total_files?: number;
  group_count?: number;
  groups?: DedupGroup[];
}
interface FindResult {
  ok?: boolean;
  error?: string;
  path?: string;
  hash_hex?: string;
  distance_threshold?: number;
  duplicates?: DedupMember[];
}

function clampDistance(s: string): number {
  const n = parseInt(s, 10);
  if (!Number.isFinite(n)) return 4;
  return Math.max(0, Math.min(32, n));
}

export function Dedup() {
  const qc = useQueryClient();

  const statusQ = useQuery<DedupStatus>({
    queryKey: ["dedup-status"],
    queryFn: ({ signal }) => apiGet<DedupStatus>("/api/dedup/status", signal),
    // poll while a scan is running, otherwise idle
    refetchInterval: (q) => (q.state.data?.scan?.running ? 1000 : false),
  });

  const status = statusQ.data;
  const scanRunning = !!status?.scan?.running;
  const available = !!status?.available;

  // ── scan ───────────────────────────────────────────────────────────
  const [root, setRoot] = useState("");
  const [scanConfirm, setScanConfirm] = useState(false);
  const scanMut = useMutation<{ ok?: boolean; error?: string; root?: string }, Error, void>({
    mutationFn: () =>
      apiPost<{ ok?: boolean; error?: string; root?: string }>("/api/dedup/scan", {
        root: root.trim() || undefined,
      }),
    onSuccess: (r) => {
      if (r.ok === false) toast.error(r.error || "scan failed to start");
      else toast.success(`Scan started: ${r.root ?? "(default root)"}`);
      qc.invalidateQueries({ queryKey: ["dedup-status"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const cancelMut = useMutation<{ ok?: boolean; error?: string }, Error, void>({
    mutationFn: () => apiPost<{ ok?: boolean; error?: string }>("/api/dedup/scan/cancel", {}),
    onSuccess: (r) => {
      if (r.ok === false) toast.error(r.error || "nothing to cancel");
      else toast.message("Scan cancellation requested");
      qc.invalidateQueries({ queryKey: ["dedup-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── groups ───────────────────────────────────────────────────────────
  const [groupsDistance, setGroupsDistance] = useState("4");
  const [groups, setGroups] = useState<GroupsResult | null>(null);
  const groupsMut = useMutation<GroupsResult, Error, void>({
    mutationFn: () =>
      apiGet<GroupsResult>(`/api/dedup/groups?distance=${clampDistance(groupsDistance)}`),
    onSuccess: (r) => {
      setGroups(r);
      if (r.ok === false) toast.error(r.error || "groups failed");
      else toast.success(`${r.group_count ?? 0} group(s) over ${r.total_files ?? 0} file(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // ── find ───────────────────────────────────────────────────────────
  const [findPath, setFindPath] = useState("");
  const [findDistance, setFindDistance] = useState("4");
  const [findResult, setFindResult] = useState<FindResult | null>(null);
  const findMut = useMutation<FindResult, Error, void>({
    mutationFn: () =>
      apiPost<FindResult>("/api/dedup/find", {
        path: findPath.trim(),
        distance: clampDistance(findDistance),
      }),
    onSuccess: (r) => {
      setFindResult(r);
      if (r.ok === false) toast.error(r.error || "find failed");
      else toast.success(`${r.duplicates?.length ?? 0} duplicate(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // ── remove (registry de-list, recoverable) ─────────────────────────
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const removeMut = useMutation<{ ok?: boolean; path?: string }, Error, string>({
    mutationFn: (path) =>
      apiPost<{ ok?: boolean; path?: string }>("/api/dedup/remove", { path }),
    onSuccess: (r, path) => {
      if (r.ok) {
        toast.success("Removed from registry");
        // prune it from the loaded groups view without a full reload
        setGroups((g) =>
          g
            ? {
                ...g,
                groups: (g.groups ?? [])
                  .map((grp) => ({
                    ...grp,
                    members: grp.members.filter((m) => m.path !== path),
                  }))
                  .filter((grp) => grp.members.length > 1),
              }
            : g,
        );
      } else {
        toast.message("Path was not in the registry");
      }
      qc.invalidateQueries({ queryKey: ["dedup-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const busy =
    scanMut.isPending || cancelMut.isPending || groupsMut.isPending || findMut.isPending || removeMut.isPending;
  const fmtSize = (b?: number) => (b ? formatBytes(b) : "—");

  return (
    <AppShell title="Dedup" subtitle="Perceptual-hash duplicate finder · scan · review · de-list">
      <GatedWriteBanner className="mb-3">
        Scan is a non-destructive background job. Remove only de-lists a path from the dedup registry — it
        does NOT delete the file on disk. Needs operator click-through validation.
      </GatedWriteBanner>

      {/* Availability + status */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Status</h2>
        {statusQ.isLoading && <p className="text-xs text-muted-foreground">Loading status…</p>}
        {statusQ.isError && <p className="text-xs text-destructive">Failed to load status.</p>}
        {status && (
          <div className="grid grid-cols-2 gap-1 text-xs sm:grid-cols-4">
            <div>
              available:{" "}
              <span className={available ? "text-emerald-400" : "text-destructive"}>
                {String(available)}
              </span>
            </div>
            <div>videohash: {String(!!status.videohash_installed)}</div>
            <div>ffmpeg: {String(!!status.ffmpeg_installed)}</div>
            <div>registry: {status.stats?.total ?? 0} file(s)</div>
          </div>
        )}
        {!available && status && (
          <p className="mt-2 text-xs text-amber-300">
            Dedup is unavailable on this host (needs videohash + ffmpeg). Actions are disabled.
          </p>
        )}
        {scanRunning && (
          <div className="mt-2 text-xs text-muted-foreground">
            Scanning… {status?.scan?.done ?? 0}/{status?.scan?.total ?? 0}
            {status?.scan?.current_path ? ` · ${status.scan.current_path}` : ""}
          </div>
        )}
      </Card>

      {/* Scan */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Scan a folder</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Compute perceptual hashes for every video under a root (defaults to the first site's
          download_dir). POSTs to <code>/api/dedup/scan</code>.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-80"
            value={root}
            onChange={(e) => setRoot(e.target.value)}
            placeholder="root path (blank = first site download_dir)"
            disabled={!available || scanRunning}
          />
          {!scanRunning ? (
            <Button
              size="sm"
              disabled={busy || !available}
              onClick={() => setScanConfirm(true)}
            >
              Scan
            </Button>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={() => cancelMut.mutate()}
            >
              Cancel scan
            </Button>
          )}
        </div>
        {status?.scan?.summary != null && (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-xs">
            {JSON.stringify(status.scan.summary, null, 2)}
          </pre>
        )}
      </Card>

      {/* Groups */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Duplicate groups</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Group registry files within a Hamming distance (capped at 5000 files). GETs{" "}
          <code>/api/dedup/groups</code>.
        </p>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Input
            className="w-40"
            value={groupsDistance}
            onChange={(e) => setGroupsDistance(e.target.value)}
            placeholder="distance (0-32)"
          />
          <Button size="sm" variant="outline" disabled={busy || !available} onClick={() => groupsMut.mutate()}>
            Load groups
          </Button>
        </div>
        {groups?.ok === false && <p className="text-xs text-destructive">{groups.error}</p>}
        {groups?.ok && (groups.groups?.length ?? 0) === 0 && (
          <p className="text-xs text-muted-foreground">No duplicate groups at this distance.</p>
        )}
        {(groups?.groups ?? []).map((g, gi) => (
          <div key={gi} className="mb-2 rounded border border-border">
            <div className="border-b border-border px-2 py-1 text-xs text-muted-foreground">
              group {gi + 1} · {g.members.length} files · max dist {g.max_distance ?? 0} ·
              Δsize {fmtSize(g.size_diff_bytes)}
            </div>
            <table className="bd-table w-full text-xs">
              <tbody>
                {g.members.map((m) => (
                  <tr key={m.path} className="border-t border-border">
                    <td className="break-all px-2 py-1">{m.path}</td>
                    <td className="px-2 py-1 whitespace-nowrap">{fmtSize(m.file_size_bytes)}</td>
                    <td className="px-2 py-1 whitespace-nowrap">dist {m.distance ?? 0}</td>
                    <td className="px-2 py-1 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => setRemoveTarget(m.path)}
                      >
                        Remove from registry
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Card>

      {/* Find */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Find duplicates of a file</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Look up near-duplicates of one path (hashes it first if needed — read-only). POSTs to{" "}
          <code>/api/dedup/find</code>.
        </p>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Input
            className="w-80"
            value={findPath}
            onChange={(e) => setFindPath(e.target.value)}
            placeholder="absolute file path"
            disabled={!available}
          />
          <Input
            className="w-32"
            value={findDistance}
            onChange={(e) => setFindDistance(e.target.value)}
            placeholder="distance"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !available || !findPath.trim()}
            onClick={() => findMut.mutate()}
          >
            Find
          </Button>
        </div>
        {findResult?.ok === false && <p className="text-xs text-destructive">{findResult.error}</p>}
        {findResult?.ok && (
          <div className="max-h-56 overflow-auto rounded border border-border">
            <table className="bd-table w-full text-xs">
              <tbody>
                {(findResult.duplicates ?? []).length === 0 ? (
                  <tr>
                    <td className="px-2 py-1 text-muted-foreground">No duplicates found.</td>
                  </tr>
                ) : (
                  (findResult.duplicates ?? []).map((m) => (
                    <tr key={m.path} className="border-t border-border">
                      <td className="break-all px-2 py-1">{m.path}</td>
                      <td className="px-2 py-1 whitespace-nowrap">dist {m.distance ?? 0}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Scan confirm (light) */}
      <Dialog open={scanConfirm} onOpenChange={(o) => !o && setScanConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start scan</DialogTitle>
            <DialogDescription>
              Hash every video under{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                {root.trim() || "(first site download_dir)"}
              </code>
              . This runs in the background and is non-destructive.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setScanConfirm(false)}>
              Cancel
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                setScanConfirm(false);
                scanMut.mutate();
              }}
            >
              Start scan
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Remove confirm (light — registry de-list only) */}
      <Dialog open={removeTarget !== null} onOpenChange={(o) => !o && setRemoveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove from registry</DialogTitle>
            <DialogDescription>
              De-list{" "}
              <code className="break-all rounded bg-muted px-1 py-0.5 text-foreground">
                {removeTarget}
              </code>{" "}
              from the dedup registry. The file on disk is NOT deleted; a re-scan re-adds it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                const p = removeTarget;
                setRemoveTarget(null);
                if (p) removeMut.mutate(p);
              }}
            >
              Confirm remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
