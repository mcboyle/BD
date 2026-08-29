import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ExternalLink,
  Info,
  Lock,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Collapsible } from "@/components/ui/collapsible";
import { FieldCard as Field } from "@/components/ui/FieldCard";
import { WorkflowSteps } from "@/components/ui/WorkflowSteps";
import { SecretField } from "@/components/SecretField";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { apiGet, apiPost, apiPut } from "@/lib/api-client";
import {
  DEFAULT_SITE_MIN_RESOLUTION,
  DEFAULT_SITE_QUALITY_PREFERENCE,
  type SiteConfigDraft,
  type ValidationResult,
} from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Add Site wizard (V3 visual redesign).
//
// This is the load-bearing UX of D3 — it's the reason the redesign
// exists. The v3.63.10 cookie_file=directory bug only made it to
// runtime because the old UI didn't validate inline.
//
// v3.66.326 changes:
//   - Create no longer gates success on a non-existent `ok` field. The
//     backend POST /api/sites returns {id} on success (HTTP status is
//     the failure signal), so a resolved apiPost IS success — we read
//     res.id. (Previously every successful create toasted "Create
//     failed" because res.ok was undefined.)
//   - "Use full editor" points at /sites. The legacy full-editor shell
//     (/legacy) was deleted in Phase 4 (v3.66.334); /legacy now 302s to
//     the SPA root, and per-site editing lives at /sites/<id>/settings.
//   - "Use a template instead" opens a real picker backed by
//     GET /api/templates (with ?url= suggestions). On create-with-
//     template we create, then POST /api/sites/<id>/templates/apply.
//   - Credentials route through the encrypted secrets vault. A typed
//     password is stored as a @cred: reference, never plaintext.
// v3.66.327 fixes the create contract: POST /api/sites ALWAYS creates the
// site and returns {id}; only the CREDENTIAL is contingent on the vault. If
// the vault is locked the response is 2xx with secrets_locked=true (the site
// exists, the password isn't stored yet) — we keep the wizard open, prompt to
// unlock, then PUT the password to the already-created site (never re-POST).
// A plaintext backend comes back with secrets_plaintext=true (site created,
// password not stored, advise switching backends).
//
// Behaviour preserved (FROZEN):
//   - Every field change debounces 400ms then POSTs the draft to
//     /api/sites/validate. Errors block save, warnings are advisory.
//   - Save disabled while validating OR a create mutation is in flight.
//   - role="alert" on the errors card (U4 test contract).
//   - Reset on dialog close.

export interface AddSiteWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const EMPTY_DRAFT: SiteConfigDraft = {
  name: "",
  start_url: "",
  login_url: "",
  username: "",
  cookie_file: "",
  download_dir: "",
  filename_template: "",
  user_field: "",
  pass_field: "",
  submit_btn: "",
  quality_preference: DEFAULT_SITE_QUALITY_PREFERENCE,
  min_resolution: DEFAULT_SITE_MIN_RESOLUTION,
  log_network: false,
  login_trigger: "",
};

interface SiteTemplate {
  id: string;
  name: string;
  description?: string;
  row_count?: number;
  trigger_count?: number;
  suggested?: boolean;
}
interface TemplatesResponse {
  ok: boolean;
  templates: SiteTemplate[];
}
interface CreateResult {
  id?: string;
  cred_stored?: boolean;
  cred_error?: string;
  secrets_locked?: boolean;
  secrets_plaintext?: boolean;
  auto_pick?: unknown;
}

// Advisory-only URL check for the wizard's URL fields. Non-blocking: it only
// surfaces an inline hint via FieldCard; it never gates submit (the existing
// `errors` array owns required-field gating). Empty is allowed.
function urlAdvisory(v?: string): string | undefined {
  if (!v) return undefined;
  try {
    new URL(v);
    return undefined;
  } catch {
    return "Doesn't look like a full URL (https://…)";
  }
}

