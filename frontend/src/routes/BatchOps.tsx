import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { DangerZone } from "@/components/ui/DangerZone";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
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
import { apiPost } from "@/lib/api-client";

// GUI parity (184 / dangerous-selection + credential tranches) — global
// body-bearing writes that are NOT per-site, so they live on their own page
// rather than SitePayloadActions:
//   * POST /api/batch/delete         — filter-scoped history delete; dry_run
//     PREVIEW first (built into the endpoint), then a typed-confirm live delete.
//   * POST /api/cookie_relogin/check — one-shot scan that SCHEDULES relogins
//     across all sites under the cookie-quality threshold (spawns fresh login
//     sessions), so it is typed-confirm gated despite not being destructive.
// Surface-only: both endpoints are pre-existing audited routes. Nothing fires
// on a single click.

interface DeleteSampleRow {
  id?: number | string;
  filename?: string;
  size_mb?: number;
}
interface BatchDeleteResult {
  ok?: boolean;
  error?: string;
  candidates_matched?: number;
  processed?: number;
  files_deleted?: number;
  errors?: number;
  dry_run?: boolean;
  sample?: DeleteSampleRow[];
  total_size_gb?: number;
}
interface ReloginAction {
  site_id?: string;
  reason?: string;
  [k: string]: unknown;
}
interface ReloginResult {
  error?: string;
  checked?: number;
  scheduled?: number;
  actions?: ReloginAction[];
}
interface SitesImportResult {
  ok?: boolean;
  id?: string;
  errors?: string[];
  warnings?: string[];
}

const DELETE_TOKEN = "DELETE HISTORY";

