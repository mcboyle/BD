import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
import { DangerZone } from "@/components/ui/DangerZone";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { apiGet, apiPost, apiPostForm } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

// GUI parity (181 / Wave B) — per-site BODY-bearing writes. The sibling
// SiteActions page is a no-body suffix dispatcher (and the cockpit
// /cockpit/actions/site page is contracted no-body), so the three endpoints
// that take a JSON body live here. Surface-only: all three are pre-existing
// audited endpoints. Reset-cooldown and apply-login-template require a typed
// confirm; prune offers a non-destructive dry-run PREVIEW before the live,
// typed-confirmed prune (RebalanceCenter plan→preview→execute idiom).

interface PrunedRow {
  kind: string;
  role: string;
  selector: string;
  hits: number;
  misses: number;
  miss_ratio: number;
}
interface PruneResult extends OkResult {
  pruned?: PrunedRow[];
  count?: number;
  dry_run?: boolean;
}
interface LoginTemplate {
  id?: string;
  key?: string;
  name?: string;
  [k: string]: unknown;
}
interface LoginTemplatesResp {
  ok: boolean;
  login_templates?: LoginTemplate[];
}

interface CookieImportResult extends OkResult {
  count?: number;
  source?: string;
  cookie_file?: string;
}
interface BulkDeleteResult extends OkResult {
  removed?: number;
}
interface LearnedImportResult extends OkResult {
  imported?: Record<string, number>;
}
interface QueueImportResult extends OkResult {
  added?: number;
  mode?: string;
  site_id?: string;
}

const PRUNE_TOKEN = "PRUNE SELECTORS";
const BULKDEL_TOKEN = "DELETE URLS";
const QUEUE_APPEND_TOKEN = "IMPORT QUEUE";
const QUEUE_REPLACE_TOKEN = "REPLACE QUEUE";