export function AddSiteWizard({ open, onOpenChange }: AddSiteWizardProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<SiteConfigDraft>(EMPTY_DRAFT);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  // Template picker sub-view + the chosen template (applied after create).
  const [pickerOpen, setPickerOpen] = useState(false);
  const [template, setTemplate] = useState<{ id: string; name: string } | null>(null);
  // Vault unlock prompt. v3.66.327: POST /api/sites now ALWAYS creates the
  // site and returns {id}; if the vault is locked the credential isn't stored
  // and the response carries secrets_locked. We open this prompt, keep the
  // created id, and on unlock PUT the password to the already-created site
  // (never re-POST — that would create a duplicate).
  const [vaultPrompt, setVaultPrompt] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);

  // Reset when the dialog opens/closes so a half-typed config from a
  // previous session doesn't leak into a fresh one.
  useEffect(() => {
    if (!open) {
      setDraft(EMPTY_DRAFT);
      setValidation(null);
      setValidating(false);
      setPickerOpen(false);
      setTemplate(null);
      setVaultPrompt(false);
      setCreatedId(null);
    }
  }, [open]);

  const debounced = useDebouncedValue(draft, 400);

  useEffect(() => {
    if (!open) return;
    const policyDefaults = new Set(["quality_preference", "min_resolution", "log_network"]);
    const hasAnyValue = Object.entries(debounced).some(
      ([key, value]) =>
        !policyDefaults.has(key) && value !== undefined && value !== null &&
        String(value).trim() !== "",
    );
    if (!hasAnyValue) {
      setValidation(null);
      return;
    }
    const ac = new AbortController();
    setValidating(true);
    apiPost<ValidationResult>("/api/sites/validate", debounced, ac.signal)
      .then((res) => setValidation(res))
      .catch((err) => {
        if (err.name !== "AbortError") {
          setValidation({
            ok: true,
            errors: [],
            warnings: [`Inline validator unavailable: ${err.message}`],
          });
        }
      })
      .finally(() => setValidating(false));
    return () => ac.abort();
  }, [debounced, open]);

  // Curated template catalog, filtered/ranked by the start URL. Only
  // fetched while the picker is open.
  const templatesQ = useQuery<TemplatesResponse>({
    queryKey: ["site-templates", draft.start_url ?? ""],
    enabled: pickerOpen,
    queryFn: ({ signal }) =>
      apiGet<TemplatesResponse>(
        `/api/templates?url=${encodeURIComponent(draft.start_url ?? "")}`,
        signal,
      ),
  });

  const createMut = useMutation<CreateResult, Error, SiteConfigDraft>({
    mutationFn: (cfg) => apiPost<CreateResult>("/api/sites", cfg),
    onSuccess: async (res) => {
      // v3.66.327: a 2xx ALWAYS means the site was created (res.id present).
      // Apply the chosen template to the new site first (it exists now).
      if (template && res.id) {
        try {
          await apiPost(`/api/sites/${encodeURIComponent(res.id)}/templates/apply`, {
            template_id: template.id,
          });
        } catch (e) {
          toast.error(
            `Site created, but template "${template.name}" failed to apply: ${
              (e as Error).message
            }`,
          );
        }
      }
      // The site shows in the list regardless of the credential outcome.
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });

      if (res.secrets_locked && res.id) {
        // Site created, but the password couldn't be vaulted because the vault
        // is locked. Keep the wizard open, remember the id, and prompt to
        // unlock; on unlock we PUT the password to this existing site (we do
        // NOT re-POST — that would create a duplicate).
        setCreatedId(res.id);
        setVaultPrompt(true);
        return;
      }
      if (res.secrets_plaintext) {
        toast.error(
          "Site created, but the password was not stored: the secrets backend is plaintext. Switch to an encrypted backend in Settings → Secrets, then re-enter the password.",
        );
        onOpenChange(false);
        return;
      }
      if (res.cred_error) {
        toast.error(`Site created, but credential not stored: ${res.cred_error}`);
      } else {
        toast.success(`Site created${res.id ? ` (${res.id})` : ""}`);
      }
      onOpenChange(false);
    },
    onError: (err) => {
      // v3.66.327: create no longer refuses on a locked/plaintext vault (those
      // come back as 2xx + a flag and are handled in onSuccess). A thrown
      // error here is therefore a genuine create failure.
      toast.error(`Create failed: ${err.message}`);
    },
  });

  const errors = validation?.errors ?? [];
  const warnings = validation?.warnings ?? [];
  const canSave =
    !validating &&
    !createMut.isPending &&
    errors.length === 0 &&
    draft.name.trim() !== "";

  const setField = <K extends keyof SiteConfigDraft>(k: K, v: SiteConfigDraft[K]) =>
    setDraft((prev) => ({ ...prev, [k]: v }));

  const currentStep = useMemo<0 | 1 | 2>(() => {
    if (canSave && draft.start_url) return 2;
    const aboutDone = draft.name.trim() !== "" && (draft.start_url ?? "").trim() !== "";
    const accessTouched =
      (draft.login_url ?? "").trim() !== "" ||
      (draft.cookie_file ?? "").trim() !== "" ||
      (draft.username ?? "").trim() !== "";
    if (aboutDone && accessTouched) return 2;
    if (aboutDone) return 1;
    return 0;
  }, [draft, canSave]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-hidden p-0 flex flex-col">
        <DialogHeader className="px-6 pt-6 pb-2">
          <DialogTitle>{pickerOpen ? "Choose a template" : "Add site"}</DialogTitle>
          <DialogDescription>
            {pickerOpen
              ? "Seeds the learned download selectors and recommended defaults. Your existing selectors are preserved."
              : "Fields are validated as you type. Errors must be cleared before save."}
          </DialogDescription>
          {!pickerOpen && (
            <WorkflowSteps
              steps={["About", "Selectors", "Confirm"]}
              current={currentStep}
              ariaLabel="Wizard progress"
              className="mt-3"
            />
          )}
        </DialogHeader>

        {pickerOpen ? (
          <TemplatePicker
            loading={templatesQ.isLoading}
            error={templatesQ.error as Error | null}
            templates={templatesQ.data?.templates ?? []}
            onPick={(t) => {
              setTemplate({ id: t.id, name: t.name });
              setPickerOpen(false);
            }}
            onBack={() => setPickerOpen(false)}
          />
        ) : (
          <div className="flex-1 overflow-y-auto px-6 py-2">
            {template ? (
              <div
                className={cn(
                  "mb-3 w-full rounded-md hairline bg-primary/5 p-3",
                  "flex items-center gap-3",
                )}
              >
                <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink">
                    Template: {template.name}
                  </div>
                  <div className="text-xs text-ink-3">
                    Applied after the site is created.
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="text-xs text-ink-3 hover:text-ink hover:underline"
                  >
                    Change
                  </button>
                  <button
                    type="button"
                    onClick={() => setTemplate(null)}
                    className="text-xs text-ink-3 hover:text-ink hover:underline"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className={cn(
                  "mb-3 w-full rounded-md border border-dashed border-primary/30",
                  "bg-primary/5 p-3 text-left",
                  "flex items-center gap-3 transition-colors",
                  "hover:bg-primary/10 hover:border-primary/50",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                )}
              >
                <Sparkles className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink">
                    Use a template instead
                  </div>
                  <div className="text-xs text-ink-3">
                    Start from a curated config for common sites.
                  </div>
                </div>
                <ArrowRight className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              </button>
            )}

            <div className="space-y-3 py-1">
              <Field label="Name" required>
                <Input
                  autoFocus
                  value={draft.name}
                  onChange={(e) => setField("name", e.target.value)}
                  placeholder="e.g. ExampleSite"
                />
              </Field>
              <Field label="Start URL" error={urlAdvisory(draft.start_url)}>
                <Input
                  type="url"
                  value={draft.start_url ?? ""}
                  onChange={(e) => setField("start_url", e.target.value)}
                  placeholder="https://example.com/library"
                />
              </Field>
              <Field label="Login URL" error={urlAdvisory(draft.login_url)}>
                <Input
                  type="url"
                  value={draft.login_url ?? ""}
                  onChange={(e) => setField("login_url", e.target.value)}
                  placeholder="https://example.com/login"
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Username">
                  <Input
                    value={draft.username ?? ""}
                    onChange={(e) => setField("username", e.target.value)}
                  />
                </Field>
                <Field label="Password">
                  <SecretField
                    value={draft.password ?? ""}
                    onChange={(v) => setField("password", v)}
                    placeholder="stored in the vault"
                    ariaLabel="Site password"
                  />
                </Field>
              </div>
              <Field
                label="Cookie file"
                hint="Path to a cookies.json — leave empty if using username/password"
              >
                <Input
                  value={draft.cookie_file ?? ""}
                  onChange={(e) => setField("cookie_file", e.target.value)}
                  placeholder="/path/to/cookies.json"
                />
              </Field>
              <Field label="Download folder">
                <Input
                  value={draft.download_dir ?? ""}
                  onChange={(e) => setField("download_dir", e.target.value)}
                  placeholder="/path/to/downloads/"
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="quality_preference" hint="Highest acceptable resolution, e.g. 2160,1080,720">
                  <Input
                    aria-label="quality_preference"
                    value={draft.quality_preference ?? DEFAULT_SITE_QUALITY_PREFERENCE}
                    onChange={(e) => setField("quality_preference", e.target.value)}
                  />
                </Field>
                <Field label="min_resolution" hint="Refuse lower options during planning">
                  <Input
                    aria-label="min_resolution"
                    type="number"
                    min={0}
                    value={draft.min_resolution ?? DEFAULT_SITE_MIN_RESOLUTION}
                    onChange={(e) => setField("min_resolution", Math.max(0, Number(e.target.value) || 0))}
                  />
                </Field>
              </div>
              <label className="flex items-start gap-2 text-[12px] text-ink-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={draft.log_network ?? false}
                  onChange={(e) => setField("log_network", e.target.checked)}
                />
                <span>
                  <span className="font-medium">Record network responses</span>
                  <span className="block text-[11px] text-ink-3">
                    Enables log_network so later runs can find media URLs when markup only exposes a blob source.
                  </span>
                </span>
              </label>
              <Collapsible
                title="Advanced — login selectors"
                className="pt-1"
                headerClassName="text-xs font-medium text-ink-2"
                bodyClassName="space-y-3 pt-2"
              >
                <p className="text-[11px] text-ink-3">
                  CSS selectors for the login form. Only needed when this host
                  has no curated login template and you want the quick-add flow
                  to self-drive login. Leave empty otherwise.
                </p>
                <Field label="Username field" hint="CSS selector for the username input">
                  <Input
                    aria-label="Username field selector"
                    value={draft.user_field ?? ""}
                    onChange={(e) => setField("user_field", e.target.value)}
                    placeholder={'input[name="username"]'}
                  />
                </Field>
                <Field label="Password field" hint="CSS selector for the password input">
                  <Input
                    aria-label="Password field selector"
                    value={draft.pass_field ?? ""}
                    onChange={(e) => setField("pass_field", e.target.value)}
                    placeholder={'input[type="password"]'}
                  />
                </Field>
                <Field label="Submit button" hint="CSS selector for the login submit button">
                  <Input
                    aria-label="Submit button selector"
                    value={draft.submit_btn ?? ""}
                    onChange={(e) => setField("submit_btn", e.target.value)}
                    placeholder={'button[type="submit"]'}
                  />
                </Field>
                <Field
                  label="Login trigger"
                  hint="CSS selector for the control that reveals a hidden login form"
                >
                  <Input
                    aria-label="Login trigger selector"
                    value={draft.login_trigger ?? ""}
                    onChange={(e) => setField("login_trigger", e.target.value)}
                    placeholder={'a[data-open-login]'}
                  />
                </Field>
              </Collapsible>
            </div>

            {errors.length > 0 && (
              <div
                role="alert"
                className="mt-3 hairline rounded-md bg-red-soft p-3 text-sm text-red"
              >
                <div className="mb-1 flex items-center gap-1.5 font-semibold">
                  <AlertCircle className="h-4 w-4" aria-hidden />
                  Fix before saving
                </div>
                <ul className="ml-1 list-disc pl-4 space-y-0.5">
                  {errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
            {warnings.length > 0 && errors.length === 0 && (
              <div className="mt-3 hairline rounded-md bg-amber-soft p-3 text-sm text-amber-dim">
                <div className="mb-1 flex items-center gap-1.5 font-semibold">
                  <Info className="h-4 w-4" aria-hidden />
                  Heads up
                </div>
                <ul className="ml-1 list-disc pl-4 space-y-0.5">
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {!pickerOpen && (
          <div
            className={cn(
              "shrink-0 border-t border-hairline",
              "bg-surface/85 backdrop-blur-md",
              "flex items-center justify-between gap-2 px-6 py-3",
            )}
          >
            <a
              href="/sites"
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "inline-flex items-center gap-1 text-xs text-ink-3",
                "hover:text-ink hover:underline",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-sm",
              )}
            >
              Use full editor
              <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={createMut.isPending}
              >
                Cancel
              </Button>
              <button
                type="button"
                disabled={!canSave}
                onClick={() => createMut.mutate(draft)}
                aria-label={createMut.isPending ? "Saving" : "Add site"}
                className={cn(
                  "rounded-md bg-ink px-4 py-2",
                  "text-sm font-semibold text-surface",
                  "inline-flex items-center gap-1.5",
                  "transition-opacity hover:opacity-90 active:opacity-80",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2",
                )}
              >
                {createMut.isPending ? "Saving…" : "Add site"}
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
          </div>
        )}
      </DialogContent>

      <VaultUnlockDialog
        open={vaultPrompt}
        onOpenChange={setVaultPrompt}
        onUnlocked={async () => {
          setVaultPrompt(false);
          // The site already exists (created on the initial POST). Store the
          // password against it now that the vault is unlocked — PUT, never
          // re-POST (a re-POST would create a duplicate site).
          if (createdId && draft.password) {
            try {
              await apiPut(`/api/sites/${encodeURIComponent(createdId)}`, {
                password: draft.password,
              });
              toast.success("Credential stored.");
            } catch (e) {
              toast.error(`Couldn't store credential: ${(e as Error).message}`);
            }
          }
          qc.invalidateQueries({ queryKey: ["sites-v2"] });
          qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
          onOpenChange(false);
        }}
      />
    </Dialog>
  );
}

// Curated template picker. Suggested templates (matched to the start URL)
// float to the top with a badge.
function TemplatePicker({
  loading,
  error,
  templates,
  onPick,
  onBack,
}: {
  loading: boolean;
  error: Error | null;
  templates: SiteTemplate[];
  onPick: (t: SiteTemplate) => void;
  onBack: () => void;
}) {
  const sorted = useMemo(
    () => [...templates].sort((a, b) => Number(b.suggested) - Number(a.suggested)),
    [templates],
  );
  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 py-2">
        {loading && <div className="py-6 text-center text-sm text-ink-3">Loading templates…</div>}
        {error && (
          <div role="alert" className="hairline rounded-md bg-red-soft p-3 text-sm text-red">
            Couldn't load templates: {error.message}
          </div>
        )}
        {!loading && !error && (
          <div className="space-y-2 py-1">
            {sorted.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => onPick(t)}
                className={cn(
                  "w-full rounded-md p-3 text-left transition-colors",
                  "hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                  t.suggested
                    ? "border-2 border-primary/50"
                    : "hairline border",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-ink">{t.name}</span>
                  {t.suggested && (
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      Suggested
                    </span>
                  )}
                </div>
                {t.description && (
                  <div className="mt-0.5 text-xs text-ink-3">{t.description}</div>
                )}
                <div className="mt-1 text-[11px] text-ink-3">
                  {t.row_count ?? 0} row selectors · {t.trigger_count ?? 0} triggers
                </div>
              </button>
            ))}
            {sorted.length === 0 && (
              <div className="py-6 text-center text-sm text-ink-3">
                No templates available.
              </div>
            )}
          </div>
        )}
      </div>
      <div
        className={cn(
          "shrink-0 border-t border-hairline bg-surface/85 backdrop-blur-md",
          "flex items-center justify-between gap-2 px-6 py-3",
        )}
      >
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-xs text-ink-3 hover:text-ink hover:underline"
        >
          <ArrowLeft className="h-3 w-3" aria-hidden />
          Back
        </button>
        <button
          type="button"
          onClick={onBack}
          className="text-xs text-ink-3 hover:text-ink hover:underline"
        >
          Skip — start blank
        </button>
      </div>
    </>
  );
}

// Vault unlock prompt — shown when a create is refused because the encrypted
// secrets backend is locked. On success it calls onUnlocked (which retries
// the create so the credential actually persists).
function VaultUnlockDialog({
  open,
  onOpenChange,
  onUnlocked,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUnlocked: () => void;
}) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPassword("");
      setErr(null);
    }
  }, [open]);

  const unlockMut = useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (pw) => apiPost<{ ok: boolean }>("/api/secrets/unlock", { password: pw }),
    onSuccess: () => onUnlocked(),
    onError: (e) => setErr(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-amber-dim" aria-hidden />
            Unlock secrets vault
          </DialogTitle>
          <DialogDescription>
            The encrypted backend is locked, so this password can't be stored.
            Unlock to save it — the credential isn't written until the vault is
            open.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <SecretField
            autoFocus
            value={password}
            onChange={(v) => {
              setPassword(v);
              setErr(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && password) unlockMut.mutate(password);
            }}
            placeholder="Vault master password"
            ariaLabel="Vault master password"
          />
          {err && (
            <div role="alert" className="hairline rounded-md bg-red-soft p-2 text-xs text-red">
              {err}
            </div>
          )}
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!password || unlockMut.isPending}
              onClick={() => unlockMut.mutate(password)}
            >
              {unlockMut.isPending ? "Unlocking…" : "Unlock & save"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
