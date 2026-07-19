import { useMutation } from "@tanstack/react-query";
import { Download, FileSearch, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { DangerZone } from "@/components/ui/DangerZone";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SecretField } from "@/components/SecretField";
import {
  apiGet,
  apiPost,
  apiPostDownload,
  apiPostForm,
} from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

// C5 (v3.66.635 status + v3.66.636 controls): the continuous SQLite replication
// (Litestream) durability layer. All FOUR /api/replication/* literals are FULL
// literals below so tools/gui_parity_inventory credits them spa_wired (the three
// mutating controls are operator-facing -> operator_facing_unwired must stay 0).
type ReplStatus = OkResult & {
  enabled?: boolean;
  binary_present?: boolean;
  configured_stores?: string[];
  replica_root?: string;
  running?: boolean;
};

// GUI parity (177) — Backup actions. Surfaces the EXISTING backup endpoints.
// Inspect ops (verify/drift/preview) are non-mutating → run directly.
// Destructive ops are GATED by a yes/no confirm (No default), never one-click:
//   create (writes+downloads an archive), smoke_restore (sandbox restore),
//   restore (DESTRUCTIVE — overwrites live data; multipart upload).

type Pending =
  | { kind: "create"; token: "" }
  | { kind: "smoke"; path: string; token: "" }
  | { kind: "restore"; dry: boolean; fileName: string; token: string };