export function SitePayloadActions() {
  const { siteId = "" } = useParams();
  const base = `/api/sites/${encodeURIComponent(siteId)}`;
  const [output, setOutput] = useState<unknown>(null);

  // ── reset cooldown ──────────────────────────────────────────────
  const [rcIdx, setRcIdx] = useState("0");
  const [rcAll, setRcAll] = useState(false);
  const [rcConfirm, setRcConfirm] = useState(false);

  // ── prune selectors ─────────────────────────────────────────────
  const [psMin, setPsMin] = useState("5");
  const [psRatio, setPsRatio] = useState("0.8");
  const [preview, setPreview] = useState<PruneResult | null>(null);
  const [psConfirm, setPsConfirm] = useState(false);

  // ── apply login template ────────────────────────────────────────
  const [ltId, setLtId] = useState("");
  const [ltConfirm, setLtConfirm] = useState(false);

  // ── import cookies (credential material) ─────────────────────────
  const [ckText, setCkText] = useState("");
  const [ckFile, setCkFile] = useState<File | null>(null);
  const [ckConfirm, setCkConfirm] = useState(false);

  // ── bulk delete by URL list ──────────────────────────────────────
  const [bdText, setBdText] = useState("");
  const [bdConfirm, setBdConfirm] = useState(false);

  // ── import learned block (OVERWRITES learned selectors) ──────────
  const [liText, setLiText] = useState("");
  const [liConfirm, setLiConfirm] = useState(false);

  // ── import queue snapshot (append | replace) ─────────────────────
  const [qiText, setQiText] = useState("");
  const [qiMode, setQiMode] = useState<"append" | "replace">("append");
  const [qiConfirm, setQiConfirm] = useState(false);

  const templatesQ = useQuery<LoginTemplatesResp>({
    queryKey: ["login_templates"],
    queryFn: () => apiGet<LoginTemplatesResp>("/api/login_templates"),
  });
  const templates = templatesQ.data?.login_templates ?? [];

  const finish = (res: OkResult, okMsg: string) => {
    setOutput(res);
    if (res.ok === false) toast.error(res.error || "action failed");
    else toast.success(okMsg);
  };

  const resetMut = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>(`/api/sites/${encodeURIComponent(siteId)}/accounts/reset_cooldown`,
        rcAll ? { all: true } : { account_index: parseInt(rcIdx, 10) }),
    onSuccess: (r) => finish(r, "Cooldown cleared"),
    onError: (e) => toast.error(e.message),
  });

  // dry_run=true preview (non-destructive)
  const previewMut = useMutation<PruneResult, Error, void>({
    mutationFn: () =>
      apiPost<PruneResult>(`/api/sites/${encodeURIComponent(siteId)}/prune_selectors`, {
        min_attempts: parseInt(psMin, 10),
        max_miss_ratio: parseFloat(psRatio),
        dry_run: true,
      }),
    onSuccess: (r) => {
      setOutput(r);
      setPreview(r);
      if (r.ok === false) toast.error(r.error || "preview failed");
      else if ((r.count ?? 0) === 0) toast.message("Nothing to prune at this threshold");
      else toast.success(`Preview: ${r.count} selector(s) would be pruned`);
    },
    onError: (e) => toast.error(e.message),
  });

  // dry_run=false live prune (mutating)
  const pruneMut = useMutation<PruneResult, Error, void>({
    mutationFn: () =>
      apiPost<PruneResult>(`/api/sites/${encodeURIComponent(siteId)}/prune_selectors`, {
        min_attempts: parseInt(psMin, 10),
        max_miss_ratio: parseFloat(psRatio),
        dry_run: false,
      }),
    onSuccess: (r) => {
      setPreview(null);
      finish(r, `Pruned ${r.count ?? 0} selector(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  const loginMut = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>(`/api/sites/${encodeURIComponent(siteId)}/login_template/apply`, { login_template: ltId }),
    onSuccess: (r) => finish(r, "Login template applied"),
    onError: (e) => toast.error(e.message),
  });

  // Import cookies: file (multipart) takes precedence, else pasted JSON text.
  // The endpoint validates the cookie JSON parses BEFORE writing anything, then
  // atomically repoints the site's cookie_file. We never render any cookie value —
  // only the returned count/source — and clear the inputs on success so credential
  // material does not linger in the field.
  const cookiesMut = useMutation<CookieImportResult, Error, void>({
    mutationFn: () => {
      if (ckFile) {
        const fd = new FormData();
        fd.append("file", ckFile);
        return apiPostForm<CookieImportResult>(`/api/sites/${encodeURIComponent(siteId)}/cookies/import`, fd);
      }
      return apiPost<CookieImportResult>(`/api/sites/${encodeURIComponent(siteId)}/cookies/import`, { text: ckText });
    },
    onSuccess: (r) => {
      if (r.ok !== false) {
        setCkText("");
        setCkFile(null);
      }
      finish(r, `Imported ${r.count ?? 0} cookie(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // Bulk delete a queue/history URL list. Body {urls:[...]}.
  const bulkDelMut = useMutation<BulkDeleteResult, Error, void>({
    mutationFn: () =>
      apiPost<BulkDeleteResult>(`/api/sites/${encodeURIComponent(siteId)}/bulk_delete`, { urls: bdUrls }),
    onSuccess: (r) => {
      if (r.ok !== false) setBdText("");
      finish(r, `Removed ${r.removed ?? 0} URL(s)`);
    },
    onError: (e) => toast.error(e.message),
  });

  // Import a learned block — OVERWRITES the site's learned selectors. The
  // endpoint keeps only the recognized top blocks (login/download/stats/
  // fingerprint) and returns a per-role count; we send the parsed JSON as-is
  // (the handler unwraps a {learned:{...}} envelope or takes the bare object).
  const learnedMut = useMutation<LearnedImportResult, Error, void>({
    mutationFn: () =>
      apiPost<LearnedImportResult>(`/api/sites/${encodeURIComponent(siteId)}/learned/import`, liParsed as object),
    onSuccess: (r) => {
      if (r.ok !== false) setLiText("");
      const n = r.imported ? Object.values(r.imported).reduce((a, b) => a + b, 0) : 0;
      finish(r, `Imported learned block (${n} selector(s))`);
    },
    onError: (e) => toast.error(e.message),
  });

  // Import a queue snapshot. mode "append" is additive (new URLs added,
  // existing left alone); "replace" wipes the current queue first. Body
  // {rows:[...], mode}.
  const queueMut = useMutation<QueueImportResult, Error, void>({
    mutationFn: () =>
      apiPost<QueueImportResult>(`/api/sites/${encodeURIComponent(siteId)}/queue/import`, { rows: qiRows, mode: qiMode }),
    onSuccess: (r) => {
      if (r.ok !== false) setQiText("");
      finish(r, `Queue ${r.mode ?? qiMode}: ${r.added ?? 0} added`);
    },
    onError: (e) => toast.error(e.message),
  });

  const minOk = Number.isInteger(Number(psMin)) && Number(psMin) >= 0;
  const ratioOk = !Number.isNaN(Number(psRatio)) && Number(psRatio) >= 0 && Number(psRatio) <= 1;
  const idxOk = rcAll || (Number.isInteger(Number(rcIdx)) && Number(rcIdx) >= 0);
  const previewCount = preview?.count ?? 0;
  const bdUrls = bdText.split(/\r?\n/).map((u) => u.trim()).filter(Boolean);
  const ckReady = !!ckFile || ckText.trim().length > 0;

  // learned: parse the pasted JSON; valid when it is a non-null object.
  let liParsed: unknown = null;
  let liErr = "";
  try { liParsed = liText.trim() ? JSON.parse(liText) : null; }
  catch { liErr = "invalid JSON"; }
  const liOk = !!liParsed && typeof liParsed === "object" && !Array.isArray(liParsed) && !liErr;

  // queue: accept either a bare rows array or a {rows:[...]} envelope.
  let qiRows: unknown[] = [];
  let qiErr = "";
  try {
    if (qiText.trim()) {
      const p = JSON.parse(qiText) as unknown;
      qiRows = Array.isArray(p)
        ? p
        : (Array.isArray((p as { rows?: unknown[] }).rows) ? (p as { rows: unknown[] }).rows : []);
    }
  } catch { qiErr = "invalid JSON"; }
  const qiOk = qiRows.length > 0 && !qiErr;
  const queueToken = qiMode === "replace" ? QUEUE_REPLACE_TOKEN : QUEUE_APPEND_TOKEN;

  const busy =
    resetMut.isPending ||
    previewMut.isPending ||
    pruneMut.isPending ||
    loginMut.isPending ||
    cookiesMut.isPending ||
    bulkDelMut.isPending ||
    learnedMut.isPending ||
    queueMut.isPending;

  return (
    <AppShell
      title={`Site payload actions — ${siteId}`}
      subtitle="Body-bearing per-site writes · gated"
      backTo={{ to: `/sites/${siteId}`, label: "Back to site" }}
      breadcrumb={`Sites › ${siteId} › Payload actions`}
    >
      <Link to={`/sites/${siteId}`} className="mb-3 inline-flex items-center text-sm text-ink-3">
        <ArrowLeft className="mr-1 h-4 w-4" /> Back to site
      </Link>

      <GatedWriteBanner className="mb-3">
        Each action requires a typed confirmation — nothing fires on a single click.
        Needs operator click-through validation.
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mb-3">
        Body-bearing per-site write actions: reset an account cooldown, prune
        low-yield selectors, apply a login template, and bulk-delete by URL. Each
        action is gated by a typed confirmation; the destructive ones are grouped
        in the danger zone below.
      </Callout>

      {/* Reset cooldown */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Reset account cooldown</h2>
        <p className="mb-2 text-xs text-ink-3">
          Force-clear a failed account's 24h cooldown. POSTs to <code>{base}/accounts/reset_cooldown</code>.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-44"
            value={rcIdx}
            disabled={rcAll}
            onChange={(e) => setRcIdx(e.target.value)}
            placeholder="account_index"
          />
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={rcAll} onChange={(e) => setRcAll(e.target.checked)} />
            all accounts
          </label>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || !idxOk}
            onClick={() => { setRcConfirm(true); }}
          >
            Reset cooldown
          </Button>
        </div>
      </Card>

      {/* Prune selectors */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Prune low-yield selectors</h2>
        <p className="mb-2 text-xs text-ink-3">
          Remove learned selectors above the miss-ratio threshold. Preview is a non-destructive dry-run;
          Execute mutates the site config. POSTs to <code>{base}/prune_selectors</code>.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="w-40" value={psMin} onChange={(e) => setPsMin(e.target.value)} placeholder="min_attempts" />
          <Input className="w-40" value={psRatio} onChange={(e) => setPsRatio(e.target.value)} placeholder="max_miss_ratio" />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !siteId || !minOk || !ratioOk}
            onClick={() => previewMut.mutate()}
          >
            Preview (dry run)
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || previewCount === 0}
            onClick={() => { setPsConfirm(true); }}
          >
            Execute prune
          </Button>
        </div>
        {preview && (preview.pruned?.length ?? 0) > 0 && (
          <div className="mt-3 max-h-56 overflow-auto rounded border border-border">
            <table className="bd-table w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="px-2 py-1">kind</th>
                  <th className="px-2 py-1">role</th>
                  <th className="px-2 py-1">selector</th>
                  <th className="px-2 py-1">hits</th>
                  <th className="px-2 py-1">misses</th>
                  <th className="px-2 py-1">miss_ratio</th>
                </tr>
              </thead>
              <tbody>
                {preview.pruned!.map((p, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-2 py-1">{p.kind}</td>
                    <td className="px-2 py-1">{p.role}</td>
                    <td className="break-all px-2 py-1">{p.selector}</td>
                    <td className="px-2 py-1">{p.hits}</td>
                    <td className="px-2 py-1">{p.misses}</td>
                    <td className="px-2 py-1">{p.miss_ratio}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Apply login template */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Apply login template</h2>
        <p className="mb-2 text-xs text-ink-3">
          Write a login template's selectors into the site (skips first-run manual teach).
          POSTs to <code>{base}/login_template/apply</code>.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="min-w-60 rounded border border-input bg-background px-3 py-2 text-sm"
            value={ltId}
            onChange={(e) => setLtId(e.target.value)}
          >
            <option value="">{templates.length ? "— choose a login template —" : "(none available)"}</option>
            {templates.map((t) => {
              const id = t.id || t.key || "";
              return (
                <option key={id} value={id}>
                  {(t.name || id) + (t.id ? ` (${t.id})` : "")}
                </option>
              );
            })}
          </select>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || !ltId}
            onClick={() => { setLtConfirm(true); }}
          >
            Apply login template
          </Button>
        </div>
      </Card>

      {/* Import cookies */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Import cookies</h2>
        <p className="mb-2 text-xs text-ink-3">
          Load a cookie JSON for this site — paste the text or choose a file. The file is validated
          (must parse as cookie JSON) before anything is written, then atomically saved into BD and the
          site is repointed. POSTs to <code>{base}/cookies/import</code>. Cookie values are never displayed.
        </p>
        <textarea
          className="mb-2 h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={ckText}
          disabled={!!ckFile}
          onChange={(e) => setCkText(e.target.value)}
          placeholder='[{"name":"...","value":"...","domain":"..."}]  (or choose a file below)'
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="file"
            accept=".json,application/json,text/plain"
            className="text-xs"
            onChange={(e) => setCkFile(e.target.files?.[0] ?? null)}
          />
          {ckFile && (
            <span className="text-xs text-ink-3">
              {ckFile.name} — paste field disabled while a file is selected
            </span>
          )}
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || !ckReady}
            onClick={() => { setCkConfirm(true); }}
          >
            Import cookies
          </Button>
        </div>
      </Card>

      {/* Bulk delete by URL list */}
      <DangerZone
        title="Bulk delete by URL list"
        warning="Permanently removes the listed queue/history entries — this cannot be undone."
        className="mb-3"
      >
        <p className="text-xs text-ink-3">
          Remove specific queue/history entries by URL — one URL per line. POSTs to{" "}
          <code>{base}/bulk_delete</code>. This is the selection-based delete; to clear the entire
          queue use the "Delete ALL jobs" action on the Site actions page.
        </p>
        <textarea
          className="h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={bdText}
          onChange={(e) => setBdText(e.target.value)}
          placeholder={"https://example.com/a\nhttps://example.com/b"}
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-ink-3">{bdUrls.length} URL(s) parsed</span>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || bdUrls.length === 0}
            onClick={() => { setBdConfirm(true); }}
          >
            Delete URLs
          </Button>
        </div>
      </DangerZone>

      {/* Import learned block */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Import learned block</h2>
        <p className="mb-2 text-xs text-ink-3">
          Load a previously-exported (or hand-written) learned block — paste the JSON. Only the
          recognized blocks (login / download / stats / fingerprint) are kept. POSTs to{" "}
          <code>{base}/learned/import</code>. This <strong>overwrites</strong> the site's current
          learned selectors.
        </p>
        <textarea
          className="mb-2 h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={liText}
          onChange={(e) => setLiText(e.target.value)}
          placeholder={'{"learned":{"download":{"video":["..."]}}}  (or a bare learned object)'}
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-ink-3">
            {liErr ? liErr : liOk ? "valid JSON object" : "paste a learned block"}
          </span>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || !liOk}
            onClick={() => { setLiConfirm(true); }}
          >
            Import learned block
          </Button>
        </div>
      </Card>

      {/* Import queue snapshot */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Import queue snapshot</h2>
        <p className="mb-2 text-xs text-ink-3">
          Import a queue snapshot — paste a rows array or a <code>{"{rows:[...]}"}</code> envelope.
          POSTs to <code>{base}/queue/import</code>. <strong>append</strong> adds new URLs and leaves
          existing ones alone; <strong>replace</strong> wipes the current queue first.
        </p>
        <textarea
          className="mb-2 h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={qiText}
          onChange={(e) => setQiText(e.target.value)}
          placeholder={'[{"url":"https://example.com/a","priority":1}]'}
        />
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-ink-3">
            mode
            <select
              className="rounded border border-input bg-background px-2 py-1 text-xs"
              value={qiMode}
              onChange={(e) => setQiMode(e.target.value === "replace" ? "replace" : "append")}
            >
              <option value="append">append</option>
              <option value="replace">replace</option>
            </select>
          </label>
          <span className="text-xs text-ink-3">
            {qiErr ? qiErr : `${qiRows.length} row(s) parsed`}
          </span>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy || !siteId || !qiOk}
            onClick={() => { setQiConfirm(true); }}
          >
            Import queue
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

      {/* Reset cooldown confirm */}
      <Dialog open={rcConfirm} onOpenChange={(o) => !o && setRcConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset account cooldown</DialogTitle>
            <DialogDescription>
              Force-clear cooldown on {rcAll ? "ALL cooled accounts" : `account ${rcIdx}`} for site "{siteId}".
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRcConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setRcConfirm(false); resetMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Prune execute confirm */}
      <Dialog open={psConfirm} onOpenChange={(o) => !o && setPsConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Execute selector prune</DialogTitle>
            <DialogDescription>
              Permanently prune the {previewCount} previewed selector(s) for site "{siteId}". This mutates the site config.
            </DialogDescription>
          </DialogHeader>
          <p className="font-mono text-xs text-amber-300">{PRUNE_TOKEN}</p>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setPsConfirm(false)}>No, cancel</Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => { setPsConfirm(false); pruneMut.mutate(); }}
            >
              Yes, proceed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Apply login template confirm */}
      <Dialog open={ltConfirm} onOpenChange={(o) => !o && setLtConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply login template</DialogTitle>
            <DialogDescription>
              Apply login template "{ltId}" to site "{siteId}". This overwrites the site's learned login selectors.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setLtConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setLtConfirm(false); loginMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Import cookies confirm */}
      <Dialog open={ckConfirm} onOpenChange={(o) => !o && setCkConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import cookies</DialogTitle>
            <DialogDescription>
              Replace the cookie file for site "{siteId}" with {ckFile ? `the selected file (${ckFile.name})` : "the pasted cookie JSON"}.
              The current session for this site will use the new cookies. Credential material — handle with care.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCkConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setCkConfirm(false); cookiesMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk delete confirm */}
      <Dialog open={bdConfirm} onOpenChange={(o) => !o && setBdConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bulk delete URLs</DialogTitle>
            <DialogDescription>
              Permanently remove {bdUrls.length} URL(s) from site "{siteId}". This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <p className="font-mono text-xs text-amber-300">{BULKDEL_TOKEN}</p>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setBdConfirm(false)}>No, cancel</Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => { setBdConfirm(false); bulkDelMut.mutate(); }}
            >
              Yes, proceed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import learned confirm */}
      <Dialog open={liConfirm} onOpenChange={(o) => !o && setLiConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import learned block</DialogTitle>
            <DialogDescription>
              Overwrite the learned selectors for site "{siteId}" with the pasted block. The previous
              learned selectors for this site are replaced.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setLiConfirm(false)}>Cancel</Button>
            <Button
              disabled={busy}
              onClick={() => { setLiConfirm(false); learnedMut.mutate(); }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import queue confirm */}
      <Dialog open={qiConfirm} onOpenChange={(o) => !o && setQiConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import queue snapshot ({qiMode})</DialogTitle>
            <DialogDescription>
              {qiMode === "replace"
                ? `Wipe the current queue for site "${siteId}" and import ${qiRows.length} row(s). This cannot be undone.`
                : `Append ${qiRows.length} row(s) to the queue for site "${siteId}" (existing URLs are left alone).`}
            </DialogDescription>
          </DialogHeader>
          {qiMode === "replace" && (
            <p className="font-mono text-xs text-amber-300">{queueToken}</p>
          )}
          <DialogFooter>
            {qiMode === "replace" ? (
              <>
                <Button autoFocus variant="default" onClick={() => setQiConfirm(false)}>No, cancel</Button>
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => { setQiConfirm(false); queueMut.mutate(); }}
                >
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setQiConfirm(false)}>Cancel</Button>
                <Button
                  disabled={busy}
                  onClick={() => { setQiConfirm(false); queueMut.mutate(); }}
                >
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