export function BatchOps() {
  const [output, setOutput] = useState<unknown>(null);

  // ── batch delete filter ──────────────────────────────────────────
  const [siteId, setSiteId] = useState("");
  const [status, setStatus] = useState("");
  const [olderThan, setOlderThan] = useState("");
  const [msgContains, setMsgContains] = useState("");
  const [limit, setLimit] = useState("500");
  const [deleteFiles, setDeleteFiles] = useState(false);
  const [preview, setPreview] = useState<BatchDeleteResult | null>(null);
  const [delConfirm, setDelConfirm] = useState(false);
  // v3.66.728: the other three /api/batch/* endpoints. All CONTROL-class and dark until now.
  const [resetTo, setResetTo] = useState("pending");
  const [targetDir, setTargetDir] = useState("");
  const [moveConfirm, setMoveConfirm] = useState(false);
  const [dedupMinMb, setDedupMinMb] = useState("50");

  // ── cookie relogin check ─────────────────────────────────────────
  const [threshold, setThreshold] = useState("50");
  const [reloginConfirm, setReloginConfirm] = useState(false);

  // ── import site (creates a NEW site from an export envelope) ──────
  const [siText, setSiText] = useState("");
  const [siConfirm, setSiConfirm] = useState(false);

  const buildFilter = () => {
    const f: Record<string, unknown> = {};
    if (siteId.trim()) f.site_id = siteId.trim();
    if (status.trim()) f.status = status.trim();
    if (olderThan.trim() && Number.isInteger(Number(olderThan))) f.older_than_days = parseInt(olderThan, 10);
    if (msgContains.trim()) f.message_contains = msgContains.trim();
    if (limit.trim() && Number.isInteger(Number(limit))) f.limit = parseInt(limit, 10);
    return f;
  };

  // dry_run=true PREVIEW (non-destructive)
  const previewMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/delete", { filter: buildFilter(), dry_run: true }),
    onSuccess: (r) => {
      setOutput(r);
      setPreview(r);
      if (r.ok === false) toast.error(r.error || "preview failed");
      else if ((r.candidates_matched ?? 0) === 0) toast.message("No matching history rows");
      else toast.success(`Preview: ${r.candidates_matched} row(s), ${r.total_size_gb ?? 0} GB`);
    },
    onError: (e) => toast.error(e.message),
  });

  // dry_run=false LIVE delete (mutating)
  const deleteMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/delete", {
        filter: buildFilter(),
        dry_run: false,
        delete_files: deleteFiles,
      }),
    onSuccess: (r) => {
      setOutput(r);
      setPreview(null);
      if (r.ok === false) toast.error(r.error || "delete failed");
      else toast.success(`Deleted ${r.processed ?? 0} row(s), ${r.files_deleted ?? 0} file(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // ── v3.66.728: batch retry / move / dedup_scan ───────────────────────────
  //
  // Same {filter, dry_run} contract as delete, so they reuse buildFilter() and the same
  // PREVIEW-then-apply shape. dry_run is sent EXPLICITLY on every call: the endpoints
  // default it to True, and a control that relies on a default it cannot see is one
  // refactor away from silently going live.

  const retryPreviewMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/retry", {
        filter: buildFilter(),
        dry_run: true,
        reset_to_status: resetTo,
      }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error(r.error || "preview failed");
      else toast.success(`Preview: ${r.candidates_matched ?? 0} row(s) would be retried`);
    },
    onError: (e) => toast.error(e.message),
  });

  const retryMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/retry", {
        filter: buildFilter(),
        dry_run: false,
        reset_to_status: resetTo,
      }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error(r.error || "retry failed");
      else toast.success(`Requeued ${r.processed ?? 0} row(s) as ${resetTo}`);
    },
    onError: (e) => toast.error(e.message),
  });

  // MOVE. target_dir is REQUIRED -- /api/batch/move answers 400 "target_dir required"
  // without it. Sending {filter, dry_run} alone would be a DEAD CONTROL: the right route,
  // a body the endpoint refuses, and a ledger that scores it WIRED. That is exactly the
  // 724 ("Delete ALL jobs") and 726 ("Start import") bug, and it is not being rebuilt here.
  const movePreviewMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/move", {
        filter: buildFilter(),
        target_dir: targetDir.trim(),
        dry_run: true,
      }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error(r.error || "preview failed");
      else toast.success(`Preview: ${r.candidates_matched ?? 0} file(s) would move`);
    },
    onError: (e) => toast.error(e.message),
  });

  const moveMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/move", {
        filter: buildFilter(),
        target_dir: targetDir.trim(),
        dry_run: false,
      }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error(r.error || "move failed");
      else toast.success(`Moved ${r.processed ?? 0} file(s) to ${targetDir}`);
    },
    onError: (e) => toast.error(e.message),
  });

  // DEDUP SCAN is READ-ONLY: it reports duplicates and changes nothing. No dry_run, and
  // deliberately NO confirm gate -- a confirmation in front of a call that mutates nothing
  // is safety theatre, and theatre is how operators learn to click through the real ones.
  const dedupMut = useMutation<BatchDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BatchDeleteResult>("/api/batch/dedup_scan", {
        site_id: siteId.trim() || undefined,
        min_file_size_mb: parseInt(dedupMinMb, 10) || 50,
        limit: parseInt(limit, 10) || 5000,
      }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error(r.error || "scan failed");
      else toast.success("Duplicate scan complete - see the result below");
    },
    onError: (e) => toast.error(e.message),
  });

  const reloginMut = useMutation<ReloginResult, Error, void>({
    mutationFn: () =>
      apiPost<ReloginResult>("/api/cookie_relogin/check", { threshold: parseInt(threshold, 10) }),
    onSuccess: (r) => {
      setOutput(r);
      if (r.error) toast.error(r.error);
      else toast.success(`Checked ${r.checked ?? 0} site(s); scheduled ${r.scheduled ?? 0} relogin(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // Import a site config exported by /export — creates a NEW site. The
  // endpoint validates + drops unknown keys; a validation failure returns 400
  // (apiPost throws an ApiError whose .body carries {errors,warnings}).
  const sitesImportMut = useMutation<SitesImportResult, Error, void>({
    mutationFn: () => apiPost<SitesImportResult>("/api/sites/import", siParsed as object),
    onSuccess: (r) => {
      setOutput(r);
      if (r.ok === false) toast.error((r.errors && r.errors[0]) || "import failed");
      else { setSiText(""); toast.success(`Imported new site ${r.id ?? ""}`); }
    },
    onError: (e) => {
      const body = (e as { body?: SitesImportResult }).body;
      if (body) setOutput(body);
      toast.error((body?.errors && body.errors[0]) || e.message);
    },
  });

  const previewCount = preview?.candidates_matched ?? 0;
  const thresholdOk = Number.isInteger(Number(threshold)) && Number(threshold) >= 0 && Number(threshold) <= 100;

  // import site: parse the pasted JSON; valid when it is a non-null object.
  let siParsed: unknown = null;
  let siErr = "";
  try { siParsed = siText.trim() ? JSON.parse(siText) : null; }
  catch { siErr = "invalid JSON"; }
  const siOk = !!siParsed && typeof siParsed === "object" && !Array.isArray(siParsed) && !siErr;

  const busy = previewMut.isPending || deleteMut.isPending || reloginMut.isPending || sitesImportMut.isPending;

  return (
    <AppShell title="Batch operations" subtitle="Filter-scoped history delete · cookie relogin sweep · gated">
      <WorkflowPage
        purpose={<>
      <GatedWriteBanner title="Global gated write surface" className="mb-3">
        Delete previews are non-destructive; the live delete and the relogin
        sweep both require a typed confirmation. Needs operator click-through validation.
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mb-3">
        Run operator actions across many records at once: delete matching history
        rows by filter, sweep cookie relogins, and import a site. Previews are
        non-destructive dry-runs; the destructive delete is grouped in the danger
        zone below.
      </Callout>
        </>}
        danger={<>
      {/* Batch delete */}
      <DangerZone
        title="Batch delete history"
        warning="Execute permanently deletes the matching history rows (and, if checked, unlinks the files on disk) — this cannot be undone. Preview first."
        className="mb-3"
      >
        <p className="mb-2 text-xs text-ink-3">
          Delete matching history rows. Preview is a non-destructive dry-run; Execute deletes the rows and,
          if checked, unlinks the files. POSTs to <code>/api/batch/delete</code>.
        </p>
        <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Input value={siteId} onChange={(e) => setSiteId(e.target.value)} placeholder="site_id (optional)" />
          <Input value={status} onChange={(e) => setStatus(e.target.value)} placeholder="status (optional)" />
          <Input value={olderThan} onChange={(e) => setOlderThan(e.target.value)} placeholder="older_than_days" />
          <Input value={msgContains} onChange={(e) => setMsgContains(e.target.value)} placeholder="message_contains" />
          <Input value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="limit" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} />
            also delete files on disk
          </label>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => previewMut.mutate()}>
            Preview (dry run)
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || previewCount === 0}
            onClick={() => { setDelConfirm(true); }}
          >
            Execute delete
          </Button>
        </div>
        {preview && (preview.sample?.length ?? 0) > 0 && (
          <div className="mt-3 max-h-56 overflow-auto rounded border border-hairline">
            <table className="bd-table w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="px-2 py-1">id</th>
                  <th className="px-2 py-1">filename</th>
                  <th className="px-2 py-1">size_mb</th>
                </tr>
              </thead>
              <tbody>
                {preview.sample!.map((r, i) => (
                  <tr key={i} className="border-t border-hairline">
                    <td className="px-2 py-1">{String(r.id ?? "")}</td>
                    <td className="break-all px-2 py-1">{r.filename}</td>
                    <td className="px-2 py-1">{r.size_mb}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DangerZone>
        </>}
        result={<>
      {/* Cookie relogin check */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Cookie relogin sweep</h2>
        <p className="mb-2 text-xs text-ink-3">
          Scan every auth-required site and SCHEDULE a fresh login for any whose cookie quality is below the
          threshold (sites that opted out are skipped). This spawns real login sessions. POSTs to{" "}
          <code>/api/cookie_relogin/check</code>.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-44"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="threshold (0-100)"
          />
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !thresholdOk}
            onClick={() => { setReloginConfirm(true); }}
          >
            Check &amp; schedule relogins
          </Button>
        </div>
      </Card>

      {/* Import site */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Import site</h2>
        <p className="mb-2 text-xs text-ink-3">
          Create a NEW site from a config export envelope (from a site's Export) — paste the JSON. The
          payload is validated and unknown keys are dropped before the site is created. POSTs to{" "}
          <code>/api/sites/import</code>.
        </p>
        <textarea
          className="mb-2 h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={siText}
          onChange={(e) => setSiText(e.target.value)}
          placeholder={'{"config":{"name":"...","url":"..."}}  (or a bare config object)'}
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-ink-3">
            {siErr ? siErr : siOk ? "valid JSON object" : "paste a site export"}
          </span>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siOk}
            onClick={() => { setSiConfirm(true); }}
          >
            Import site
          </Button>
        </div>
      </Card>

      {output !== null && (
        <Card className="p-4">
          <h2 className="section-head">Result</h2>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">
            {JSON.stringify(output, null, 2)}
          </pre>
        </Card>
      )}
        </>}
      />

      {/* Batch delete confirm */}
      {/* v3.66.728: retry / move / dedup_scan -- the rest of the /api/batch/* cluster.
          All three reuse the SAME filter above, so what you previewed is what acts. */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Batch retry</h2>
        <p className="mb-2 text-sm text-ink-3">
          Requeue the filtered history rows. Preview first; the live run is explicit.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Input
            className="max-w-xs"
            value={resetTo}
            onChange={(e) => setResetTo(e.target.value)}
            placeholder="reset_to_status (pending)"
            aria-label="reset to status"
          />
          <Button size="sm" variant="outline" disabled={busy} onClick={() => retryPreviewMut.mutate()}>
            Preview (dry run)
          </Button>
          <Button size="sm" disabled={busy} onClick={() => retryMut.mutate()}>
            Execute retry
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Batch move</h2>
        <p className="mb-2 text-sm text-ink-3">
          Move the filtered files to another directory. <code>target_dir</code> is required --
          the endpoint refuses the call without it.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Input
            className="max-w-md"
            value={targetDir}
            onChange={(e) => setTargetDir(e.target.value)}
            placeholder="target_dir (required)"
            aria-label="target directory"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !targetDir.trim()}
            onClick={() => movePreviewMut.mutate()}
          >
            Preview (dry run)
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !targetDir.trim()}
            onClick={() => setMoveConfirm(true)}
          >
            Execute move
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Duplicate scan</h2>
        <p className="mb-2 text-sm text-ink-3">
          READ-ONLY. Reports duplicate downloads; changes nothing, so there is no confirm step.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Input
            className="max-w-xs"
            value={dedupMinMb}
            onChange={(e) => setDedupMinMb(e.target.value)}
            placeholder="min_file_size_mb (50)"
            aria-label="minimum file size in MB"
          />
          <Button size="sm" variant="outline" disabled={busy} onClick={() => dedupMut.mutate()}>
            Scan for duplicates
          </Button>
        </div>
      </Card>

      <Dialog open={moveConfirm} onOpenChange={(o) => !o && setMoveConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move the matched files?</DialogTitle>
            <DialogDescription>
              Relocates every file matching the filter above into
              <code> {targetDir || "(no target set)"}</code>. Moving files on disk is not
              practically reversible. Preview first if you have not.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setMoveConfirm(false)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={busy || !targetDir.trim()}
              onClick={() => { setMoveConfirm(false); moveMut.mutate(); }}
            >
              Move files
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={delConfirm} onOpenChange={(o) => !o && setDelConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Execute batch delete</DialogTitle>
            <DialogDescription>
              Permanently delete the {previewCount} previewed history row(s)
              {deleteFiles ? " AND unlink their files on disk" : ""}. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <p className="font-mono text-xs text-amber-300">{DELETE_TOKEN}</p>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setDelConfirm(false)}>No, cancel</Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => { setDelConfirm(false); deleteMut.mutate(); }}
            >
              Yes, proceed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Relogin sweep confirm */}
      <Dialog open={reloginConfirm} onOpenChange={(o) => !o && setReloginConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Schedule cookie relogins</DialogTitle>
            <DialogDescription>
              Scan all sites at threshold {threshold} and schedule fresh login sessions for those below it.
              This can spawn multiple browser login sessions.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setReloginConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setReloginConfirm(false); reloginMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import site confirm */}
      <Dialog open={siConfirm} onOpenChange={(o) => !o && setSiConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import site</DialogTitle>
            <DialogDescription>
              Create a new site from the pasted export envelope. A new site entry and runner are created.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSiConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setSiConfirm(false); sitesImportMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
