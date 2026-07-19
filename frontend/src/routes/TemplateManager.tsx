import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileCheck2, FileWarning, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Callout } from "@/components/ui/Callout";
import { apiGet, apiPost } from "@/lib/api-client";
import type {
  TemplateActionResult,
  TemplateManagerEntry,
  TemplateManagerList,
} from "@/lib/api-types";
import { TemplateAuthoringSection } from "@/components/sections/TemplateAuthoringSection";
import { SemanticSearchPanel } from "@/components/ui/SemanticSearchPanel";

// v3.66.149 (#10) — Template Manager. Lists templates/reviewed/* and
// templates/drafts/* with status / host / selector groups / resolutions /
// redacted network patterns / lint warnings, and lets the operator promote a
// draft (refused if it has blocking-lint selectors; never auto-enabled) or
// disable a reviewed template. Read paths expose no cookie/token/storage data.

// v3.66.186 (T19a) — POST /api/template/extract_login returns the learned
// login block derived from a pasted login-page's HTML. Pure rule-based derive
// (no AI, no side-effect, nothing persisted), so it is a read-shaped tool: no
// typed confirm. Sits beside the rule-based download extractor.
interface LoginExtractResult {
  ok: boolean;
  error?: string;
  login?: {
    user_field?: string | null;
    pass_field?: string | null;
    submit_btn?: string | null;
  };
  form_action?: string | null;
  warnings?: string[];
}

function TemplateRow({
  t,
  action,
}: {
  t: TemplateManagerEntry;
  action: React.ReactNode;
}) {
  return (
    <Card className="hairline border bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {t.has_blocking_lint ? (
              <ShieldAlert className="h-4 w-4 shrink-0 text-red" aria-hidden />
            ) : t.enabled ? (
              <FileCheck2 className="h-4 w-4 shrink-0 text-green" aria-hidden />
            ) : (
              <FileWarning className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
            )}
            <span className="truncate text-sm font-medium text-ink">
              {t.host ?? t.file}
            </span>
            <span
              className={
                "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider " +
                (t.enabled
                  ? "bg-green-soft text-green"
                  : "bg-ink-1/40 text-ink-3")
              }
            >
              {t.status ?? "—"}
            </span>
          </div>
          <div className="mt-1 text-xs text-ink-3">
            {t.resolutions.length > 0 && (
              <>Res {t.resolutions.slice(0, 5).join("/")} · </>
            )}
            {t.selectors.length} selector groups · {t.network_patterns.length}{" "}
            patterns
          </div>
          {t.has_blocking_lint && (
            <div className="mt-1 text-xs font-medium text-red">
              {t.lint_warnings.find((w) => w.level === "error")?.message ??
                "unsafe selector — fix before promoting"}
            </div>
          )}
        </div>
        <div className="shrink-0">{action}</div>
      </div>
    </Card>
  );
}