export function Backup() {
  const [path, setPath] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [includeDb, setIncludeDb] = useState(true);
  const [restorePass, setRestorePass] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const [output, setOutput] = useState<unknown>(null);

  const pump = (res: unknown) => setOutput(res);

  const verify = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/backup/verify", { path }),
    onSuccess: pump,
    onError: (e) => toast.error(e.message),
  });
  const drift = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/backup/drift", { path }),
    onSuccess: pump,
    onError: (e) => toast.error(e.message),
  });
  const preview = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>("/api/backup/preview", {
        passphrase: passphrase || null,
        include_db: includeDb,
      }),
    onSuccess: pump,
    onError: (e) => toast.error(e.message),
  });
  const smoke = useMutation<OkResult, Error, string>({
    mutationFn: (p) => apiPost<OkResult>("/api/backup/smoke_restore", { path: p }),
    onSuccess: (res) => {
      pump(res);
      toast.success("Smoke-restore done");
    },
    onError: (e) => toast.error(e.message),
  });
  const create = useMutation<void, Error, void>({
    mutationFn: () =>
      apiPostDownload(
        "/api/backup/create",
        { passphrase: passphrase || null, include_db: includeDb },
        "backup.zip",
      ),
    onSuccess: () => toast.success("Backup created + downloaded"),
    onError: (e) => toast.error(e.message),
  });
  const restore = useMutation<OkResult, Error, { file: File; dry: boolean }>({
    mutationFn: ({ file, dry }) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("dry_run", dry ? "1" : "0");
      if (restorePass) fd.append("passphrase", restorePass);
      return apiPostForm<OkResult>("/api/backup/restore", fd);
    },
    onSuccess: (res, vars) => {
      pump(res);
      if (res.ok) toast.success(vars.dry ? "Dry-run restore ok" : "Restore applied");
      else toast.error(res.error || "restore failed");
    },
    onError: (e) => toast.error(e.message),
  });

  // ── C5: continuous replication (durability) ─────────────────────────
  const [replStores, setReplStores] = useState<string[]>([]);
  const [replDb, setReplDb] = useState("");
  const replStatus = useMutation<ReplStatus, Error, void>({
    mutationFn: () => apiGet<ReplStatus>("/api/replication/status"),
    onSuccess: (res) => {
      pump(res);
      const stores = res.configured_stores ?? [];
      setReplStores(stores);
      if (stores.length && !replDb) setReplDb(stores[0]);
    },
    onError: (e) => toast.error(e.message),
  });
  const replStart = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/replication/start", {}),
    onSuccess: (res) => {
      pump(res);
      if (res.ok) toast.success("Replication started");
      else toast.error(res.error || "start failed (default-off / no binary)");
    },
    onError: (e) => toast.error(e.message),
  });
  const replStop = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/replication/stop", {}),
    onSuccess: (res) => {
      pump(res);
      toast.success("Replication stopped");
    },
    onError: (e) => toast.error(e.message),
  });
  const replRestore = useMutation<OkResult, Error, string>({
    mutationFn: (db) => apiPost<OkResult>("/api/replication/restore", { db_name: db }),
    onSuccess: (res) => {
      pump(res);
      if (res.ok) toast.success("Store restored to staging");
      else toast.error(res.error || "restore failed");
    },
    onError: (e) => toast.error(e.message),
  });

  const [pending, setPending] = useState<Pending | null>(null);
  const arm = (p: Pending) => {
    setPending(p);
  };
  const busy =
    verify.isPending ||
    drift.isPending ||
    preview.isPending ||
    smoke.isPending ||
    create.isPending ||
    restore.isPending ||
    replStatus.isPending ||
    replStart.isPending ||
    replStop.isPending ||
    replRestore.isPending;

  const confirmRun = () => {
    if (!pending) return;
    if (pending.kind === "create") create.mutate();
    else if (pending.kind === "smoke") smoke.mutate(pending.path);
    else if (pending.kind === "restore") {
      const f = fileRef.current?.files?.[0];
      if (f) restore.mutate({ file: f, dry: pending.dry });
    }
    setPending(null);
  };

  const needPath = () => {
    if (!path.trim()) {
      toast.error("backup path required");
      return false;
    }
    return true;
  };

  return (
    <AppShell title="Backup" subtitle="Create, inspect, and restore backups">
      <WorkflowPage
        purpose={
          <Callout tone="info" title="What this page does">
            Create an encrypted backup archive, inspect or smoke-restore one safely
            in a sandbox, or restore a backup over live data. Inspect and smoke-restore
            never touch live data; a live restore is grouped under the danger zone below.
          </Callout>
        }
        inputs={
          <>
            <Card className="p-4">
              <h2 className="section-head">Inspect a backup (read-only)</h2>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  placeholder="backup path on host"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  className="min-w-[260px] flex-1"
                />
                <Button variant="outline" disabled={busy} onClick={() => needPath() && verify.mutate()}>
                  <FileSearch className="mr-1 h-4 w-4" /> Verify
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => needPath() && drift.mutate()}>
                  Drift vs live
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => needPath() && arm({ kind: "smoke", path, token: "" })}
                >
                  Smoke-restore (sandbox)
                </Button>
              </div>
            </Card>

            <Card className="mt-4 p-4">
              <h2 className="section-head">Create</h2>
              <div className="flex flex-wrap items-center gap-2">
                <SecretField
                  placeholder="passphrase (optional)"
                  value={passphrase}
                  onChange={setPassphrase}
                  ariaLabel="Backup passphrase"
                  className="max-w-xs"
                />
                <label className="flex items-center gap-1 text-sm text-ink-3">
                  <input
                    type="checkbox"
                    checked={includeDb}
                    onChange={(e) => setIncludeDb(e.target.checked)}
                  />
                  include DB
                </label>
                <Button variant="outline" disabled={busy} onClick={() => preview.mutate()}>
                  Preview
                </Button>
                <Button disabled={busy} onClick={() => arm({ kind: "create", token: "" })}>
                  <Download className="mr-1 h-4 w-4" /> Create &amp; download
                </Button>
              </div>
            </Card>

            <Card className="mt-4 p-4">
              <h2 className="section-head">Continuous replication (durability)</h2>
              <p className="mb-2 text-sm text-ink-3">
                Litestream continuously ships the SQLite WAL to a file replica
                (near-zero RPO). Off by default; needs the litestream binary and
                replication.enabled in config. Refresh reads the live durability
                state; Start/Stop control the sidecar; Restore reconstructs a store
                from its replica into a staging path (never overwrites live).
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" disabled={busy} onClick={() => replStatus.mutate()}>
                  Refresh status
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => replStart.mutate()}>
                  Start
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => replStop.mutate()}>
                  Stop
                </Button>
                <select
                  className="rounded border bg-transparent px-2 py-1 text-sm"
                  aria-label="Store to restore"
                  value={replDb}
                  onChange={(e) => setReplDb(e.target.value)}
                >
                  {replStores.length === 0 ? (
                    <option value="">(refresh to list stores)</option>
                  ) : (
                    replStores.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))
                  )}
                </select>
                <Button
                  variant="outline"
                  disabled={busy || !replDb}
                  onClick={() => replDb && replRestore.mutate(replDb)}
                >
                  Restore store
                </Button>
              </div>
            </Card>
          </>
        }
        danger={
          <DangerZone
            title="Restore"
            warning="Destructive — a live restore overwrites current data. Dry-run applies nothing."
          >
            <div className="flex flex-wrap items-center gap-2">
              <input ref={fileRef} type="file" className="text-sm" />
              <SecretField
                placeholder="passphrase (optional)"
                value={restorePass}
                onChange={setRestorePass}
                ariaLabel="Restore passphrase"
                className="max-w-xs"
              />
              <label className="flex items-center gap-1 text-sm text-ink-3">
                <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                dry-run
              </label>
              <Button
                variant="destructive"
                disabled={busy}
                onClick={() => {
                  const f = fileRef.current?.files?.[0];
                  if (!f) {
                    toast.error("choose a backup file first");
                    return;
                  }
                  arm({
                    kind: "restore",
                    dry: dryRun,
                    fileName: f.name,
                    token: dryRun ? "" : "RESTORE",
                  });
                }}
              >
                <Upload className="mr-1 h-4 w-4" /> Restore…
              </Button>
            </div>
          </DangerZone>
        }
        result={
          output !== null ? (
            <Card className="p-4">
              <h2 className="section-head">Result</h2>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">
                {JSON.stringify(output, null, 2)}
              </pre>
            </Card>
          ) : null
        }
      />

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending?.kind === "create" && "Create a backup archive and download it."}
              {pending?.kind === "smoke" &&
                `Smoke-restore "${pending.path}" into a throwaway sandbox (does not touch live data).`}
              {pending?.kind === "restore" &&
                (pending.dry
                  ? `Dry-run restore of "${pending.fileName}" — no changes applied.`
                  : `LIVE RESTORE of "${pending.fileName}" — this OVERWRITES current data and cannot be undone.`)}
            </DialogDescription>
          </DialogHeader>
          {pending && pending.token.length > 0 && (
            <p className="font-mono text-xs text-amber-300">{pending.token}</p>
          )}
          <DialogFooter>
            {pending && pending.token.length > 0 ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" disabled={busy} onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="default" disabled={busy} onClick={confirmRun}>
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
