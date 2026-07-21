import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { TakeoverViewer } from "@/components/TakeoverViewer";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api-client";
import { downloadSitesTemplate, useBulkSitesCsv } from "@/hooks/useOpsControls";
import type { OkResult } from "@/lib/api-types";

// GUI parity (180, Wave A) — Imports & Captcha Queue. Surfaces five existing
// operator endpoints under the risk model: captcha-queue actions operate on an
// already-pending item (one-step confirm), while the two payload imports are
// gated with a confirm dialog. Surface-only — never reimplements the underlying
// operation; never one-click. The endpoint literals below are exactly what the
// parity inventory's SPA-wiring scanner matches.

type CaptchaItem = {
  url: string;
  site_id?: string;
  captcha_type?: string;
  status?: string;
  title?: string;
  solve_session_id?: string | null;
  // MOD-1 C-6: effective takeover mode + downgrade reason + KasmVNC viewer URL,
  // persisted onto the polled pending state so the cockpit shows what is running.
  mode?: string | null;
  mode_reason?: string | null;
  vnc_url?: string | null;
};
type CaptchaPending = { ok?: boolean; pending?: CaptchaItem[] };
type ImportResult = OkResult & { added?: number; skipped?: number };

// Cut 3 — read-only import-preview shapes (mirror the backend contract).
type UtPreviewItem = {
  id?: string;
  name?: string;
  status: "new" | "changed" | "conflict" | "invalid";
  secrets_omitted: string[];
  error?: string;
};
type UtPreview = {
  ok: boolean;
  mode: "merge" | "replace";
  counts: {
    new: number;
    changed: number;
    conflict: number;
    invalid: number;
    destructive: number;
    secrets_omitted: number;
  };
  items: UtPreviewItem[];
  destructive: { id?: string; name?: string }[];
  errors: string[];
};
type MpPreview = {
  ok: boolean;
  site_id: string;
  status?: "new" | "changed";
  config_preview?: Record<string, unknown>;
  secrets_omitted?: string[];
  warnings?: string[];
  errors?: string[];
};

type Pending =
  | { kind: "captchaStart"; url: string; token: "" }
  | { kind: "captchaResolved"; url: string; token: "" }
  | { kind: "captchaDismiss"; url: string; token: "" }
  | { kind: "templatesImport"; payload: unknown; merge: boolean; token: "" }
  | { kind: "marketplaceImport"; body: Record<string, unknown>; token: "" }
  // T4 (v3.66.207): bulk site creation from pasted CSV — one-step confirm;
  // per-row results render for review after the run.
  | { kind: "bulkSites"; csv: string; token: "" };

const isTyped = (p: Pending): boolean => p.token.length > 0;

