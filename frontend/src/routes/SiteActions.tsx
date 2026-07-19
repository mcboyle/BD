import { useMutation } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
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
import { apiDelete, apiGet, apiPost } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";
import { actionSuffixWithIdx } from "@/lib/poolPath";

// GUI parity (177) — per-site no-body actions. Surfaces existing
// /api/sites/<sid>/* endpoints. Destructive actions require a yes/no confirm (No default);
// the rest a one-step confirm. Never one-click. Body-bearing site actions
// (imports, bulk_delete, prune, login_template) are handled separately.

type Act = {
  suffix: string;
  label: string;
  hard?: string; // destructive marker (yes/no, No default); absent → simple confirm
  needsIdx?: boolean; // account_pool/reset/<idx>
};

const GROUPS: { title: string; acts: Act[] }[] = [
  { title: "Lifecycle", acts: [
    { suffix: "start", label: "Start" },
    { suffix: "stop", label: "Stop" },
    { suffix: "watch/scan_now", label: "Scan now" },
  ] },
  { title: "Login flow", acts: [
    { suffix: "login", label: "Login" },
    { suffix: "login_verify", label: "Verify login" },
    { suffix: "manual_login", label: "Manual login" },
    { suffix: "login_manual_done", label: "Manual login done" },
    { suffix: "login_manual_cancel", label: "Manual login cancel" },
  ] },
  { title: "Accounts", acts: [
    { suffix: "accounts/rotate", label: "Rotate account" },
    { suffix: "account_pool/reset", label: "Reset pool slot", needsIdx: true, hard: "RESET POOL" },
  ] },
  { title: "Diagnostics", acts: [
    { suffix: "captcha/test", label: "Test captcha" },
    { suffix: "ai/detect_login", label: "AI detect login" },
  ] },
  { title: "Destructive", acts: [
    { suffix: "reset_learned", label: "Reset learned selectors", hard: "RESET LEARNED" },
    // v3.66.724: the bodyless bulk-delete control was REMOVED -- it was a DEAD CONTROL.
    //
    // It ran through the generic `${suffix}` mutation below, which posts {}. But
    // the bulk-delete endpoint validates {urls: [...]} and answers
    // 400 "urls must be a non-empty list". So the button failed 100% of the time --
    // AFTER the operator typed out its hard-confirm phrase.
    //
    // And the label lied twice over: that endpoint has NO "all" semantic at any body.
    // It deletes only the URLs you name. There was no request this button could have
    // sent that would do what it said. A dead control is worse than a missing one: a
    // missing one tells the truth, this one let you believe the jobs were gone.
    //
    // The control now lives where the URLs actually exist -- the queue selection
    // (Queue.tsx), which can send real urls and gets a real count back.
  ] },
];

