import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import type { OkResult, SitesV2 } from "@/lib/api-types";

// P4-A.0 Cut 1 (v3.66.329) -- the SPA Transform-URLs + Clear-done surface that
// replaces the two legacy command-palette actions (app.js `bulkTransformPrompt`
// and `act('clear')`) ahead of the Phase C /legacy deletion. Both target the
// per-site queue:
//   - Transform URLs -> POST /api/sites/<sid>/bulk_url_transform
//         {pattern, replacement, dry_run}  (dry-run preview, then commit)
//   - Clear done     -> POST /api/sites/<sid>/clear   (remove finished URLs)
// FULL /api/... literals (templated by <sid>) are required for the parity
// scanner to credit both endpoints spa_wired.

type Mode = "transform" | "clear" | "dead_letter";

// v3.66.754c — dead-letter list + per-row requeue (previously-dark controls).
interface DeadLetterJob {
  site_id?: string;
  url?: string;
  status?: string;
  message?: string;
  retries?: number;
  lane?: string;
}
interface DeadLetterList extends OkResult {
  jobs?: DeadLetterJob[];
}

interface TransformResult extends OkResult {
  matched?: number;
  changed?: number;
  committed?: number;
  sample?: { from: string; to: string }[];
  dry_run?: boolean;
  error?: string;
}