export function TemplateManager() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<TemplateManagerList>({
    queryKey: ["template-manager"],
    queryFn: ({ signal }) =>
      apiGet<TemplateManagerList>("/api/template_manager", signal),
    refetchOnWindowFocus: false,
  });

  const promoteMut = useMutation<
    TemplateActionResult,
    Error,
    { file: string; accept_api: boolean }
  >({
    mutationFn: ({ file, accept_api }) =>
      apiPost<TemplateActionResult>("/api/template_manager/promote", {
        file,
        enable: true,
        accept_api,
      }),
    onSuccess: (res) => {
      if (res.ok)
        toast.success(
          res.api_accepted
            ? `Promoted ${res.promoted} (API endpoints accepted)`
            : `Promoted ${res.promoted}`,
        );
      else toast.error(res.error ?? "Promote failed");
      qc.invalidateQueries({ queryKey: ["template-manager"] });
    },
    onError: (err) => toast.error(`Promote failed: ${err.message}`),
  });

  const disableMut = useMutation<TemplateActionResult, Error, string>({
    mutationFn: (file) =>
      apiPost<TemplateActionResult>("/api/template_manager/disable", { file }),
    onSuccess: (res) => {
      if (res.ok) toast.success(`Disabled ${res.disabled}`);
      else toast.error(res.error ?? "Disable failed");
      qc.invalidateQueries({ queryKey: ["template-manager"] });
    },
    onError: (err) => toast.error(`Disable failed: ${err.message}`),
  });

  // Confirm gate — promote/disable mutate live template state, so they are
  // never one-click: arm a confirmation dialog first (the backend audit + CSRF
  // still apply on the actual POST). null = no dialog open.
  const [pending, setPending] = useState<{
    action: "promote" | "disable";
    file: string;
    label: string;
    apiCandidate?: { base: string; endpoints: string[] } | null;
  } | null>(null);
  // A6-1: opt-in to materialize the derived API block. Defaults OFF every time
  // the dialog arms — accepting the API is a deliberate per-promote decision.
  const [acceptApi, setAcceptApi] = useState(false);

  const busy = promoteMut.isPending || disableMut.isPending;

  function runPending() {
    if (!pending) return;
    if (pending.action === "promote")
      promoteMut.mutate({
        file: pending.file,
        // never send accept_api unless a candidate exists AND it was checked
        accept_api: !!pending.apiCandidate && acceptApi,
      });
    else disableMut.mutate(pending.file);
    setPending(null);
    setAcceptApi(false);
  }

  // ── Learn login selectors (T19a) ─────────────────────────────────────
  // Paste a login page's HTML → derive user/password/submit selectors.
  // No side-effect (nothing is saved), so no confirm — it's a tool, not a
  // write. The operator copies the result into a template by hand.
  const [loginHtml, setLoginHtml] = useState("");
  const [loginResult, setLoginResult] = useState<LoginExtractResult | null>(
    null,
  );
  const extractLoginMut = useMutation<LoginExtractResult, Error, string>({
    mutationFn: (html) =>
      apiPost<LoginExtractResult>("/api/template/extract_login", { html }),
    onSuccess: (res) => {
      setLoginResult(res);
      if (res.ok) toast.success("Login selectors derived");
      else toast.error(res.error ?? "Extract failed");
    },
    onError: (err) => toast.error(`Extract failed: ${err.message}`),
  });

  const trailing = (
    <Link
      to="/settings/advanced"
      className="grid h-8 w-8 place-items-center rounded-sm text-ink-3 hover:bg-surface-2 hover:text-ink"
      aria-label="Back to advanced"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden />
    </Link>
  );

  return (
    <AppShell
      title="Template Manager"
      subtitle="Reviewed + draft templates"
      trailing={trailing}
    >
      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* Slice helper card (UI convergence #4) — what this page manages +
              the safe boundary. References only existing controls. */}
          <Callout tone="info" title="What this page manages">
            Reviewed templates are the ones the runtime may apply; you can
            enable or disable each here. Drafts are listed separately and are
            never auto-enabled — promoting one with unsafe selectors is refused
            until it's fixed. Build new templates from the{" "}
            <span className="text-ink">Live capture workflow</span>.
          </Callout>
          <section className="space-y-2">
            <h2 className="eyebrow">
              Reviewed ({data?.reviewed.length ?? 0})
            </h2>
            {(data?.reviewed ?? []).map((t) => (
              <TemplateRow
                key={t.file}
                t={t}
                action={
                  t.enabled ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setPending({
                          action: "disable",
                          file: t.file,
                          label: t.host ?? t.file,
                        })
                      }
                      disabled={busy}
                    >
                      Disable
                    </Button>
                  ) : (
                    <span className="text-[11px] text-ink-3">disabled</span>
                  )
                }
              />
            ))}
            {(data?.reviewed.length ?? 0) === 0 && (
              <p className="text-xs text-ink-3">No reviewed templates.</p>
            )}
          </section>

          <section className="space-y-2">
            <h2 className="eyebrow">
              Drafts ({data?.drafts.length ?? 0})
            </h2>
            <p className="text-xs text-ink-3">
              Drafts are never auto-enabled. Promoting one with unsafe selectors
              is refused — fix it first.
            </p>
            {(data?.drafts ?? []).map((t) => (
              <TemplateRow
                key={t.file}
                t={t}
                action={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setPending({
                        action: "promote",
                        file: t.file,
                        label: t.host ?? t.file,
                        apiCandidate: t.api_candidate ?? null,
                      })
                    }
                    disabled={busy || t.has_blocking_lint}
                    aria-label={`Promote ${t.host ?? t.file}`}
                  >
                    Promote
                  </Button>
                }
              />
            ))}
            {(data?.drafts.length ?? 0) === 0 && (
              <p className="text-xs text-ink-3">No drafts.</p>
            )}
          </section>

          <section className="space-y-2">
            <h2 className="eyebrow">
              Learn login selectors
            </h2>
            <p className="text-xs text-ink-3">
              Paste a login page's HTML to derive the user / password / submit
              selectors. Rule-based and read-only — nothing is saved; copy the
              result into a site's login template yourself.
            </p>
            <textarea
              className="h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs"
              value={loginHtml}
              onChange={(e) => setLoginHtml(e.target.value)}
              placeholder="<form>…</form>  (paste the login form's HTML)"
              aria-label="Login page HTML"
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => extractLoginMut.mutate(loginHtml)}
                disabled={extractLoginMut.isPending || !loginHtml.trim()}
              >
                {extractLoginMut.isPending ? "Deriving…" : "Derive selectors"}
              </Button>
              {loginResult && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setLoginResult(null);
                    setLoginHtml("");
                  }}
                >
                  Clear
                </Button>
              )}
            </div>
            {loginResult && loginResult.ok && (
              <Card className="hairline border bg-surface p-3 text-xs">
                <dl className="space-y-1">
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-ink-3">user_field</dt>
                    <dd className="break-all font-mono text-ink">
                      {loginResult.login?.user_field ?? "—"}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-ink-3">pass_field</dt>
                    <dd className="break-all font-mono text-ink">
                      {loginResult.login?.pass_field ?? "—"}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-ink-3">submit_btn</dt>
                    <dd className="break-all font-mono text-ink">
                      {loginResult.login?.submit_btn ?? "—"}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-ink-3">form_action</dt>
                    <dd className="break-all font-mono text-ink">
                      {loginResult.form_action ?? "—"}
                    </dd>
                  </div>
                </dl>
                {(loginResult.warnings?.length ?? 0) > 0 && (
                  <ul className="mt-2 list-disc space-y-0.5 pl-4 text-ink-3">
                    {loginResult.warnings!.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                )}
              </Card>
            )}
          </section>
        </div>
      )}

      <Dialog
        open={!!pending}
        onOpenChange={(o) => {
          if (!o) {
            setPending(null);
            setAcceptApi(false);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending?.action === "promote"
                ? "Promote draft?"
                : "Disable template?"}
            </DialogTitle>
            <DialogDescription>
              {pending?.action === "promote"
                ? `Enable "${pending?.label}" as a reviewed template — it will start matching live captures. This action is audited.`
                : `Disable "${pending?.label}" — it will no longer match captures. This action is audited.`}
            </DialogDescription>
          </DialogHeader>
          {pending?.action === "promote" && pending.apiCandidate && (
            <div className="rounded border border-input bg-surface p-3 text-xs">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={acceptApi}
                  onChange={(e) => setAcceptApi(e.target.checked)}
                  aria-label="Accept derived API endpoints"
                />
                <span>
                  <span className="font-medium text-ink">
                    Also accept the derived API endpoints
                  </span>
                  <span className="block text-ink-3">
                    Enables first-party API URL building for this template
                    (otherwise gated). Off by default — accept only after
                    reviewing the base and paths below.
                  </span>
                </span>
              </label>
              <dl className="mt-2 space-y-1">
                <div className="flex gap-2">
                  <dt className="w-16 shrink-0 text-ink-3">base</dt>
                  <dd className="break-all font-mono text-ink">
                    {pending.apiCandidate.base}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-16 shrink-0 text-ink-3">paths</dt>
                  <dd className="break-all font-mono text-ink">
                    {pending.apiCandidate.endpoints.join(", ") || "—"}
                  </dd>
                </div>
              </dl>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setPending(null);
                setAcceptApi(false);
              }}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button onClick={runPending} disabled={busy}>
              {pending?.action === "promote" ? "Promote" : "Disable"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <TemplateAuthoringSection />
      {/* v3.66.743 — semantic recall over captures + the template corpus */}
      <SemanticSearchPanel />
    </AppShell>
  );
}