export function SiteActions() {
  const { siteId = "" } = useParams();
  const [pending, setPending] = useState<Act | null>(null);
  const [idx, setIdx] = useState("0");
  const [output, setOutput] = useState<unknown>(null);
  const [runNowOpen, setRunNowOpen] = useState(false);

  const run = useMutation<OkResult, Error, string>({
    mutationFn: (suffix) => apiPost<OkResult>(`/api/sites/${encodeURIComponent(siteId)}/${suffix}`, {}),
    onSuccess: (res) => {
      setOutput(res);
      if (res.ok === false) toast.error(res.error || "action failed");
      else toast.success("Done");
    },
    onError: (e) => toast.error(e.message),
  });

  // P4-A.0 Cut 2 (v3.66.330) -- the per-site integration connection tests,
  // storage-tier manual sweep + status, and spillover-pick check that
  // previously lived ONLY in the legacy Add-Site editor. The SiteActions
  // generic `${suffix}` POST has TWO interpolations, so the parity scanner
  // can't resolve the trailing segment; each endpoint below is therefore
  // wired with a FULL /api/sites/${...}/<segment> literal so the scanner
  // credits it spa_wired. All are read-only GETs except the sweep (POST,
  // confirm-gated -- it moves files).
  const INTEGRATION_CHECKS: { label: string; call: (s: string) => Promise<unknown> }[] = [
    { label: "Test Plex", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/plex/diagnose`) },
    { label: "List Plex sections", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/plex/sections`) },
    { label: "Test Jellyfin", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/jellyfin/diagnose`) },
    { label: "List Jellyfin libraries", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/jellyfin/libraries`) },
    { label: "Test Stash", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/stash/diagnose`) },
    { label: "Test qBittorrent", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/qb/diagnose`) },
    { label: "Test JDownloader", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/jd/diagnose`) },
    { label: "Check JD coverage", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/jd/coverage`) },
  ];

  const STORAGE_HOOK_CHECKS: { label: string; call: (s: string) => Promise<unknown> }[] = [
    { label: "Refresh storage status", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/storage_tier/status`) },
    { label: "Check spillover pick", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/hooks/spillover_check`) },
    // v3.66.754c — inspect the accumulated URL-pattern fingerprint (previously dark).
    { label: "View URL fingerprint", call: (s) => apiGet(`/api/sites/${encodeURIComponent(s)}/heuristic/fingerprint`) },
  ];

  // Read-only test/diagnose/status GETs -> run directly, show the JSON result.
  const probe = useMutation<unknown, Error, (s: string) => Promise<unknown>>({
    mutationFn: (call) => call(siteId),
    onSuccess: (res) => {
      setOutput(res);
      const ok = (res as { ok?: boolean })?.ok;
      if (ok === false) toast.error((res as { error?: string })?.error || "check failed");
      else toast.success("Done");
    },
    onError: (e) => {
      setOutput({ error: e.message });
      toast.error(e.message);
    },
  });

  // Storage-tier manual sweep -- POST, confirm-gated (it relocates files).
  const runNow = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>(`/api/sites/${encodeURIComponent(siteId)}/storage_tier/run_now`, {}),
    onSuccess: (res) => {
      setOutput(res);
      if (res.ok === false) toast.error(res.error || "sweep failed");
      else toast.success("Sweep started");
    },
    onError: (e) => toast.error(e.message),
  });

  // v3.66.754c — reset the URL-pattern fingerprint (DELETE, previously dark).
  // Destructive (wipes the self-tuning signal), so confirm-gated like the sweep.
  const [resetFpOpen, setResetFpOpen] = useState(false);
  const resetFingerprint = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiDelete<OkResult>(
        `/api/sites/${encodeURIComponent(siteId)}/heuristic/fingerprint`,
      ),
    onSuccess: (res) => {
      setOutput(res);
      if (res.ok === false) toast.error(res.error || "reset failed");
      else toast.success("Fingerprint reset");
    },
    onError: (e) => toast.error(e.message),
  });

  const checksBusy =
    probe.isPending || runNow.isPending || resetFingerprint.isPending;

  const confirmRun = () => {
    if (!pending) return;
    // v3.66.336: the descriptor suffix already ends in the action verb
    // (e.g. "account_pool/reset"); append ONLY the index, never the verb again
    // (that produced the doubled-segment account_pool/reset/reset/<idx> 404).
    const suffix = actionSuffixWithIdx(pending.suffix, !!pending.needsIdx, idx);
    run.mutate(suffix);
    setPending(null);
  };

  return (
    <AppShell
      title={`Site actions — ${siteId}`}
      subtitle="Lifecycle · login · accounts · diagnostics"
      backTo={{ to: `/sites/${siteId}`, label: "Back to site" }}
      breadcrumb={`Sites › ${siteId} › Actions`}
    >
      <Link to={`/sites/${siteId}`} className="mb-3 inline-flex items-center text-sm text-muted-foreground">
        <ArrowLeft className="mr-1 h-4 w-4" /> Back to site
      </Link>
      {GROUPS.map((g) => (
        <Card key={g.title} className="mb-3 p-4">
          <h2 className={`mb-2 text-sm font-semibold ${g.title === "Destructive" ? "text-destructive" : ""}`}>
            {g.title}
          </h2>
          <div className="flex flex-wrap gap-2">
            {g.acts.map((a) => (
              <Button
                key={a.suffix}
                size="sm"
                variant={a.hard ? "destructive" : "outline"}
                disabled={run.isPending || !siteId}
                onClick={() => {
                  setIdx("0");
                  setPending(a);
                }}
              >
                {a.label}
              </Button>
            ))}
          </div>
        </Card>
      ))}

      <Card className="mb-3 p-4">
        <h2 className="section-head">Integration connection tests</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          Read-only checks against this site&apos;s configured media servers.
          Results show below.
        </p>
        <div className="flex flex-wrap gap-2">
          {INTEGRATION_CHECKS.map((c) => (
            <Button
              key={c.label}
              size="sm"
              variant="outline"
              disabled={checksBusy || !siteId}
              onClick={() => probe.mutate(c.call)}
            >
              {c.label}
            </Button>
          ))}
        </div>
      </Card>

      <Card className="mb-3 p-4">
        <h2 className="section-head">Storage tier · hooks</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={checksBusy || !siteId}
            onClick={() => setRunNowOpen(true)}
          >
            Run sweep now
          </Button>
          {STORAGE_HOOK_CHECKS.map((c) => (
            <Button
              key={c.label}
              size="sm"
              variant="outline"
              disabled={checksBusy || !siteId}
              onClick={() => probe.mutate(c.call)}
            >
              {c.label}
            </Button>
          ))}
          <Button
            size="sm"
            variant="outline"
            disabled={checksBusy || !siteId}
            onClick={() => setResetFpOpen(true)}
            title="Wipe the accumulated URL-pattern fingerprint (use when a site's CDN moves)"
          >
            Reset fingerprint
          </Button>
        </div>
      </Card>

      <Dialog open={resetFpOpen} onOpenChange={setResetFpOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset URL fingerprint?</DialogTitle>
            <DialogDescription>
              This wipes the self-tuning URL-pattern signal for{" "}
              <span className="font-mono">{siteId}</span>. It rebuilds from
              subsequent successful downloads. Use this when a site&apos;s CDN
              moves and the prior fingerprint is stale.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetFpOpen(false)}>
              No
            </Button>
            <Button
              onClick={() => {
                resetFingerprint.mutate();
                setResetFpOpen(false);
              }}
              disabled={resetFingerprint.isPending}
            >
              Yes, reset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {output !== null && (
        <Card className="p-4">
          <h2 className="section-head">Result</h2>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">
            {JSON.stringify(output, null, 2)}
          </pre>
        </Card>
      )}

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending && `${pending.label} for site "${siteId}".`}
            </DialogDescription>
          </DialogHeader>
          {pending?.needsIdx && (
            <div>
              <p className="mb-1 text-sm text-muted-foreground">Account pool slot index:</p>
              <Input value={idx} onChange={(e) => setIdx(e.target.value)} placeholder="0" />
            </div>
          )}
          {pending?.hard && (
            <>
              <p className="text-sm text-muted-foreground">
                This is destructive and cannot be undone. Proceed?
              </p>
              <p className="font-mono text-xs text-amber-300">{pending.hard}</p>
            </>
          )}
          <DialogFooter>
            {pending?.hard ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" disabled={run.isPending} onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="default" disabled={run.isPending} onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={runNowOpen} onOpenChange={setRunNowOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Run storage sweep now?</DialogTitle>
            <DialogDescription>
              Triggers the storage-tier sweep for site &quot;{siteId}&quot;. This
              relocates files between tiers per the site&apos;s policy.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRunNowOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="default"
              disabled={runNow.isPending}
              onClick={() => {
                runNow.mutate();
                setRunNowOpen(false);
              }}
            >
              Run sweep
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