export function QueueOpsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("transform");
  const [siteId, setSiteId] = useState("");
  const [pattern, setPattern] = useState("");
  const [replacement, setReplacement] = useState("");
  const [preview, setPreview] = useState<TransformResult | null>(null);

  // Reuse the same site list the Sites page renders; only load it while the
  // dialog is open so a closed dialog adds no polling.
  const { data: sitesData, isLoading: sitesLoading } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    enabled: open,
  });
  const sites = sitesData?.sites ?? [];
  const effectiveSite = siteId || sites[0]?.site_id || "";

  const afterMutate = () => {
    qc.invalidateQueries({ queryKey: ["queue-v2"] });
    qc.invalidateQueries({ queryKey: ["sites-v2"] });
    qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
  };

  // Transform runs in two phases against the same endpoint: a dry-run that
  // returns up to 8 sample {from,to} pairs for the operator to verify, then a
  // commit. The preview is invalidated whenever the inputs change.
  const transformMut = useMutation<TransformResult, Error, { dryRun: boolean }>({
    mutationFn: ({ dryRun }) =>
      apiPost<TransformResult>(
        `/api/sites/${encodeURIComponent(effectiveSite)}/bulk_url_transform`,
        { pattern, replacement, dry_run: dryRun },
      ),
    onSuccess: (res, vars) => {
      if (res.ok === false) {
        toast.error(res.error || "Transform failed");
        return;
      }
      if (vars.dryRun) {
        setPreview(res);
        if ((res.changed ?? 0) === 0) {
          toast.info(
            `Matched ${res.matched ?? 0} URL(s) but nothing would change`,
          );
        }
      } else {
        toast.success(`Transformed ${res.committed ?? 0} URL(s)`);
        setPreview(null);
        setPattern("");
        setReplacement("");
        afterMutate();
        onOpenChange(false);
      }
    },
    onError: (e) => toast.error(e.message || "Transform failed"),
  });

  const clearMut = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>(
        `/api/sites/${encodeURIComponent(effectiveSite)}/clear`,
        {},
      ),
    onSuccess: () => {
      toast.success("Cleared finished URLs");
      afterMutate();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.message || "Clear failed"),
  });

  // Dead-letter list (GET /api/queue/dead_letter) — loaded only in that mode so
  // it never polls when the dialog is on another tab. FULL literal for the parity
  // scanner.
  const { data: deadLetterData, isLoading: deadLetterLoading } =
    useQuery<DeadLetterList>({
      queryKey: ["queue-dead-letter"],
      queryFn: ({ signal }) =>
        apiGet<DeadLetterList>("/api/queue/dead_letter", signal),
      enabled: open && mode === "dead_letter",
    });
  const deadLetterJobs = deadLetterData?.jobs ?? [];

  // Requeue one dead-lettered job (POST /api/queue/dead_letter/requeue). Wired
  // TOGETHER with the list above so the action always has a visible surface.
  const requeueMut = useMutation<
    OkResult,
    Error,
    { site_id: string; url: string }
  >({
    mutationFn: (body) =>
      apiPost<OkResult>("/api/queue/dead_letter/requeue", body),
    onSuccess: (res) => {
      if (res.ok) {
        toast.success("Requeued");
        qc.invalidateQueries({ queryKey: ["queue-dead-letter"] });
        afterMutate();
      } else {
        toast.error(res.error || "Requeue failed");
      }
    },
    onError: (e) => toast.error(e.message || "Requeue failed"),
  });

  const busy =
    transformMut.isPending || clearMut.isPending || requeueMut.isPending;
  const noSites = !sitesLoading && sites.length === 0;
  const canPreview = !!effectiveSite && !busy && pattern.length > 0;
  const canApply =
    canPreview && !!preview && preview.dry_run === true && (preview.changed ?? 0) > 0;

  // Any input change invalidates a stale preview so Apply can't commit a regex
  // the operator hasn't previewed.
  const onPatternChange = (v: string) => {
    setPattern(v);
    setPreview(null);
  };
  const onReplacementChange = (v: string) => {
    setReplacement(v);
    setPreview(null);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Queue operations</DialogTitle>
          <DialogDescription>
            Bulk find/replace across queued URLs, or clear finished URLs from a
            site&apos;s queue.
          </DialogDescription>
        </DialogHeader>

        {noSites ? (
          <p className="text-sm text-ink-3">
            No sites configured yet. Add a site first.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {/* mode toggle */}
            <div className="flex gap-1 rounded-md bg-surface-2 p-1">
              {(["transform", "clear", "dead_letter"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={
                    "flex-1 rounded-sm px-3 py-1.5 text-sm " +
                    (mode === m
                      ? "bg-surface text-ink shadow-sm"
                      : "text-ink-3 hover:text-ink")
                  }
                >
                  {m === "transform"
                    ? "Transform URLs"
                    : m === "clear"
                      ? "Clear done"
                      : "Dead letter"}
                </button>
              ))}
            </div>

            {/* site selector */}
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-ink-2">Site</span>
              <select
                value={effectiveSite}
                onChange={(e) => {
                  setSiteId(e.target.value);
                  setPreview(null);
                }}
                disabled={sitesLoading}
                className="hairline rounded-md bg-surface px-2 py-1.5 text-sm"
              >
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>

            {mode === "transform" ? (
              <>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-ink-2">Find (regex)</span>
                  <Input
                    value={pattern}
                    onChange={(e) => onPatternChange(e.target.value)}
                    placeholder="https://cdn1\\."
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-ink-2">
                    Replace ($1, $2 for capture groups; empty = delete match)
                  </span>
                  <Input
                    value={replacement}
                    onChange={(e) => onReplacementChange(e.target.value)}
                    placeholder="https://cdn2."
                  />
                </label>

                {preview ? (
                  <div className="flex flex-col gap-1 text-xs">
                    <span className="text-ink-2">
                      {preview.changed ?? 0} of {preview.matched ?? 0} matched
                      URL(s) would change
                    </span>
                    <div className="hairline max-h-48 overflow-y-auto rounded-md">
                      {(preview.sample ?? []).map((s, i) => (
                        <div
                          key={i}
                          className="border-b border-hairline px-2 py-1 font-mono"
                        >
                          <div className="text-red-400 line-through opacity-70">
                            {s.from}
                          </div>
                          <div className="text-green-400">{s.to}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </>
            ) : mode === "clear" ? (
              <p className="text-sm text-ink-3">
                Removes finished/completed URLs from the selected site&apos;s
                queue. In-progress and pending URLs are untouched.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-sm text-ink-3">
                  Terminal jobs (retry-exhausted or dependency-blocked). Requeue
                  sends one back to pending with its retry counter cleared.
                </p>
                {deadLetterLoading ? (
                  <p className="text-sm text-ink-3">Loading…</p>
                ) : deadLetterJobs.length === 0 ? (
                  <p className="text-sm text-ink-3">
                    No dead-lettered jobs.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {deadLetterJobs.map((j, i) => (
                      <li
                        key={`${j.site_id}:${j.url}:${i}`}
                        className="flex items-center justify-between gap-2 rounded-md bg-surface-2 px-2 py-1.5 text-sm"
                      >
                        <span className="min-w-0 flex-1 truncate" title={j.url}>
                          <span className="text-ink-3">{j.site_id}</span>{" "}
                          {j.url}
                          {j.message ? (
                            <span className="text-ink-3"> — {j.message}</span>
                          ) : null}
                        </span>
                        <Button
                          variant="outline"
                          disabled={busy || !j.site_id || !j.url}
                          onClick={() =>
                            requeueMut.mutate({
                              site_id: j.site_id ?? "",
                              url: j.url ?? "",
                            })
                          }
                        >
                          Requeue
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          {mode === "transform" ? (
            canApply ? (
              <Button
                onClick={() => transformMut.mutate({ dryRun: false })}
                disabled={busy}
              >
                {busy ? "Applying\u2026" : `Apply (${preview?.changed ?? 0})`}
              </Button>
            ) : (
              <Button
                onClick={() => transformMut.mutate({ dryRun: true })}
                disabled={!canPreview}
              >
                {busy ? "Previewing\u2026" : "Preview"}
              </Button>
            )
          ) : mode === "clear" ? (
            <Button
              onClick={() => clearMut.mutate()}
              disabled={!effectiveSite || busy}
            >
              {busy ? "Clearing\u2026" : "Clear done"}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