// Cut 3 — read-only preview renderers (shown in the WorkflowPage `plan` slot).
function UtPreviewPanel({ p }: { p: UtPreview }) {
  if (!p.ok) {
    return (
      <Card className="p-3">
        <h3 className="text-sm font-semibold text-red">Templates preview failed</h3>
        <ul className="mt-1 text-xs text-ink-3">
          {(p.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      </Card>
    );
  }
  const c = p.counts;
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold text-ink-1">
        Templates import preview ({p.mode})
      </h3>
      <p className="mt-1 text-xs text-ink-3">
        {c.new} new · {c.changed} changed · {c.conflict} conflict · {c.invalid} invalid
        {c.destructive ? ` · ${c.destructive} removed` : ""}
        {c.secrets_omitted ? ` · ${c.secrets_omitted} with secrets omitted` : ""}
      </p>
      {p.items.length > 0 && (
        <ul className="mt-2 max-h-40 divide-y divide-border overflow-auto rounded border border-border bg-black/20 text-xs">
          {p.items.map((it, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-2 px-2 py-1">
              <span className="font-medium text-foreground">{it.name ?? it.id ?? "(unnamed)"}</span>
              <span className="text-ink-3">{it.status}</span>
              {it.secrets_omitted.length > 0 && (
                <span className="text-amber-300">secrets omitted: {it.secrets_omitted.join(", ")}</span>
              )}
              {it.error && <span className="text-red-300">{it.error}</span>}
            </li>
          ))}
        </ul>
      )}
      {p.destructive.length > 0 && (
        <p className="mt-2 text-xs text-red-300">
          Replace would remove: {p.destructive.map((d) => d.name ?? d.id).join(", ")}
        </p>
      )}
      {p.errors.length > 0 && (
        <ul className="mt-2 text-xs text-amber-300">
          {p.errors.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
    </Card>
  );
}

function MpPreviewPanel({ p }: { p: MpPreview }) {
  if (!p.ok) {
    return (
      <Card className="p-3">
        <h3 className="text-sm font-semibold text-red">Bundle preview failed</h3>
        <ul className="mt-1 text-xs text-ink-3">
          {(p.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      </Card>
    );
  }
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold text-ink-1">
        Bundle preview · {p.site_id} ({p.status})
      </h3>
      {(p.secrets_omitted ?? []).length > 0 && (
        <p className="mt-1 text-xs text-amber-300">
          secrets omitted: {(p.secrets_omitted ?? []).join(", ")}
        </p>
      )}
      {(p.warnings ?? []).length > 0 && (
        <ul className="mt-1 text-xs text-amber-200">
          {(p.warnings ?? []).map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
        {JSON.stringify(p.config_preview ?? {}, null, 2)}
      </pre>
    </Card>
  );
}

export function ImportsCenter() {
  const qc = useQueryClient();
  const [utJson, setUtJson] = useState("");
  const [utMerge, setUtMerge] = useState(true);
  const [mpBundle, setMpBundle] = useState("");
  const [mpTarget, setMpTarget] = useState("");
  const [mpVerify, setMpVerify] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  // Cut 3 — read-only preview state (shown before an apply).
  const [utPreview, setUtPreview] = useState<UtPreview | null>(null);
  const [mpPreview, setMpPreview] = useState<MpPreview | null>(null);
  // T4 (v3.66.207): bulk site import.
  const bulkSites = useBulkSitesCsv();
  const [bulkCsv, setBulkCsv] = useState("");

  const captcha = useQuery<CaptchaPending, Error>({
    queryKey: ["captcha", "pending"],
    queryFn: () => apiGet<CaptchaPending>("/api/captcha/pending"),
    refetchInterval: 5000,
  });

  const okToast = (msg: string) => (res: OkResult) =>
    res.ok === false ? toast.error(res.error || "failed") : toast.success(msg);

  const invalidateCaptcha = () => qc.invalidateQueries({ queryKey: ["captcha", "pending"] });

  const captchaStart = useMutation<OkResult, Error, string>({
    mutationFn: (url) => apiPost<OkResult>("/api/captcha/start_solve", { url }),
    onSuccess: (res) => {
      okToast("Solve session started")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });
  const captchaResolved = useMutation<OkResult, Error, string>({
    mutationFn: (url) => apiPost<OkResult>("/api/captcha/resolved", { url }),
    onSuccess: (res) => {
      okToast("Marked resolved")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });
  const captchaDismiss = useMutation<OkResult, Error, string>({
    mutationFn: (url) => apiPost<OkResult>("/api/captcha/dismiss", { url }),
    onSuccess: (res) => {
      okToast("Dismissed")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });

  const templatesImport = useMutation<ImportResult, Error, { payload: unknown; merge: boolean }>({
    mutationFn: ({ payload, merge }) =>
      apiPost<ImportResult>(`/api/user_templates/import?merge=${merge ? "1" : "0"}`, payload),
    onSuccess: (res) =>
      res.ok === false
        ? toast.error(res.error || "import failed")
        : toast.success(
            `Templates imported${res.added != null ? ` (added ${res.added}, skipped ${res.skipped})` : ""}`,
          ),
    onError: (e) => toast.error(e.message),
  });
  const marketplaceImport = useMutation<OkResult, Error, Record<string, unknown>>({
    mutationFn: (body) => apiPost<OkResult>("/api/marketplace/import", body),
    onSuccess: okToast("Bundle imported"),
    onError: (e) => toast.error(e.message),
  });

  // Cut 3 — read-only PREVIEW-before-apply. Classifies what an import would do
  // (new/changed/conflict/destructive/secrets-omitted) without writing.
  const templatesPreview = useMutation<UtPreview, Error, { payload: unknown; merge: boolean }>({
    mutationFn: ({ payload, merge }) =>
      apiPost<UtPreview>(`/api/user_templates/import/preview?merge=${merge ? "1" : "0"}`, payload),
    onSuccess: (r) => {
      setUtPreview(r);
      if (!r.ok) toast.error(r.errors?.[0] || "preview failed");
      else toast.success("Preview ready — review before importing");
    },
    onError: (e) => toast.error(e.message),
  });
  const marketplacePreview = useMutation<MpPreview, Error, Record<string, unknown>>({
    mutationFn: (body) => apiPost<MpPreview>("/api/marketplace/import/preview", body),
    onSuccess: (r) => {
      setMpPreview(r);
      if (!r.ok) toast.error(r.errors?.[0] || "preview failed");
      else toast.success("Bundle preview ready");
    },
    onError: (e) => toast.error(e.message),
  });

  const busy =
    captchaStart.isPending ||
    captchaResolved.isPending ||
    captchaDismiss.isPending ||
    templatesImport.isPending ||
    marketplaceImport.isPending ||
    templatesPreview.isPending ||
    marketplacePreview.isPending ||
    bulkSites.isPending;

  const arm = (p: Pending) => {
    setPending(p);
  };

  const armTemplatesImport = () => {
    const raw = utJson.trim();
    if (!raw) {
      toast.error("Paste an exported templates payload first");
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    arm({ kind: "templatesImport", payload, merge: utMerge, token: "" });
  };

  const armMarketplaceImport = () => {
    const raw = mpBundle.trim();
    if (!raw) {
      toast.error("Paste a marketplace bundle first");
      return;
    }
    let bundle: unknown;
    try {
      bundle = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    const body: Record<string, unknown> = { bundle };
    if (mpTarget.trim()) body.target_site_id = mpTarget.trim();
    if (mpVerify.trim()) body.verify_with = mpVerify.trim();
    arm({ kind: "marketplaceImport", body, token: "" });
  };

  // Cut 3 — preview handlers (parse the same payload, hit the read-only route).
  const previewTemplatesImport = () => {
    const raw = utJson.trim();
    if (!raw) {
      toast.error("Paste an exported templates payload first");
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setUtPreview(null);
    templatesPreview.mutate({ payload, merge: utMerge });
  };

  const previewMarketplaceImport = () => {
    const raw = mpBundle.trim();
    if (!raw) {
      toast.error("Paste a marketplace bundle first");
      return;
    }
    let bundle: unknown;
    try {
      bundle = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    const body: Record<string, unknown> = { bundle };
    if (mpTarget.trim()) body.target_site_id = mpTarget.trim();
    if (mpVerify.trim()) body.verify_with = mpVerify.trim();
    setMpPreview(null);
    marketplacePreview.mutate(body);
  };

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "captchaStart":
        captchaStart.mutate(pending.url);
        break;
      case "captchaResolved":
        captchaResolved.mutate(pending.url);
        break;
      case "captchaDismiss":
        captchaDismiss.mutate(pending.url);
        break;
      case "templatesImport":
        templatesImport.mutate({ payload: pending.payload, merge: pending.merge });
        break;
      case "marketplaceImport":
        marketplaceImport.mutate(pending.body);
        break;
      case "bulkSites":
        bulkSites.mutate(
          { csv: pending.csv },
          {
            onSuccess: (r) =>
              r.error
                ? toast.error(r.error)
                : toast.success(`Created ${r.created ?? 0} sites — see per-row results`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
    }
    setPending(null);
  };

  const items = captcha.data?.pending ?? [];

  const confirmLabel = (p: Pending): string => {
    switch (p.kind) {
      case "captchaStart":
        return `Start a manual solve session for:\n  ${p.url}`;
      case "captchaResolved":
        return `Mark this captcha resolved and signal the worker to retry?\n  ${p.url}`;
      case "captchaDismiss":
        return `Dismiss (give up on) this captcha?\n  ${p.url}`;
      case "templatesImport":
        return p.merge
          ? "Import user templates (merge — keeps existing)."
          : "REPLACE ALL user templates with the imported set. This is destructive and cannot be undone.";
      case "marketplaceImport":
        return "Import this marketplace bundle into the install.";
      case "bulkSites":
        return `Bulk-create sites from the pasted CSV (${p.csv.split("\n").filter((l) => l.trim()).length} non-empty lines). Per-row results follow.`;
    }
  };

  return (
    <AppShell title="Imports · Captcha Queue" subtitle="Act on pending captchas · import templates / marketplace bundles">
      <GatedWriteBanner level="chip">
        Imports and captcha-queue actions confirm via dialog; nothing
        confirm before firing — nothing fires on a single click; every request is audited by the
        underlying endpoint. <b>Needs operator click-through validation.</b>
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mt-3">
        Act on pending captcha challenges and import data into BulkDownloader —
        user templates, a marketplace bundle, or a bulk site list from CSV/XLSX.
        Imports confirm via dialog; replacing all templates is destructive and is
        flagged inline where you choose it.
      </Callout>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Captcha queue</h2>
        <p className="mb-2 text-xs text-ink-3">
          Pending challenges awaiting a manual solve. Acting on a queued item is not destructive to stored data.
        </p>
        {captcha.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length === 0 ? (
          <p className="py-6 text-center text-sm text-ink-3">No pending captchas.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <tbody>
              {items.map((p) => (
                <tr key={p.url} className="border-b border-border/40">
                  <td className="py-1.5 pr-3 align-top">
                    <div className="text-ink-2">
                      {p.captcha_type || "?"} · {p.status || ""}
                    </div>
                    <div className="text-xs text-ink-3">
                      {p.site_id || ""}
                      {p.title ? ` · ${p.title}` : ""}
                    </div>
                    <div className="break-all font-mono text-xs text-emerald-200/70">{p.url}</div>
                    {p.status === "solving" && p.solve_session_id ? (
                      <TakeoverViewer
                        sid={p.solve_session_id}
                        mode={p.mode}
                        vncUrl={p.vnc_url}
                        reason={p.mode_reason}
                      />
                    ) : null}
                  </td>
                  <td className="py-1.5 text-right align-top">
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaStart", url: p.url, token: "" })}
                      >
                        Start solve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaResolved", url: p.url, token: "" })}
                      >
                        Resolved
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaDismiss", url: p.url, token: "" })}
                      >
                        Dismiss
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <WorkflowPage
        inputs={
          <>
      <Card className="mt-4 p-4">
        <h2 className="section-head">Import user templates</h2>
        <p className="mb-2 text-xs text-ink-3">
          Paste a payload previously produced by the user-templates export. Merge keeps existing
          templates; Replace is destructive (replaces all user templates).
        </p>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder='{"templates":[...]}'
          value={utJson}
          onChange={(e) => setUtJson(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={utMerge} onChange={(e) => setUtMerge(e.target.checked)} />
            merge (uncheck = replace all, destructive)
          </label>
          <Button variant="outline" disabled={busy} onClick={previewTemplatesImport}>
            Preview
          </Button>
          <Button variant="outline" disabled={busy} onClick={armTemplatesImport}>
            Import templates
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Import marketplace bundle</h2>
        <p className="mb-2 text-xs text-ink-3">
          Paste a marketplace bundle (JSON object). Optional target site id and verify secret.
        </p>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder='{"name":"...","config":{...}}'
          value={mpBundle}
          onChange={(e) => setMpBundle(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="target_site_id (optional)"
            value={mpTarget}
            onChange={(e) => setMpTarget(e.target.value)}
          />
          <Input
            className="max-w-[220px]"
            placeholder="verify_with (optional)"
            value={mpVerify}
            onChange={(e) => setMpVerify(e.target.value)}
          />
          <Button variant="outline" disabled={busy} onClick={previewMarketplaceImport}>
            Preview
          </Button>
          <Button variant="outline" disabled={busy} onClick={armMarketplaceImport}>
            Import bundle
          </Button>
        </div>
      </Card>

      {/* ── T4 (v3.66.207): bulk site import ─────────────────────────── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Bulk site import</h2>
        <p className="mb-2 text-xs text-ink-3">
          Download a template, fill it, paste the CSV back here. Each row becomes a site; results
          come back per-row so failures are reviewable.
        </p>
        <div className="mb-2 flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => downloadSitesTemplate("csv")}>
            CSV template
          </Button>
          <Button variant="outline" onClick={() => downloadSitesTemplate("xlsx")}>
            XLSX template
          </Button>
        </div>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder="name,login_url,template,…"
          value={bulkCsv}
          onChange={(e) => setBulkCsv(e.target.value)}
        />
        <div className="mt-2">
          <Button
            variant="outline"
            disabled={busy || !bulkCsv.trim()}
            onClick={() => arm({ kind: "bulkSites", csv: bulkCsv, token: "" })}
          >
            Import sites
          </Button>
        </div>
        {bulkSites.data?.results && (
          <table className="bd-table mt-3 w-full text-xs">
            <tbody>
              {bulkSites.data.results.map((r, i) => (
                <tr key={i} className="border-b border-border/40">
                  <td className="py-1 pr-2 text-ink-3">line {r.line ?? "—"}</td>
                  <td className="py-1 pr-2">{r.name ?? ""}</td>
                  <td
                    className={
                      r.status === "error" ? "py-1 text-red-300" : "py-1 text-emerald-300"
                    }
                  >
                    {r.status}
                    {r.error ? ` — ${r.error}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
          </>
        }
        plan={
          utPreview || mpPreview ? (
            <div className="space-y-3">
              {utPreview && <UtPreviewPanel p={utPreview} />}
              {mpPreview && <MpPreviewPanel p={mpPreview} />}
            </div>
          ) : undefined
        }
      />

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending && isTyped(pending)
                ? "This is destructive and cannot be undone. Proceed?"
                : "Confirm this operation."}
            </DialogDescription>
          </DialogHeader>
          {pending && (
            <p className="whitespace-pre-wrap text-sm text-amber-200">{confirmLabel(pending)}</p>
          )}
          {pending && isTyped(pending) && (
            <p className="font-mono text-xs text-amber-300">{pending.token}</p>
          )}
          <DialogFooter>
            {pending && isTyped(pending) ? (
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
