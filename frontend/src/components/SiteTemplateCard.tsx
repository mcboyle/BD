import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheck2, FilePlus2, FileSearch, KeyRound, Save, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";
import type { SessionReuseResult, TemplateOnboardResult, TemplateStatus } from "@/lib/api-types";

// v3.66.144 (Goal 2) — reviewed-template visibility on the site page.
//
// Surfaces, for the site's host:
//   - whether an enabled reviewed template exists ("Reviewed Template:
//     <host> enabled") vs. capture-required vs. none,
//   - a short detail line (resolutions / selector groups / auto-teach),
//   - a "Run onboarding" button → POST /template_onboard, which classifies
//     the site and, when a capture is required, launches the capture flow
//     (approved sites never launch). The status query is invalidated on
//     success so the badge reflects the new state.
//
// Read-only status comes from GET /template_status (no tokens/cookies).

export function SiteTemplateCard({ siteId }: { siteId: string }) {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<TemplateStatus>({
    queryKey: ["template-status", siteId],
    queryFn: ({ signal }) =>
      apiGet<TemplateStatus>(
        `/api/sites/${encodeURIComponent(siteId)}/template_status`,
        signal,
      ),
    refetchOnWindowFocus: false,
  });

  const onboardMut = useMutation<TemplateOnboardResult, Error, void>({
    mutationFn: () =>
      apiPost<TemplateOnboardResult>(
        `/api/sites/${encodeURIComponent(siteId)}/template_onboard`,
        { run: true },
      ),
    onSuccess: (res) => {
      if (res.template_onboarding === "approved_template_found") {
        toast.success("Approved template found — no capture needed");
      } else if (res.launched) {
        toast.success("Capture launched — review the draft when it finishes");
      } else {
        toast("Capture required — run the capture flow to build a draft");
      }
      qc.invalidateQueries({ queryKey: ["template-status", siteId] });
    },
    onError: (err) => toast.error(`Onboarding failed: ${err.message}`),
  });

  // 3e/C1 — reuse the authenticated onboarding session for downloads. Copies
  // the login-continuity state (cookies incl. cf_clearance, storage) from the
  // onboarding capture profile into the runtime download profiles. Session
  // reuse, not challenge-solving. Value-free response.
  const reuseMut = useMutation<SessionReuseResult, Error, void>({
    mutationFn: () =>
      apiPost<SessionReuseResult>(
        `/api/sites/${encodeURIComponent(siteId)}/session/reuse_onboarding`,
        {},
      ),
    onSuccess: (res) => {
      if (res.reused) {
        const where = res.seeded.map((s) => s.profile).join(", ");
        toast.success(
          `Onboarding session reused for downloads${where ? ` (${where})` : ""}`,
        );
      } else {
        toast(res.skipped_reason || "No onboarding session found to reuse");
      }
      qc.invalidateQueries({ queryKey: ["template-status", siteId] });
    },
    onError: (err) => toast.error(`Session reuse failed: ${err.message}`),
  });

  // CAP-CANCEL — stop an in-flight onboarding capture. The onboarding launch is
  // a detached subprocess (no cockpit task_id), so we POST the site-scoped
  // cancel endpoint, which drops the per-capture .CANCEL sentinel; the status
  // query is invalidated so the in-flight badge + this control clear.
  const cancelMut = useMutation<{ ok: boolean; cancelled: boolean }, Error, void>({
    mutationFn: () =>
      apiPost<{ ok: boolean; cancelled: boolean }>(
        `/api/sites/${encodeURIComponent(siteId)}/template_capture_cancel`,
        {},
      ),
    onSuccess: (res) => {
      toast(res.cancelled ? "Capture cancelled — discarded" : "No capture in flight");
      qc.invalidateQueries({ queryKey: ["template-status", siteId] });
    },
    onError: (err) => toast.error(`Cancel failed: ${err.message}`),
  });

  const finishMut = useMutation<{ ok: boolean; finished: boolean }, Error, void>({
    mutationFn: () =>
      apiPost<{ ok: boolean; finished: boolean }>(
        `/api/sites/${encodeURIComponent(siteId)}/template_capture_finish`,
        {},
      ),
    onSuccess: (res) => {
      toast(res.finished ? "Capture finished — building draft…" : "No capture in flight");
      qc.invalidateQueries({ queryKey: ["template-status", siteId] });
    },
    onError: (err) => toast.error(`Finish failed: ${err.message}`),
  });

  const captureInFlight = data?.capture_in_flight ?? false;

  const enabled = data?.template?.enabled ?? false;
  const onboarding = data?.onboarding ?? null;
  const autoTeach = data?.auto_teach_first_run;

  let detail: string;
  if (isLoading) {
    detail = "";
  } else if (enabled && data) {
    const res = data.template.resolutions.slice(0, 4).join("/");
    const teach = autoTeach ? "on" : "off";
    detail = `${res ? `Resolutions ${res} · ` : ""}${data.template.selectors.length} selector groups · auto-teach ${teach}`;
  } else if (onboarding === "capture_required") {
    detail = `Capture required — run onboarding to build a reviewed draft. Auto-teach ${autoTeach ? "on" : "off"}.`;
  } else {
    detail = "No reviewed template for this host yet.";
  }

  return (
    <Card className="hairline border bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-0.5 flex items-center gap-2">
            {enabled ? (
              <FileCheck2 className="h-4 w-4 text-green" aria-hidden />
            ) : (
              <FileSearch className="h-4 w-4 text-ink-3" aria-hidden />
            )}
            <span className="eyebrow">
              Template
            </span>
          </div>
          <div className="truncate text-sm font-medium text-ink">
            {isLoading ? "Checking reviewed templates…" : (data?.label ?? "No reviewed template")}
          </div>
          {detail && <div className="mt-1 text-xs text-ink-3">{detail}</div>}
          {data?.download_template?.enabled && data.download_template.host && (
            <div className="mt-1 text-xs text-amber-dim" data-testid="download-host-template">
              An enabled host-level template ({data.download_template.host}) will
              apply at download time.
            </div>
          )}
          {data?.has_blocking_lint && (
            <div className="mt-1 text-xs font-medium text-red">
              Unsafe selector in this template — review in Template Manager
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onboardMut.mutate()}
            disabled={onboardMut.isPending || isLoading}
            aria-label="Run template onboarding for this site"
          >
            <FilePlus2 className="h-3.5 w-3.5" aria-hidden />
            {onboardMut.isPending ? "Running…" : "Run onboarding"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => reuseMut.mutate()}
            disabled={reuseMut.isPending || isLoading}
            aria-label="Reuse the onboarding session for downloads"
          >
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
            {reuseMut.isPending ? "Reusing…" : "Reuse onboarding session"}
          </Button>
          {captureInFlight && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => finishMut.mutate()}
                disabled={finishMut.isPending}
                aria-label="Finish and save the in-flight onboarding capture for this site"
              >
                <Save className="h-3.5 w-3.5" aria-hidden />
                {finishMut.isPending ? "Finishing…" : "Finish & Save"}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => cancelMut.mutate()}
                disabled={cancelMut.isPending}
                aria-label="Cancel the in-flight onboarding capture for this site"
              >
                <XCircle className="h-3.5 w-3.5" aria-hidden />
                {cancelMut.isPending ? "Cancelling…" : "Cancel capture"}
              </Button>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}
